# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from app.config import Settings
from app.database import Database
from app.main import ManagedStreamingResponse, RequestBodyLimitMiddleware, create_app
from app.openai_gateway import GatewayRequestError, validate_chat_body
from app.security import hash_password, verify_password


PASSWORD = "a-secure-test-password"


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        public_url="http://testserver",
        public_origin="http://testserver",
        secure_cookie=False,
        source_url="https://example.org/source",
        inference_base_url="http://127.0.0.1:8080",
        searxng_url="http://127.0.0.1:8888",
        live_tools_url="http://127.0.0.1:8090",
        default_model="kevinbellm-27b",
        preferred_models=(),
        session_ttl_seconds=3600,
        fetch_max_bytes=65_536,
        tool_result_max_chars=4_000,
        chat_concurrency=1,
        chat_pending=1,
        chat_queue_timeout_seconds=1,
        chat_deadline_seconds=60,
        fetch_deadline_seconds=5,
        database_concurrency=2,
        zoo_api_base_url="https://api.example.test/v1",
        api_token_ttl_seconds=30 * 24 * 3600,
        zoo_max_output_tokens=8_192,
        zoo_context_window=32_768,
    )


def _seed(tmp_path) -> None:
    async def seed() -> None:
        database = Database(tmp_path, 3600, 2)
        await database.initialize()
        await database.bootstrap_user(
            "owner@example.test", "Local Owner", await hash_password(PASSWORD)
        )

    asyncio.run(seed())


def _fake_catalog():
    async def fake_models(_client, _settings):
        return {
            "models": [
                {
                    "id": "kevinbellm-27b",
                    "name": "kevinbellm-27b",
                    "size": None,
                    "parameter_size": "27B",
                    "quantization": "IQ4_XS",
                    "recommended": True,
                }
            ],
            "default_model": "kevinbellm-27b",
        }

    return fake_models


