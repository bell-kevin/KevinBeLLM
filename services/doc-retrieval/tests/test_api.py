# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.backend import BackendError
from app.config import Settings
from app.main import create_app
from app.store import Chunk, write_index


DIMENSION = 4


def _settings(tmp_path, **overrides) -> Settings:
    base = dict(
        index_dir=tmp_path / "index",
        embedding_base_url="http://127.0.0.1:8081",
        embedding_model="kevinbellm-embed",
        reranker_base_url="http://127.0.0.1:8082",
        reranker_model="kevinbellm-rerank",
        candidates=10,
        max_results=5,
        excerpt_chars=40,
        max_chunks=1_000,
        backend_timeout_seconds=20.0,
    )
    base.update(overrides)
    return Settings(**base)


def _build_index(tmp_path, texts: list[str], vectors: list[list[float]]) -> None:
    write_index(
        tmp_path / "index",
        embedding_model="kevinbellm-embed",
        built_at="2026-08-16T00:00:00+00:00",
        document_count=len(texts),
        chunks=[
            Chunk(document=f"notes/doc-{i}.md", title=f"Doc {i}", ordinal=0, text=text)
            for i, text in enumerate(texts)
        ],
        vectors=np.array(vectors, dtype=np.float32),
    )


class _FakeBackends:
    """Stands in for Machine B's two GPU servers."""

    query_vector = [1.0, 0.0, 0.0, 0.0]
    rerank_scores: list[float] | None = None
    embed_error: str | None = None
    rerank_error: str | None = None
    embed_dimension: int | None = None

    def __init__(self, _client, _settings) -> None:
        pass

    async def embed(self, texts: list[str]) -> np.ndarray:
        if _FakeBackends.embed_error:
            raise BackendError(_FakeBackends.embed_error)
        width = _FakeBackends.embed_dimension or len(_FakeBackends.query_vector)
        row = (_FakeBackends.query_vector + [0.0] * width)[:width]
        return np.array([row for _ in texts], dtype=np.float32)

    async def rerank(self, _query: str, documents: list[str]) -> list[float]:
        if _FakeBackends.rerank_error:
            raise BackendError(_FakeBackends.rerank_error)
        if _FakeBackends.rerank_scores is not None:
            return _FakeBackends.rerank_scores[: len(documents)]
        return [float(len(documents) - index) for index in range(len(documents))]


@pytest.fixture(autouse=True)
def _reset_fake():
    _FakeBackends.query_vector = [1.0, 0.0, 0.0, 0.0]
    _FakeBackends.rerank_scores = None
    _FakeBackends.embed_error = None
    _FakeBackends.rerank_error = None
    _FakeBackends.embed_dimension = None
    yield


@pytest.fixture
def client_factory(monkeypatch):
    monkeypatch.setattr("app.main.ModelBackends", _FakeBackends)

    def build(settings: Settings) -> TestClient:
        return TestClient(create_app(settings))

    return build


def test_health_reports_a_degraded_service_when_no_index_exists(
    tmp_path, client_factory
) -> None:
    with client_factory(_settings(tmp_path)) as client:
        body = client.get("/health").json()

    assert body["status"] == "degraded"
    assert body["index_loaded"] is False
    assert "missing" in body["detail"]


def test_health_reports_the_loaded_index(tmp_path, client_factory) -> None:
    _build_index(tmp_path, ["alpha", "beta"], [[1, 0, 0, 0], [0, 1, 0, 0]])

    with client_factory(_settings(tmp_path)) as client:
        body = client.get("/health").json()

    assert body["status"] == "ok"
    assert (body["chunk_count"], body["document_count"]) == (2, 2)
    assert body["embedding_model"] == "kevinbellm-embed"


def test_search_returns_reranked_passages(tmp_path, client_factory) -> None:
    _build_index(
        tmp_path,
        ["alpha passage", "beta passage", "gamma passage"],
        [[1, 0, 0, 0], [0.9, 0.1, 0, 0], [0, 1, 0, 0]],
    )
    # Reranking, not the vector order, decides the final ranking.
    _FakeBackends.rerank_scores = [1.0, 9.0, 5.0]

    with client_factory(_settings(tmp_path)) as client:
        body = client.post("/search", json={"query": "alpha", "limit": 3}).json()

    assert [passage["excerpt"] for passage in body["passages"]] == [
        "beta passage",
        "gamma passage",
        "alpha passage",
    ]
    assert body["passages"][0]["rerank_score"] == 9.0
    assert body["considered"] == 3
    assert "UNTRUSTED DOCUMENT DATA" in body["notice"]


