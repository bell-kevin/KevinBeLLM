# SPDX-License-Identifier: AGPL-3.0-or-later
"""Machine B document retrieval, and the guarantees that keep Machine A fast.

The load-bearing tests here are the negative ones: with retrieval switched off,
Machine A must send byte-identical prompts and tool lists, and with Machine B
down the assistant must degrade instead of waiting.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from app.assistant import _system_prompt, run_chat
from app.config import Settings
from app.tools import (
    DOCUMENTS_UNAVAILABLE,
    TOOL_DEFINITIONS,
    CircuitBreaker,
    ToolError,
    ToolRunner,
    breaker_for,
    reset_breakers,
    tool_definitions,
)


RETRIEVAL_URL = "http://127.0.0.1:8091"


@pytest.fixture(autouse=True)
def _clear_breaker_state():
    """Breakers are shared process state, so no test may inherit another's."""
    reset_breakers()
    yield
    reset_breakers()


def _settings(tmp_path, **overrides) -> Settings:
    base = Settings(
        data_dir=tmp_path,
        public_url="http://localhost:3000",
        public_origin="http://localhost:3000",
        secure_cookie=False,
        source_url="https://example.org/source",
        ollama_url="http://127.0.0.1:8080",
        searxng_url="http://127.0.0.1:8888",
        live_tools_url="http://127.0.0.1:8090",
        default_model="test-model",
        preferred_models=(),
        session_ttl_seconds=3600,
        fetch_max_bytes=65_536,
        tool_result_max_chars=12_000,
        chat_concurrency=1,
        chat_pending=0,
        chat_queue_timeout_seconds=1,
        chat_deadline_seconds=60,
        fetch_deadline_seconds=5,
        database_concurrency=2,
        ollama_context_length=4_096,
        inference_backend="llamacpp",
        inference_base_url="http://127.0.0.1:8080",
    )
    return replace(base, **overrides) if overrides else base


def _enabled(tmp_path, **overrides) -> Settings:
    return _settings(tmp_path, doc_retrieval_url=RETRIEVAL_URL, **overrides)


def _passage(index: int, text: str = "Passage body text.") -> dict:
    return {
        "document": f"notes/file-{index}.md",
        "title": f"File {index}",
        "ordinal": index,
        "excerpt": text,
        "truncated": False,
        "vector_score": 0.5,
        "rerank_score": 3.5 - index,
    }


def _run(coroutine_factory, handler=None):
    async def go():
        transport = httpx.MockTransport(handler) if handler else None
        async with httpx.AsyncClient(transport=transport) as client:
            return await coroutine_factory(client)

    return asyncio.run(go())


# --------------------------------------------------------------------------
# Machine A must be unchanged when retrieval is switched off.
# --------------------------------------------------------------------------


def test_disabled_retrieval_advertises_exactly_the_previous_tools(tmp_path) -> None:
    settings = _settings(tmp_path)

    assert settings.doc_retrieval_enabled is False
    assert tool_definitions(settings) == TOOL_DEFINITIONS
    assert tool_definitions(None) == TOOL_DEFINITIONS
    names = [tool["function"]["name"] for tool in tool_definitions(settings)]
    assert "search_documents" not in names


def test_disabled_retrieval_sends_a_byte_identical_system_prompt(tmp_path) -> None:
    """Prompt tokens are prefilled on Machine A's GPU for every single turn."""
    settings = _settings(tmp_path)

    assert _system_prompt(settings) == _system_prompt(None)
    assert "search_documents" not in _system_prompt(settings)


def test_enabled_retrieval_adds_one_tool_and_one_prompt_paragraph(tmp_path) -> None:
    settings = _enabled(tmp_path)

    definitions = tool_definitions(settings)
    assert len(definitions) == len(TOOL_DEFINITIONS) + 1
    assert definitions[:-1] == TOOL_DEFINITIONS
    assert definitions[-1]["function"]["name"] == "search_documents"
    assert "search_documents" in _system_prompt(settings)


def test_tool_definitions_cannot_be_mutated_through_the_returned_list(tmp_path) -> None:
    returned = tool_definitions(_settings(tmp_path))
    returned.append({"type": "function", "function": {"name": "injected"}})

    assert len(TOOL_DEFINITIONS) == len(tool_definitions(None))


# --------------------------------------------------------------------------
# The happy path.
# --------------------------------------------------------------------------


