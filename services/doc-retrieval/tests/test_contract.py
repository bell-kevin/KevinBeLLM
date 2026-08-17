# SPDX-License-Identifier: AGPL-3.0-or-later
"""Machine B's half of the published wire contract.

Machine A and Machine B are separate hosts that are upgraded independently, so
the shared fixture is the only thing that keeps them agreeing. The matching
assertions live in services/assistant-web/tests/test_retrieval.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import SearchRequest, create_app
from app.store import Chunk, write_index


CONTRACT = json.loads(
    (
        Path(__file__).resolve().parents[3] / "tests" / "contract" / "doc-retrieval-search.json"
    ).read_text(encoding="utf-8")
)


class _FakeBackends:
    def __init__(self, _client, _settings) -> None:
        pass

    async def embed(self, texts: list[str]) -> np.ndarray:
        return np.array([[1.0, 0.0]] * len(texts), dtype=np.float32)

    async def rerank(self, _query: str, documents: list[str]) -> list[float]:
        return [float(len(documents) - index) for index in range(len(documents))]


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setattr("app.main.ModelBackends", _FakeBackends)
    write_index(
        tmp_path / "index",
        embedding_model="kevinbellm-embed",
        built_at="2026-08-16T00:00:00+00:00",
        document_count=2,
        chunks=[
            Chunk(document="house/roof.md", title="Roof replacement", ordinal=0, text="a"),
            Chunk(document="house/insurance-2025.txt", title="Policy", ordinal=3, text="b"),
        ],
        vectors=np.array([[1.0, 0.0], [0.9, 0.1]], dtype=np.float32),
    )
    settings = Settings(
        index_dir=tmp_path / "index",
        embedding_base_url="http://127.0.0.1:8081",
        embedding_model="kevinbellm-embed",
        reranker_base_url="http://127.0.0.1:8082",
        reranker_model="kevinbellm-rerank",
        candidates=40,
        max_results=10,
        excerpt_chars=1_200,
        max_chunks=1_000,
        backend_timeout_seconds=20.0,
    )
    return TestClient(create_app(settings))


def test_the_contract_request_is_accepted_exactly_as_published() -> None:
    """extra='forbid' means an unexpected field from Machine A is a 422."""
    parsed = SearchRequest(**CONTRACT["request"])

    assert parsed.query == CONTRACT["request"]["query"]
    assert parsed.limit == CONTRACT["request"]["limit"]


def test_the_service_answers_with_the_published_response_keys(client) -> None:
    with client:
        body = client.post("/search", json=CONTRACT["request"]).json()

    expected = CONTRACT["response"]
    assert set(body) == set(expected)
    assert body["passages"], "the fixture exercises a non-empty result set"
    for passage in body["passages"]:
        assert set(passage) == set(expected["passages"][0])


def test_the_published_response_field_types_still_hold(client) -> None:
    with client:
        body = client.post("/search", json=CONTRACT["request"]).json()

    expected_passage = CONTRACT["response"]["passages"][0]
    for key, value in CONTRACT["response"].items():
        if key == "passages":
            continue
        assert isinstance(body[key], type(value)), f"{key} changed type"
    for key, value in expected_passage.items():
        actual = body["passages"][0][key]
        if isinstance(value, bool):
            assert isinstance(actual, bool), f"passages[].{key} changed type"
        elif isinstance(value, float):
            assert isinstance(actual, (int, float)), f"passages[].{key} changed type"
        else:
            assert isinstance(actual, type(value)), f"passages[].{key} changed type"


def test_the_untrusted_data_warning_is_still_sent(client) -> None:
    """The model is told this is data, not instructions, on every response."""
    with client:
        body = client.post("/search", json=CONTRACT["request"]).json()

    assert "UNTRUSTED DOCUMENT DATA" in body["notice"]
    assert "UNTRUSTED DOCUMENT DATA" in CONTRACT["response"]["notice"]