def test_search_honours_the_configured_result_ceiling(tmp_path, client_factory) -> None:
    _build_index(tmp_path, [f"passage {i}" for i in range(6)], [[1, 0, 0, 0]] * 6)

    with client_factory(_settings(tmp_path, max_results=2)) as client:
        body = client.post("/search", json={"query": "x", "limit": 20}).json()

    assert len(body["passages"]) == 2


def test_excerpts_are_truncated_and_flagged(tmp_path, client_factory) -> None:
    _build_index(tmp_path, ["y" * 200], [[1, 0, 0, 0]])

    with client_factory(_settings(tmp_path, excerpt_chars=40)) as client:
        passage = client.post("/search", json={"query": "x"}).json()["passages"][0]

    assert len(passage["excerpt"]) == 40
    assert passage["truncated"] is True


def test_search_is_unavailable_while_the_index_is_missing(
    tmp_path, client_factory
) -> None:
    with client_factory(_settings(tmp_path)) as client:
        response = client.post("/search", json={"query": "x"})

    assert response.status_code == 503
    assert "unavailable" in response.json()["detail"]


def test_a_dimension_change_demands_a_rebuild_instead_of_scoring_garbage(
    tmp_path, client_factory
) -> None:
    _build_index(tmp_path, ["alpha"], [[1, 0, 0, 0]])
    _FakeBackends.embed_dimension = 8

    with client_factory(_settings(tmp_path)) as client:
        response = client.post("/search", json={"query": "x"})

    assert response.status_code == 503
    assert "rebuild the index" in response.json()["detail"]


@pytest.mark.parametrize(
    ("field", "detail"),
    [("embed_error", "Embedding failed"), ("rerank_error", "Reranking failed")],
)
def test_a_failing_gpu_backend_becomes_a_bad_gateway(
    tmp_path, client_factory, field, detail
) -> None:
    _build_index(tmp_path, ["alpha"], [[1, 0, 0, 0]])
    setattr(_FakeBackends, field, "model backend is unreachable")

    with client_factory(_settings(tmp_path)) as client:
        response = client.post("/search", json={"query": "x"})

    assert response.status_code == 502
    assert detail in response.json()["detail"]


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "x" * 301},
        {"query": "x", "limit": 0},
        {"query": "x", "limit": 21},
        {"query": "x", "unexpected": True},
    ],
)
def test_invalid_search_requests_are_rejected(tmp_path, client_factory, body) -> None:
    _build_index(tmp_path, ["alpha"], [[1, 0, 0, 0]])

    with client_factory(_settings(tmp_path)) as client:
        assert client.post("/search", json=body).status_code == 422


def test_a_relevance_floor_filters_weak_matches(
    tmp_path, client_factory, monkeypatch
) -> None:
    monkeypatch.setenv("RETRIEVAL_MIN_SCORE", "2.0")
    _build_index(tmp_path, ["alpha", "beta"], [[1, 0, 0, 0], [0.9, 0.1, 0, 0]])
    _FakeBackends.rerank_scores = [5.0, 1.0]

    with client_factory(_settings(tmp_path)) as client:
        body = client.post("/search", json={"query": "x"}).json()

    assert [passage["excerpt"] for passage in body["passages"]] == ["alpha"]


def test_the_relevance_floor_is_disabled_by_default(
    tmp_path, client_factory, monkeypatch
) -> None:
    monkeypatch.delenv("RETRIEVAL_MIN_SCORE", raising=False)
    _build_index(tmp_path, ["alpha", "beta"], [[1, 0, 0, 0], [0.9, 0.1, 0, 0]])
    _FakeBackends.rerank_scores = [-50.0, -80.0]

    with client_factory(_settings(tmp_path)) as client:
        body = client.post("/search", json={"query": "x"}).json()

    assert len(body["passages"]) == 2
