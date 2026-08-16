# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from app.assistant import MAX_TOOL_CALLS, installed_models, run_chat
from app.config import Settings
from app.tools import ToolExecution


def _settings(tmp_path, *, backend: str = "ollama") -> Settings:
    base_url = (
        "http://127.0.0.1:11434"
        if backend == "ollama"
        else "http://127.0.0.1:8080"
    )
    return Settings(
        data_dir=tmp_path,
        public_url="http://localhost:3000",
        public_origin="http://localhost:3000",
        secure_cookie=False,
        source_url="https://example.org/source",
        ollama_url=base_url,
        searxng_url="http://127.0.0.1:8888",
        live_tools_url="http://127.0.0.1:8090",
        default_model="test-model",
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
        inference_backend=backend,
        inference_base_url=base_url,
    )


def test_model_cannot_amplify_tool_calls(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    calls = 0
    model_requests: list[list[dict]] = []

    async def fake_chat_once(
        _client, _settings, _model, messages, *, include_tools,
        on_delta=None, on_reasoning=None, reasoning=False,
    ):
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
                "test-model",
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


def test_llamacpp_model_discovery_is_normalized(tmp_path) -> None:
    settings = _settings(tmp_path, backend="llamacpp")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url == "http://127.0.0.1:8080/v1/models"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {
                        "id": "test-model",
                        "object": "model",
                        "owned_by": "llamacpp",
                        "meta": {
                            "size": 11_234_567_890,
                            "n_params": 20_000_000_000,
                            "quantization": "Q4_K_M",
                        },
                    }
                ],
            },
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await installed_models(client, settings)

    assert asyncio.run(run()) == {
        "models": [
            {
                "id": "test-model",
                "name": "test-model",
                "size": 11_234_567_890,
                "parameter_size": "20B",
                "quantization": "Q4_K_M",
                "recommended": True,
            }
        ],
        "default_model": "test-model",
    }


@pytest.mark.parametrize(
    ("configured_default", "advertised_model"),
    [
        ("kevinbellm-27b", "kevinbellm-9b"),
        ("kevinbellm-9b", "kevinbellm-27b"),
    ],
)
def test_llamacpp_model_discovery_follows_active_service_profile(
    tmp_path, configured_default: str, advertised_model: str
) -> None:
    settings = replace(
        _settings(tmp_path, backend="llamacpp"),
        default_model=configured_default,
        preferred_models=("kevinbellm-9b", "kevinbellm-27b"),
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"id": advertised_model, "meta": {"n_params": 9_000_000_000}}]},
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await installed_models(client, settings)

    result = asyncio.run(run())
    assert result["default_model"] == advertised_model
    assert result["models"][0]["recommended"] is True