def _login_and_create_token(client: TestClient, name: str = "Work laptop") -> tuple[str, str, int]:
    login = client.post(
        "/api/auth/login",
        json={"email": "owner@example.test", "password": PASSWORD},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    created = client.post(
        "/api/auth/api-tokens",
        headers={"X-CSRF-Token": csrf},
        json={"name": name, "current_password": PASSWORD},
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    return created.json()["token"], csrf, created.json()["credential"]["id"]


def test_token_management_requires_login_password_and_csrf(tmp_path) -> None:
    _seed(tmp_path)
    with TestClient(create_app(_settings(tmp_path))) as client:
        assert client.get("/api/auth/api-tokens").status_code == 401
        login = client.post(
            "/api/auth/login",
            json={"email": "owner@example.test", "password": PASSWORD},
        )
        csrf = login.json()["csrf_token"]
        assert client.post(
            "/api/auth/api-tokens",
            json={"name": "Laptop", "current_password": PASSWORD},
        ).status_code == 403
        assert client.post(
            "/api/auth/api-tokens",
            headers={"X-CSRF-Token": csrf},
            json={"name": "Laptop", "current_password": "incorrect-password"},
        ).status_code == 401

        token, _csrf, token_id = _login_and_create_token(client)
        assert token.startswith("kbm_v1_")
        listing = client.get("/api/auth/api-tokens")
        assert listing.status_code == 200
        body = listing.json()
        assert body["setup"] == {
            "base_url": "https://api.example.test/v1",
            "model": "kevinbellm-27b",
            "context_window": 32_768,
            "max_output_tokens": 8_192,
            "token_ttl_days": 30,
        }
        assert body["tokens"][0]["id"] == token_id
        assert token not in listing.text

    with sqlite3.connect(tmp_path / "assistant.sqlite3") as database:
        stored = database.execute(
            "SELECT token_hash, name FROM api_tokens WHERE id = ?", (token_id,)
        ).fetchone()
    assert stored is not None
    assert stored[1] == "Work laptop"
    assert token.encode() not in stored[0]


def test_bearer_auth_models_revocation_and_expiry(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    with TestClient(create_app(_settings(tmp_path))) as client:
        token, csrf, token_id = _login_and_create_token(client)

        # The browser cookie is deliberately not an API credential.
        missing = client.get("/v1/models")
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == 'Bearer realm="KevinBeLLM"'
        assert client.get(
            "/v1/models", headers={"Authorization": "Bearer malformed"}
        ).status_code == 401
        assert client.get(
            "/v1/models",
            headers=[
                ("Authorization", f"Bearer {token}"),
                ("Authorization", f"Bearer {token}"),
            ],
        ).status_code == 401

        models = client.get(
            "/v1/models", headers={"Authorization": f"Bearer {token}"}
        )
        assert models.status_code == 200
        assert models.headers["cache-control"] == "no-store"
        assert models.json() == {
            "object": "list",
            "data": [
                {
                    "id": "kevinbellm-27b",
                    "object": "model",
                    "created": 0,
                    "owned_by": "kevinbellm",
                }
            ],
        }

        revoked = client.delete(
            f"/api/auth/api-tokens/{token_id}", headers={"X-CSRF-Token": csrf}
        )
        assert revoked.status_code == 204
        assert client.get(
            "/v1/models", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401

        expired, _csrf, expired_id = _login_and_create_token(client, "Expired")
        with sqlite3.connect(tmp_path / "assistant.sqlite3") as database:
            database.execute(
                "UPDATE api_tokens SET expires_at = 1 WHERE id = ?", (expired_id,)
            )
            database.commit()
        assert client.get(
            "/v1/models", headers={"Authorization": f"Bearer {expired}"}
        ).status_code == 401


class _Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


class _SlowChunks(httpx.AsyncByteStream):
    def __init__(self, delay: float, chunk: bytes):
        self.delay = delay
        self.chunk = chunk
        self.closed = False

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield self.chunk

    async def aclose(self) -> None:
        self.closed = True


class _RaisingCloseChunks(_Chunks):
    def __init__(self, chunks: list[bytes]):
        super().__init__(chunks)
        self.close_attempted = False

    async def aclose(self) -> None:
        self.close_attempted = True
        raise RuntimeError("simulated transport close failure")


class _ReadErrorChunks(httpx.AsyncByteStream):
    def __init__(self):
        self.closed = False

    async def __aiter__(self):
        raise httpx.ReadError("simulated upstream read failure")
        yield b""  # pragma: no cover - keeps this an async generator

    async def aclose(self) -> None:
        self.closed = True


def test_unauthenticated_chat_is_rejected_before_body_is_read() -> None:
    class RejectingDatabase:
        async def user_for_api_token(self, _token: str, *, touch: bool = True):
            return None

    body_was_read = False
    application_was_called = False
    sent: list[dict[str, object]] = []

    async def application(_scope, _receive, _send) -> None:
        nonlocal application_was_called
        application_was_called = True

    async def receive():
        nonlocal body_was_read
        body_was_read = True
        raise AssertionError("an unauthenticated body must not be read")

    async def send(message) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [],
        "client": ("192.0.2.10", 12345),
        "server": ("assistant.example.test", 443),
    }
    middleware = RequestBodyLimitMiddleware(
        application,
        maximum=1024,
        api_database=RejectingDatabase(),  # type: ignore[arg-type]
    )
    asyncio.run(middleware(scope, receive, send))  # type: ignore[arg-type]

    assert body_was_read is False
    assert application_was_called is False
    assert sent[0]["status"] == 401
    assert b'"code": "invalid_api_key"' in sent[1]["body"]


def test_managed_stream_runs_cleanup_when_response_start_fails() -> None:
    cleanup_called = False
    iterator_started = False

    async def content():
        nonlocal iterator_started
        iterator_started = True
        yield b"data: [DONE]\n\n"

    async def cleanup() -> None:
        nonlocal cleanup_called
        cleanup_called = True

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message) -> None:
        if message["type"] == "http.response.start":
            raise ConnectionError("simulated disconnect before body iteration")

    response = ManagedStreamingResponse(content(), cleanup=cleanup)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/v1/chat/completions",
        "raw_path": b"/v1/chat/completions",
        "query_string": b"",
        "headers": [],
        "client": ("192.0.2.10", 12345),
        "server": ("assistant.example.test", 443),
    }

    async def invoke() -> None:
        with pytest.raises(ClientDisconnect):
            await response(scope, receive, send)  # type: ignore[arg-type]

    asyncio.run(invoke())
    assert cleanup_called is True
    assert iterator_started is False


def test_zoo_streamed_native_tool_calls_round_trip(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    captured: dict[str, object] = {}
    sse = (
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,'
        '"id":"call_1","type":"function","function":{"name":"read_file",'
        '"arguments":"{\\"path\\":\\"README.md\\"}"}}]},"finish_reason":null}]}\n\n'
        'data: {"id":"chatcmpl-1","object":"chat.completion.chunk","choices":'
        '[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()

    async def fake_upstream(_client, _settings, body):
        captured["body"] = body
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream; charset=utf-8"},
            stream=_Chunks([sse[:97], sse[97:]]),
        )

    monkeypatch.setattr("app.main.open_upstream_chat", fake_upstream)
    with TestClient(create_app(_settings(tmp_path))) as client:
        token, _csrf, _token_id = _login_and_create_token(client)
        request_body = {
            "model": "kevinbellm-27b",
            "messages": [{"role": "user", "content": "Read the README"}],
            "stream": True,
            "stream_options": {"include_usage": True},
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read one file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
            "parallel_tool_calls": True,
        }
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json=request_body,
        ) as response:
            output = b"".join(response.iter_bytes())
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert output == sse
        sent = captured["body"]
        assert isinstance(sent, dict)
        assert sent["tools"] == request_body["tools"]
        assert sent["tool_choice"] == "auto"
        assert sent["parallel_tool_calls"] is True
        assert sent["parse_tool_calls"] is True
        assert sent["max_tokens"] == 8_192
        assert "backend_sampling" not in sent


def test_zoo_uses_backend_sampling_only_for_unconstrained_text(tmp_path) -> None:
    settings = _settings(tmp_path)
    base = {
        "model": "kevinbellm-27b",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    plain = validate_chat_body(base, settings)
    assert plain["backend_sampling"] is True

    tool = {
        "type": "function",
        "function": {
            "name": "lookup",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    constrained_by_tools = validate_chat_body(
        {**base, "tools": [tool], "tool_choice": "auto"}, settings
    )
    assert "backend_sampling" not in constrained_by_tools

    tools_explicitly_disabled = validate_chat_body(
        {
            **base,
            "tools": [tool],
            "tool_choice": "none",
            "parallel_tool_calls": True,
        },
        settings,
    )
    assert tools_explicitly_disabled["backend_sampling"] is True
    assert "tools" not in tools_explicitly_disabled
    assert "parallel_tool_calls" not in tools_explicitly_disabled

    constrained_by_json = validate_chat_body(
        {**base, "response_format": {"type": "json_object"}}, settings
    )
    assert "backend_sampling" not in constrained_by_json


def test_zoo_thinking_is_opt_in_and_level_independent(tmp_path) -> None:
    settings = _settings(tmp_path)
    base = {
        "model": "kevinbellm-27b",
        "messages": [{"role": "user", "content": "Hello"}],
    }

    # Default deployment: thinking stays off and is forced off upstream.
    assert settings.zoo_enable_thinking is False
    off = validate_chat_body(base, settings)
    assert off["reasoning_effort"] == "none"
    assert off["chat_template_kwargs"] == {"enable_thinking": False}
    with pytest.raises(GatewayRequestError) as rejected:
        validate_chat_body({**base, "reasoning_effort": "low"}, settings)
    assert rejected.value.param == "reasoning_effort"

    enabled = replace(settings, zoo_enable_thinking=True)

    # An absent field still means off, so existing clients are unaffected.
    still_off = validate_chat_body(base, enabled)
    assert still_off["reasoning_effort"] == "none"
    assert still_off["chat_template_kwargs"] == {"enable_thinking": False}
    assert validate_chat_body({**base, "reasoning_effort": "none"}, enabled) == still_off

    # Qwen3.8 has no graded scale, so every level is the same request upstream
    # and the level itself is never forwarded.
    for level in ("minimal", "low", "medium", "high", "max"):
        on = validate_chat_body({**base, "reasoning_effort": level}, enabled)
        assert "reasoning_effort" not in on
        assert "chat_template_kwargs" not in on
        assert on == validate_chat_body({**base, "reasoning_effort": "high"}, enabled)

    with pytest.raises(GatewayRequestError) as invalid:
        validate_chat_body({**base, "reasoning_effort": "x" * 33}, enabled)
    assert invalid.value.param == "reasoning_effort"

    with pytest.raises(GatewayRequestError) as wrong_type:
        validate_chat_body({**base, "reasoning_effort": 3}, enabled)
    assert wrong_type.value.param == "reasoning_effort"


def test_stream_close_failure_still_releases_every_slot(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    stream = _RaisingCloseChunks([b"data: [DONE]\n\n"])

    async def fake_upstream(_client, _settings, _body):
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=stream,
        )

    monkeypatch.setattr("app.main.open_upstream_chat", fake_upstream)
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        token, _csrf, _token_id = _login_and_create_token(client)
        with client.stream(
            "POST",
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "Hello"}],
                "stream": True,
            },
        ) as response:
            assert b"".join(response.iter_bytes()) == b"data: [DONE]\n\n"

        assert response.status_code == 200
        assert stream.close_attempted is True
        assert application.state.chat_slots._value == 1
        assert application.state.chat_admission._value == 2
        assert application.state.api_body_slots._value == 2
        assert application.state.active_chats == {}


def test_nonstream_tool_result_and_request_controls(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    captured: dict[str, object] = {}
    completion = {
        "id": "chatcmpl-2",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Done"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 1, "total_tokens": 21},
    }

    async def fake_upstream(_client, _settings, body):
        captured["body"] = body
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            content=json.dumps(completion).encode(),
        )

    monkeypatch.setattr("app.main.open_upstream_chat", fake_upstream)
    with TestClient(create_app(_settings(tmp_path))) as client:
        token, _csrf, _token_id = _login_and_create_token(client)
        headers = {"Authorization": f"Bearer {token}"}
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "kevinbellm-27b",
                "messages": [
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path":"README.md"}',
                                },
                            }
                        ],
                    },
                    {"role": "tool", "tool_call_id": "call_1", "content": "contents"},
                ],
                "stream": False,
                "max_completion_tokens": 1024,
            },
        )
        assert response.status_code == 200
        assert response.json() == completion
        sent = captured["body"]
        assert isinstance(sent, dict)
        assert sent["max_tokens"] == 1024
        assert "max_completion_tokens" not in sent
        assert sent["backend_sampling"] is True

        assert client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "hello"}],
                "backend_sampling": True,
            },
        ).status_code == 400
        too_many = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 8_193,
            },
        )
        assert too_many.status_code == 400
        assert too_many.json()["error"]["param"] == "max_tokens"

        reasoning = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "hello"}],
                "reasoning_effort": "low",
            },
        )
        assert reasoning.status_code == 400
        assert reasoning.json()["error"]["param"] == "reasoning_effort"


