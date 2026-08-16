# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio
import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        public_url="http://testserver",
        public_origin="http://testserver",
        secure_cookie=False,
        source_url="https://example.org/source",
        ollama_url="http://127.0.0.1:11434",
        searxng_url="http://127.0.0.1:8888",
        live_tools_url="http://127.0.0.1:8090",
        default_model="recommended:latest",
        preferred_models=("second:latest",),
        session_ttl_seconds=3600,
        fetch_max_bytes=65_536,
        tool_result_max_chars=4_000,
        chat_concurrency=1,
        chat_pending=1,
        chat_queue_timeout_seconds=1,
        chat_deadline_seconds=60,
        fetch_deadline_seconds=5,
        database_concurrency=2,
        ollama_context_length=4_096,
    )


def test_login_session_csrf_models_and_chat(tmp_path, monkeypatch) -> None:
    async def fake_models(_client, _settings):
        return {
            "models": [
                {
                    "id": "recommended:latest",
                    "name": "recommended:latest",
                    "size": 123,
                    "parameter_size": "7B",
                    "quantization": "Q4_K_M",
                    "recommended": True,
                }
            ],
            "default_model": "recommended:latest",
        }

    async def fake_chat(_client, _settings, model, messages, emit, reasoning=False):
        assert messages[-1] == {"role": "user", "content": "hello"}
        await emit("status", {"message": "Testing"})
        # run_chat now streams visible text as the model produces it; main.py no
        # longer re-chunks a finished answer.
        await emit("delta", {"content": "Local "})
        await emit("delta", {"content": "answer"})
        return "Local answer", [{"title": "Example", "url": "https://example.test/"}]

    monkeypatch.setattr("app.main.installed_models", fake_models)
    monkeypatch.setattr("app.main.run_chat", fake_chat)
    async def create_user(database):
        from app.security import hash_password

        await database.initialize()
        await database.bootstrap_user(
            "owner@example.test",
            "Local Owner",
            await hash_password("a-secure-test-password"),
        )

    from app.database import Database
    asyncio.run(create_user(Database(tmp_path, 3600, 2)))
    application = create_app(_settings(tmp_path))

    with TestClient(application) as client:
        health = client.get("/health")
        assert health.status_code == 200
        assert health.headers["x-request-id"]
        assert client.get("/api/auth/session").json() == {"authenticated": False}
        assert client.get("/", follow_redirects=False).headers["location"] == "/login"
        assert client.get("/api/models").status_code == 401
        assert client.post(
            "/api/auth/login",
            headers={"Origin": "https://attacker.invalid"},
            json={"email": "owner@example.test", "password": "a-secure-test-password"},
        ).status_code == 403
        assert client.post(
            "/api/auth/login",
            json={"email": "owner@example.test", "password": "wrong"},
        ).status_code == 401

        login = client.post(
            "/api/auth/login",
            json={
                "email": "OWNER@example.test",
                "password": "a-secure-test-password",
            },
        )
        assert login.status_code == 200
        assert login.json()["user"] == {
            "name": "Local Owner",
            "email": "owner@example.test",
        }
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=lax" in login.headers["set-cookie"]
        assert "Secure" not in login.headers["set-cookie"]
        csrf = login.json()["csrf_token"]

        session = client.get("/api/auth/session").json()
        assert session["authenticated"] is True
        assert session["csrf_token"] == csrf
        assert client.get("/api/models").json()["default_model"] == "recommended:latest"

        admission = application.state.chat_admission
        application.state.chat_admission = asyncio.Semaphore(0)
        full = client.post(
            "/api/chat",
            headers={"X-CSRF-Token": csrf},
            json={
                "model": "recommended:latest",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert full.status_code == 503
        application.state.chat_admission = admission

        class FakeTask:
            cancelled = False

            def cancel(self):
                self.cancelled = True

        active_task = FakeTask()
        application.state.active_chats[user_id := 1] = {active_task}

        denied = client.post(
            "/api/chat",
            json={
                "model": "recommended:latest",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert denied.status_code == 403

        with client.stream(
            "POST",
            "/api/chat",
            headers={"X-CSRF-Token": csrf},
            json={
                "model": "recommended:latest",
                "messages": [{"role": "user", "content": "hello"}],
            },
        ) as response:
            stream = "".join(response.iter_text())
        assert response.status_code == 200
        assert "event: status" in stream
        assert 'event: delta\ndata: {"content":"Local "}' in stream
        assert 'event: delta\ndata: {"content":"answer"}' in stream
        assert "event: done" in stream
        # "done" carries the authoritative answer so the client can reconcile
        # its live preview against post-processed text.
        assert '"content":"Local answer"' in stream

        assert client.post("/api/auth/logout").status_code == 403
        assert client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": csrf},
            json={"current_password": "wrong", "new_password": "new-secure-password"},
        ).status_code == 400
        assert client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": csrf},
            json={
                "current_password": "a-secure-test-password",
                "new_password": "new-secure-password",
            },
        ).status_code == 204
        assert active_task.cancelled
        application.state.active_chats.pop(user_id, None)
        assert client.get("/api/auth/session").json() == {"authenticated": False}

        assert client.post(
            "/api/auth/login",
            json={"email": "owner@example.test", "password": "a-secure-test-password"},
        ).status_code == 401
        second_login = client.post(
            "/api/auth/login",
            json={"email": "owner@example.test", "password": "new-secure-password"},
        )
        assert second_login.status_code == 200
        second_csrf = second_login.json()["csrf_token"]
        assert client.post(
            "/api/auth/logout", headers={"X-CSRF-Token": second_csrf}
        ).status_code == 204

    with sqlite3.connect(tmp_path / "assistant.sqlite3") as database:
        password_hash = database.execute("SELECT password_hash FROM users").fetchone()[0]
        assert password_hash.startswith("$argon2id$")
        assert "a-secure-test-password" not in password_hash
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_seed_happens_only_when_users_table_is_empty(tmp_path, monkeypatch) -> None:
    from app.database import Database
    from app.security import hash_password
    async def seed_twice():
        database = Database(tmp_path, 3600, 2)
        await database.initialize()
        assert await database.bootstrap_user(
            "first@example.test", "First", await hash_password("first-secure-password")
        )
        assert not await database.bootstrap_user(
            "replacement@example.test",
            "Replacement",
            await hash_password("replacement-password"),
        )

    asyncio.run(seed_twice())
    with TestClient(create_app(_settings(tmp_path))):
        pass

    with sqlite3.connect(tmp_path / "assistant.sqlite3") as database:
        assert database.execute("SELECT email, name FROM users").fetchall() == [
            ("first@example.test", "First")
        ]


def test_normal_startup_refuses_an_empty_database(tmp_path) -> None:
    import pytest

    with pytest.raises(RuntimeError, match="app.bootstrap"):
        with TestClient(create_app(_settings(tmp_path))):
            pass


def test_chunked_oversize_body_is_rejected_before_json_parse(tmp_path) -> None:
    from app.database import Database
    from app.main import MAX_REQUEST_BYTES
    from app.security import hash_password

    async def seed():
        database = Database(tmp_path, 3600, 2)
        await database.initialize()
        await database.bootstrap_user(
            "owner@example.test", "Owner", await hash_password("secure-test-password")
        )

    asyncio.run(seed())
    with TestClient(create_app(_settings(tmp_path))) as client:
        def chunks():
            yield b"x" * (MAX_REQUEST_BYTES // 2)
            yield b"x" * (MAX_REQUEST_BYTES // 2)
            yield b"x"

        response = client.post(
            "/api/auth/login",
            content=chunks(),
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 413
        assert response.json()["detail"] == "Request body is too large"
        assert response.headers["x-request-id"]