def test_llamacpp_chat_completion_is_normalized(tmp_path) -> None:
    settings = _settings(tmp_path, backend="llamacpp")
    requests: list[dict] = []

    def _sse(*chunks: dict) -> bytes:
        lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
        lines.append("data: [DONE]\n\n")
        return "".join(lines).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://127.0.0.1:8080/v1/chat/completions"
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_sse(
                {"choices": [{"index": 0, "delta": {"role": "assistant"}}]},
                {"choices": [{"index": 0, "delta": {"content": "Local "}}]},
                {"choices": [{"index": 0, "delta": {"content": "answer"}}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
            ),
        )

    events: list[tuple[str, dict]] = []

    async def record(event: str, payload: dict) -> None:
        events.append((event, payload))

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await run_chat(
                client,
                settings,
                "test-model",
                [{"role": "user", "content": "hello"}],
                record,
            )

    content, sources = asyncio.run(run())
    assert (content, sources) == ("Local answer", [])
    # The answer must reach the browser incrementally, not as one block after
    # the whole generation finished.
    assert [payload["content"] for name, payload in events if name == "delta"] == [
        "Local ",
        "answer",
    ]
    assert len(requests) == 1
    assert requests[0]["model"] == "test-model"
    assert requests[0]["stream"] is True
    assert requests[0]["max_tokens"] == 2_048
    assert requests[0]["temperature"] == 0.3
    assert requests[0]["reasoning_effort"] == "none"
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[0]["parse_tool_calls"] is True
    assert requests[0]["tool_choice"] == "auto"
    assert requests[0]["messages"][-1] == {"role": "user", "content": "hello"}
    assert isinstance(requests[0]["tools"], list)
    assert "options" not in requests[0]
    assert "think" not in requests[0]


@pytest.mark.parametrize("upstream_call_id", ["call-from-server", None])
def test_llamacpp_tool_call_round_trip(
    monkeypatch, tmp_path, upstream_call_id: str | None
) -> None:
    settings = _settings(tmp_path, backend="llamacpp")
    requests: list[dict] = []

    def _sse(*chunks: dict) -> bytes:
        lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
        lines.append("data: [DONE]\n\n")
        return "".join(lines).encode()

    def _delta(fragment: dict) -> dict:
        return {"choices": [{"index": 0, "delta": fragment}]}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        headers = {"Content-Type": "text/event-stream"}
        if len(requests) == 1:
            opening: dict = {
                "index": 0,
                "type": "function",
                "function": {"name": "web_search", "arguments": ""},
            }
            if upstream_call_id is not None:
                opening["id"] = upstream_call_id
            # Real servers split the JSON arguments across many chunks, so the
            # accumulator must rebuild them in order.
            return httpx.Response(
                200,
                headers=headers,
                content=_sse(
                    _delta({"role": "assistant"}),
                    _delta({"tool_calls": [opening]}),
                    _delta(
                        {
                            "tool_calls": [
                                {"index": 0, "function": {"arguments": '{"query":'}}
                            ]
                        }
                    ),
                    _delta(
                        {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"local inference"}'},
                                }
                            ]
                        }
                    ),
                ),
            )
        return httpx.Response(
            200,
            headers=headers,
            content=_sse(
                _delta({"role": "assistant"}),
                _delta({"content": "Grounded local answer"}),
            ),
        )

    async def fake_tool_run(_self, name, arguments):
        assert name == "web_search"
        assert arguments == {"query": "local inference"}
        return ToolExecution('{"results":[]}')

    monkeypatch.setattr("app.assistant.ToolRunner.run", fake_tool_run)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await run_chat(
                client,
                settings,
                "test-model",
                [{"role": "user", "content": "research this"}],
                lambda _event, _payload: asyncio.sleep(0),
            )

    content, sources = asyncio.run(run())
    assert (content, sources) == ("Grounded local answer", [])
    assert len(requests) == 2
    assistant_message = requests[1]["messages"][-2]
    tool_message = requests[1]["messages"][-1]
    sent_call = assistant_message["tool_calls"][0]
    assert sent_call["type"] == "function"
    assert sent_call["function"] == {
        "name": "web_search",
        "arguments": '{"query":"local inference"}',
    }
    expected_id = upstream_call_id or "call_2_0"
    assert sent_call["id"] == expected_id
    assert tool_message == {
        "role": "tool",
        "content": '{"results":[]}',
        "tool_call_id": expected_id,
    }


def test_reasoning_is_opt_in_and_never_enters_the_answer(tmp_path) -> None:
    """Fast mode suppresses thinking; reasoning mode streams it on its own channel."""
    settings = _settings(tmp_path, backend="llamacpp")
    requests: list[dict] = []

    def _sse(*chunks: dict) -> bytes:
        lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
        lines.append("data: [DONE]\n\n")
        return "".join(lines).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content=_sse(
                {"choices": [{"index": 0, "delta": {"reasoning_content": "weighing "}}]},
                {"choices": [{"index": 0, "delta": {"reasoning_content": "options"}}]},
                {"choices": [{"index": 0, "delta": {"content": "Paris"}}]},
            ),
        )

    def run(reasoning: bool):
        events: list[tuple[str, dict]] = []

        async def record(event: str, payload: dict) -> None:
            events.append((event, payload))

        async def go():
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                return await run_chat(
                    client, settings, "test-model",
                    [{"role": "user", "content": "capital of France?"}],
                    record, reasoning,
                )

        content, _sources = asyncio.run(go())
        return content, events

    # Fast mode: thinking explicitly suppressed and the budget stays small.
    content, events = run(False)
    assert content == "Paris"
    assert requests[-1]["reasoning_effort"] == "none"
    assert requests[-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[-1]["max_tokens"] == 2_048
    # Even though the server sent reasoning, fast mode must not surface it.
    assert not [event for event, _ in events if event == "reasoning"]

    # Reasoning mode: suppression removed and the budget raised.
    content, events = run(True)
    assert content == "Paris"
    assert "reasoning_effort" not in requests[-1]
    assert "chat_template_kwargs" not in requests[-1]
    assert requests[-1]["max_tokens"] == 8_192
    thoughts = [p["content"] for e, p in events if e == "reasoning"]
    assert thoughts == ["weighing ", "options"]
    # The stored answer must contain no chain-of-thought.
    assert "weighing" not in content