def test_document_search_is_one_bounded_request_to_machine_b(tmp_path) -> None:
    settings = _enabled(tmp_path, doc_retrieval_max_results=3)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "notice": "UNTRUSTED DOCUMENT DATA",
                "query": "roof warranty",
                "index_built_at": "2026-08-16T00:00:00+00:00",
                "considered": 40,
                "passages": [_passage(0), _passage(1), _passage(2)],
            },
        )

    result = _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": "roof warranty"}
        ),
        handler,
    )

    # Everything expensive happened on Machine B: exactly one round trip.
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == f"{RETRIEVAL_URL}/search"
    assert json.loads(requests[0].content) == {"query": "roof warranty", "limit": 3}

    payload = json.loads(result.content)
    assert [entry["document"] for entry in payload["results"]] == [
        "notes/file-0.md",
        "notes/file-1.md",
        "notes/file-2.md",
    ]
    assert payload["results"][0]["relevance"] == 3.5
    assert "UNTRUSTED DOCUMENT DATA" in payload["notice"]


def test_local_documents_contribute_no_citation_sources(tmp_path) -> None:
    """The browser's citation card only renders vetted public http(s) URLs.

    A local file has no such URL, so it is cited inline from the tool content
    instead of being pushed through the card's URL check.
    """
    settings = _enabled(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"passages": [_passage(0)]})

    result = _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": "anything"}
        ),
        handler,
    )

    assert result.sources == ()


def test_document_search_uses_a_short_connect_budget(tmp_path) -> None:
    """A hung Machine B must cost about two seconds, not the full deadline."""
    settings = _enabled(tmp_path, doc_retrieval_timeout_seconds=8.0)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.extensions["timeout"])
        return httpx.Response(200, json={"passages": []})

    _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": "x"}
        ),
        handler,
    )

    assert seen[0]["connect"] == 2.0
    assert seen[0]["read"] == 8.0
    assert settings.doc_retrieval_connect_timeout_seconds < settings.doc_retrieval_timeout_seconds


def test_untrusted_passage_fields_are_bounded_and_sanitized(tmp_path) -> None:
    settings = _enabled(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "passages": [
                    "not-an-object",
                    {"document": "d.md", "excerpt": ""},
                    {
                        "document": "x" * 900,
                        "title": "t" * 900,
                        "excerpt": "line one\x00\x07\nline two" + "y" * 5_000,
                        "rerank_score": "not-a-number",
                    },
                ]
            },
        )

    result = _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": "x"}
        ),
        handler,
    )

    payload = json.loads(result.content)
    assert len(payload["results"]) == 1
    entry = payload["results"][0]
    assert len(entry["document"]) == 300
    assert len(entry["excerpt"]) == 1_500
    # Control bytes are stripped, but real line structure survives.
    assert "\x00" not in entry["excerpt"] and "\x07" not in entry["excerpt"]
    assert entry["excerpt"].startswith("line one\nline two")
    # A non-numeric score is dropped rather than passed through to the model.
    assert "relevance" not in entry


def test_a_full_result_set_stays_within_the_tool_result_budget(tmp_path) -> None:
    """Ten long passages must stay a readable list, not one truncated blob."""
    settings = _enabled(
        tmp_path, doc_retrieval_max_results=10, tool_result_max_chars=12_000
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "passages": [
                    {
                        "document": f"notes/{'d' * 100}-{index}.md",
                        "title": "t" * 150,
                        "excerpt": ("paragraph text\n" * 400),
                        "rerank_score": 1.0,
                    }
                    for index in range(10)
                ]
            },
        )

    result = _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": "x"}
        ),
        handler,
    )

    assert len(result.content) <= settings.tool_result_max_chars
    payload = json.loads(result.content)
    # _capped_json's fallback shape would have replaced these keys entirely.
    assert "truncated" not in payload
    assert len(payload["results"]) == 10
    assert all(entry["excerpt"] for entry in payload["results"])


# --------------------------------------------------------------------------
# Machine A's half of the published wire contract. Machine B asserts the other
# half in services/doc-retrieval/tests/test_contract.py, so a field renamed on
# either host fails CI instead of failing in production after a partial upgrade.
# --------------------------------------------------------------------------

CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[3] / "tests" / "contract" / "doc-retrieval-search.json"
    ).read_text(encoding="utf-8")
)


def test_the_tool_sends_exactly_the_published_request(tmp_path) -> None:
    settings = _enabled(
        tmp_path, doc_retrieval_max_results=CONTRACT["request"]["limit"]
    )
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=CONTRACT["response"])

    _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": CONTRACT["request"]["query"]}
        ),
        handler,
    )

    assert sent == [CONTRACT["request"]]


def test_the_tool_parses_the_published_response(tmp_path) -> None:
    settings = _enabled(
        tmp_path, doc_retrieval_max_results=CONTRACT["request"]["limit"]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CONTRACT["response"])

    result = _run(
        lambda client: ToolRunner(client, settings).run(
            "search_documents", {"query": CONTRACT["request"]["query"]}
        ),
        handler,
    )

    payload = json.loads(result.content)
    expected = CONTRACT["response"]["passages"]
    assert len(payload["results"]) == len(expected)
    for entry, passage in zip(payload["results"], expected):
        assert entry["document"] == passage["document"]
        assert entry["title"] == passage["title"]
        # Multi-line document text must survive intact for the model to read.
        assert entry["excerpt"] == passage["excerpt"]
        assert entry["relevance"] == round(passage["rerank_score"], 3)


