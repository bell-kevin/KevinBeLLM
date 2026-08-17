# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio
import json

import httpx
import numpy as np
import pytest

from app.backend import BackendError, ModelBackends
from app.config import Settings


def _settings(tmp_path) -> Settings:
    return Settings(
        index_dir=tmp_path / "index",
        embedding_base_url="http://127.0.0.1:8081",
        embedding_model="kevinbellm-embed",
        reranker_base_url="http://127.0.0.1:8082",
        reranker_model="kevinbellm-rerank",
        candidates=40,
        max_results=5,
        excerpt_chars=1_200,
        max_chunks=50_000,
        backend_timeout_seconds=20.0,
    )


def _run(tmp_path, handler, call):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await call(ModelBackends(client, _settings(tmp_path)))

    return asyncio.run(go())


def test_embedding_request_targets_the_openai_endpoint(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"data": [{"index": 0, "embedding": [1.0, 2.0, 3.0]}]},
        )

    matrix = _run(tmp_path, handler, lambda backends: backends.embed(["hello"]))

    assert str(seen[0].url) == "http://127.0.0.1:8081/v1/embeddings"
    assert json.loads(seen[0].content) == {
        "model": "kevinbellm-embed",
        "input": ["hello"],
    }
    assert matrix.shape == (1, 3)
    assert matrix.dtype == np.float32


def test_embeddings_are_reordered_to_match_the_input(tmp_path) -> None:
    """The OpenAI schema does not promise response ordering."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    matrix = _run(tmp_path, handler, lambda backends: backends.embed(["a", "b"]))

    assert matrix.tolist() == [[1.0, 0.0], [0.0, 1.0]]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"data": []}, "wrong number"),
        ({"data": [{"index": 0, "embedding": []}]}, "empty vector"),
        ({"data": [{"embedding": [1.0]}]}, "without an index"),
        ({"data": [{"index": 5, "embedding": [1.0]}]}, "out-of-range"),
        ({"data": [{"index": 0, "embedding": [1.0, "x"]}]}, "non-numeric"),
        ({"data": [{"index": 0, "embedding": [[1.0], [2.0]]}]}, "--pooling cls"),
    ],
)
def test_malformed_embedding_responses_are_rejected(tmp_path, body, message) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    with pytest.raises(BackendError, match=message):
        _run(tmp_path, handler, lambda backends: backends.embed(["only-one"]))


def test_inconsistent_embedding_widths_are_rejected(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 0, "embedding": [1.0, 2.0]},
                    {"index": 1, "embedding": [1.0]},
                ]
            },
        )

    with pytest.raises(BackendError, match="inconsistent dimensions"):
        _run(tmp_path, handler, lambda backends: backends.embed(["a", "b"]))


def test_an_unreachable_backend_is_reported_safely(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(BackendError, match="unreachable"):
        _run(tmp_path, handler, lambda backends: backends.embed(["a"]))


def test_a_backend_error_status_is_reported(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(BackendError, match="HTTP 500"):
        _run(tmp_path, handler, lambda backends: backends.embed(["a"]))


def test_rerank_scores_are_aligned_to_the_input_order(tmp_path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 2, "relevance_score": -1.0},
                    {"index": 0, "relevance_score": 5.0},
                    {"index": 1, "relevance_score": 2.0},
                ]
            },
        )

    scores = _run(
        tmp_path, handler, lambda backends: backends.rerank("q", ["a", "b", "c"])
    )

    assert str(seen[0].url) == "http://127.0.0.1:8082/v1/rerank"
    assert json.loads(seen[0].content)["top_n"] == 3
    assert scores == [5.0, 2.0, -1.0]


def test_rerank_accepts_the_alternative_score_field(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"index": 0, "score": 1.25}]})

    assert _run(tmp_path, handler, lambda backends: backends.rerank("q", ["a"])) == [1.25]


def test_rerank_rejects_a_skipped_candidate(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    with pytest.raises(BackendError, match="skipped a candidate"):
        _run(tmp_path, handler, lambda backends: backends.rerank("q", ["a", "b"]))


def test_reranking_nothing_is_not_an_error(tmp_path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("no request should be made")

    assert _run(tmp_path, handler, lambda backends: backends.rerank("q", [])) == []