def test_aggregate_prompt_budget_is_enforced_before_inference(
    tmp_path, monkeypatch
) -> None:
    _seed(tmp_path)
    upstream_called = False

    async def unexpected_upstream(_client, _settings, _body):
        nonlocal upstream_called
        upstream_called = True
        raise AssertionError("an oversized prompt must not reach inference")

    monkeypatch.setattr("app.main.open_upstream_chat", unexpected_upstream)
    settings = replace(
        _settings(tmp_path), zoo_context_window=4_096, zoo_max_output_tokens=1_024
    )
    with TestClient(create_app(settings)) as client:
        token, _csrf, _token_id = _login_and_create_token(client)
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "x" * 25_000}],
            },
        )

    assert response.status_code == 400
    assert response.json()["error"]["param"] == "messages"
    assert upstream_called is False


def test_nonstream_deadline_closes_upstream_and_releases_every_slot(
    tmp_path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    slow_stream = _SlowChunks(
        0.1,
        b'{"id":"late","object":"chat.completion","choices":[]}',
    )

    async def slow_upstream(_client, _settings, _body):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=slow_stream,
        )

    monkeypatch.setattr("app.main.open_upstream_chat", slow_upstream)
    settings = replace(_settings(tmp_path), chat_deadline_seconds=0.05)
    application = create_app(settings)
    with TestClient(application) as client:
        token, _csrf, _token_id = _login_and_create_token(client)
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "Wait"}],
                "stream": False,
            },
        )

        assert response.status_code == 504
        assert response.json()["error"]["code"] == "request_timeout"
        assert slow_stream.closed is True
        assert application.state.chat_slots._value == 1
        assert application.state.chat_admission._value == 2
        assert application.state.api_body_slots._value == 2