# --------------------------------------------------------------------------
# Failure must be cheap and must never block an answer.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler",
    [
        pytest.param(
            lambda _request: httpx.Response(503, json={"detail": "index unavailable"}),
            id="service-error",
        ),
        pytest.param(
            lambda _request: httpx.Response(200, json={"unexpected": True}),
            id="malformed-body",
        ),
    ],
)
def test_retrieval_failures_surface_as_a_safe_tool_error(tmp_path, handler) -> None:
    settings = _enabled(tmp_path)

    with pytest.raises(ToolError) as error:
        _run(
            lambda client: ToolRunner(client, settings).run(
                "search_documents", {"query": "x"}
            ),
            handler,
        )

    assert str(error.value) == DOCUMENTS_UNAVAILABLE


def test_unreachable_machine_b_surfaces_as_a_safe_tool_error(tmp_path) -> None:
    settings = _enabled(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ToolError) as error:
        _run(
            lambda client: ToolRunner(client, settings).run(
                "search_documents", {"query": "x"}
            ),
            handler,
        )

    assert str(error.value) == DOCUMENTS_UNAVAILABLE


def test_repeated_failures_stop_calling_a_powered_off_machine_b(tmp_path) -> None:
    """Machine B can be locked for days; every later turn must cost nothing."""
    settings = _enabled(tmp_path, doc_retrieval_failure_threshold=3)
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("connection refused")

    async def go(client):
        for _ in range(8):
            with pytest.raises(ToolError):
                await ToolRunner(client, settings).run("search_documents", {"query": "x"})

    _run(go, handler)

    # Three real attempts opened the breaker; the remaining five never touched
    # the network at all.
    assert attempts == 3


def test_breaker_allows_one_probe_after_the_cooldown_and_closes_on_success() -> None:
    clock = {"now": 1_000.0}
    breaker = CircuitBreaker(
        failure_threshold=2,
        cooldown_seconds=120.0,
        time_source=lambda: clock["now"],
    )

    assert breaker.allows() is True
    breaker.record_failure()
    assert breaker.allows() is True
    breaker.record_failure()
    assert breaker.allows() is False

    clock["now"] += 119.0
    assert breaker.allows() is False

    clock["now"] += 2.0
    assert breaker.allows() is True

    # A failed probe refreshes the cooldown instead of reopening the floodgate.
    breaker.record_failure()
    assert breaker.allows() is False
    clock["now"] += 121.0
    assert breaker.allows() is True

    breaker.record_success()
    assert breaker.allows() is True
    breaker.record_failure()
    assert breaker.allows() is True


def test_breaker_is_shared_across_requests_and_rebuilt_when_tuning_changes(tmp_path) -> None:
    settings = _enabled(tmp_path)

    first = breaker_for(settings)
    assert breaker_for(settings) is first

    retuned = replace(settings, doc_retrieval_cooldown_seconds=600)
    assert breaker_for(retuned) is not first
    assert breaker_for(retuned).cooldown_seconds == 600.0


def test_a_failed_document_search_still_produces_an_answer(monkeypatch, tmp_path) -> None:
    """The bounded tool loop treats retrieval like any other failing tool."""
    settings = _enabled(tmp_path)
    tool_messages: list[dict] = []

    async def fake_chat_once(
        _client, _settings, _model, messages, *, include_tools,
        on_delta=None, on_reasoning=None, reasoning=False,
    ):
        if include_tools and not tool_messages:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "search_documents",
                            "arguments": {"query": "my notes"},
                        }
                    }
                ],
            }
        tool_messages.extend(
            message for message in messages if message.get("role") == "tool"
        )
        return {"content": "Answered without the local documents."}

    monkeypatch.setattr("app.assistant._chat_once", fake_chat_once)

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    content, sources = _run(
        lambda client: run_chat(
            client,
            settings,
            "test-model",
            [{"role": "user", "content": "what do my notes say?"}],
            lambda _event, _payload: asyncio.sleep(0),
        ),
        handler,
    )

    assert content == "Answered without the local documents."
    assert sources == []
    assert json.loads(tool_messages[0]["content"]) == {"error": DOCUMENTS_UNAVAILABLE}


def test_the_tool_refuses_to_run_when_retrieval_is_not_configured(tmp_path) -> None:
    settings = _settings(tmp_path)

    with pytest.raises(ToolError, match="not configured"):
        _run(
            lambda client: ToolRunner(client, settings).run(
                "search_documents", {"query": "x"}
            )
        )
