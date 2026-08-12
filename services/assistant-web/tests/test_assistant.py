# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio

import httpx

from app.assistant import MAX_TOOL_CALLS, run_chat
from app.config import Settings
from app.tools import ToolExecution


def test_model_cannot_amplify_tool_calls(monkeypatch, tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path,
        public_url="http://localhost:3000",
        public_origin="http://localhost:3000",
        secure_cookie=False,
        source_url="https://example.org/source",
        ollama_url="http://127.0.0.1:11434",
        searxng_url="http://127.0.0.1:8888",
        live_tools_url="http://127.0.0.1:8090",
        default_model="test:latest",
        preferred_models=(),
        session_ttl_seconds=3600,
        fetch_max_bytes=65_536,
        tool_result_max_chars=4_000,
        chat_concurrency=1,
        chat_pending=0,
        chat_queue_timeout_seconds=1,
        chat_deadline_seconds=60,
        fetch_deadline_seconds=5,
        database_concurrency=2,
        ollama_context_length=4_096,
    )
    calls = 0
    model_requests: list[list[dict]] = []

    async def fake_chat_once(_client, _settings, _model, messages, *, include_tools):
        model_requests.append(messages.copy())
        if len(model_requests) == 1:
            return {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "web_search", "arguments": {"query": "x"}}}
                    for _ in range(1_000)
                ],
            }
        return {"content": "bounded answer"}

    async def fake_tool_run(_self, _name, _arguments):
        nonlocal calls
        calls += 1
        return ToolExecution("{}")

    monkeypatch.setattr("app.assistant._chat_once", fake_chat_once)
    monkeypatch.setattr("app.assistant.ToolRunner.run", fake_tool_run)

    async def run():
        async with httpx.AsyncClient() as client:
            return await run_chat(
                client,
                settings,
                "test:latest",
                [{"role": "user", "content": "test"}],
                lambda _event, _payload: asyncio.sleep(0),
            )

    content, _sources = asyncio.run(run())
    assert content == "bounded answer"
    assert calls == MAX_TOOL_CALLS
    assistant_messages = [
        message for message in model_requests[1] if message.get("role") == "assistant"
    ]
    assert len(assistant_messages[-1]["tool_calls"]) == MAX_TOOL_CALLS