def test_nonstream_read_failure_uses_openai_error_and_releases_slots(
    tmp_path, monkeypatch
) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    failed_stream = _ReadErrorChunks()

    async def failing_upstream(_client, _settings, _body):
        return httpx.Response(
            200,
            headers={"Content-Type": "application/json"},
            stream=failed_stream,
        )

    monkeypatch.setattr("app.main.open_upstream_chat", failing_upstream)
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        token, _csrf, _token_id = _login_and_create_token(client)
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "model": "kevinbellm-27b",
                "messages": [{"role": "user", "content": "Hello"}],
            },
        )

        assert response.status_code == 502
        assert response.json()["error"]["code"] == "invalid_upstream_response"
        assert failed_stream.closed is True
        assert application.state.chat_slots._value == 1
        assert application.state.chat_admission._value == 2
        assert application.state.api_body_slots._value == 2


def test_password_change_revokes_zoo_token(tmp_path, monkeypatch) -> None:
    _seed(tmp_path)
    monkeypatch.setattr("app.main.installed_models", _fake_catalog())
    with TestClient(create_app(_settings(tmp_path))) as client:
        token, csrf, _token_id = _login_and_create_token(client)
        changed = client.post(
            "/api/auth/change-password",
            headers={"X-CSRF-Token": csrf},
            json={
                "current_password": PASSWORD,
                "new_password": "a-different-secure-password",
            },
        )
        assert changed.status_code == 204
        assert client.get(
            "/v1/models", headers={"Authorization": f"Bearer {token}"}
        ).status_code == 401


def test_password_change_during_token_creation_cannot_mint_token(
    tmp_path, monkeypatch
) -> None:
    _seed(tmp_path)
    application = create_app(_settings(tmp_path))
    with TestClient(application) as client:
        login = client.post(
            "/api/auth/login",
            json={"email": "owner@example.test", "password": PASSWORD},
        )
        assert login.status_code == 200
        csrf = login.json()["csrf_token"]

        async def change_password_after_verification(encoded: str | None, password: str) -> bool:
            accepted = await verify_password(encoded, password)
            if accepted:
                replacement = await hash_password("a-raced-password-change")
                await application.state.database.change_password(1, replacement)
            return accepted

        monkeypatch.setattr(
            "app.main.verify_password", change_password_after_verification
        )
        created = client.post(
            "/api/auth/api-tokens",
            headers={"X-CSRF-Token": csrf},
            json={"name": "Raced client", "current_password": PASSWORD},
        )
        assert created.status_code == 401

    with sqlite3.connect(tmp_path / "assistant.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM api_tokens").fetchone()[0] == 0
        assert database.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
