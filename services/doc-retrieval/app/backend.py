# SPDX-License-Identifier: AGPL-3.0-or-later
"""Clients for Machine B's two loopback llama-server instances.

Both calls run entirely on Machine B's RTX 3070. Machine A issues one request
to this service and never talks to either model server.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import numpy as np

from .config import Settings


USER_AGENT = "KevinBeLLM-doc-retrieval/1.0"
MAX_EMBEDDING_BYTES = 64 * 1024 * 1024
MAX_RERANK_BYTES = 4 * 1024 * 1024


class BackendError(RuntimeError):
    """A model backend on Machine B failed or answered unusably."""


async def _post_json(
    client: httpx.AsyncClient,
    url: str,
    body: dict[str, Any],
    *,
    timeout: float,
    maximum_bytes: int,
) -> Any:
    try:
        async with client.stream(
            "POST",
            url,
            json=body,
            headers={"Accept": "application/json", "User-Agent": USER_AGENT},
            follow_redirects=False,
            timeout=timeout,
        ) as response:
            if response.status_code < 200 or response.status_code >= 300:
                raise BackendError(f"model backend returned HTTP {response.status_code}")
            content = bytearray()
            async for piece in response.aiter_bytes():
                content.extend(piece)
                if len(content) > maximum_bytes:
                    raise BackendError("model backend response was too large")
    except BackendError:
        raise
    except httpx.HTTPError as exc:
        raise BackendError("model backend is unreachable") from exc
    try:
        return json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackendError("model backend returned invalid JSON") from exc


def _embedding_row(entry: Any) -> list[float]:
    if not isinstance(entry, dict):
        raise BackendError("embedding backend returned a malformed entry")
    values = entry.get("embedding")
    if isinstance(values, list) and values and isinstance(values[0], list):
        raise BackendError(
            "embedding backend returned token vectors; start llama-server with --pooling cls"
        )
    if not isinstance(values, list) or not values:
        raise BackendError("embedding backend returned an empty vector")
    row: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BackendError("embedding backend returned a non-numeric value")
        row.append(float(value))
    return row


class ModelBackends:
    """Embedding and reranking calls against Machine B's local GPU servers."""

    def __init__(self, client: httpx.AsyncClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings

    async def embed(self, texts: list[str]) -> np.ndarray:
        """Embed a batch and return an (n, dim) float32 matrix in input order."""
        if not texts:
            raise BackendError("no text was supplied for embedding")
        payload = await _post_json(
            self.client,
            f"{self.settings.embedding_base_url}/v1/embeddings",
            {"model": self.settings.embedding_model, "input": texts},
            timeout=self.settings.backend_timeout_seconds,
            maximum_bytes=MAX_EMBEDDING_BYTES,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(texts):
            raise BackendError("embedding backend returned the wrong number of vectors")

        # The OpenAI schema allows any ordering, so place each row by its own
        # index rather than trusting the response order.
        rows: list[list[float] | None] = [None] * len(texts)
        for entry in data:
            position = entry.get("index") if isinstance(entry, dict) else None
            if isinstance(position, bool) or not isinstance(position, int):
                raise BackendError("embedding backend returned an entry without an index")
            if not 0 <= position < len(texts) or rows[position] is not None:
                raise BackendError("embedding backend returned a duplicate or out-of-range index")
            rows[position] = _embedding_row(entry)
        if any(row is None for row in rows):
            raise BackendError("embedding backend skipped an input")

        widths = {len(row) for row in rows if row is not None}
        if len(widths) != 1:
            raise BackendError("embedding backend returned inconsistent dimensions")
        matrix = np.asarray(rows, dtype=np.float32)
        if not np.all(np.isfinite(matrix)):
            raise BackendError("embedding backend returned non-finite values")
        return matrix

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        """Score each document against the query, aligned to the input order."""
        if not documents:
            return []
        payload = await _post_json(
            self.client,
            f"{self.settings.reranker_base_url}/v1/rerank",
            {
                "model": self.settings.reranker_model,
                "query": query,
                "documents": documents,
                "top_n": len(documents),
            },
            timeout=self.settings.backend_timeout_seconds,
            maximum_bytes=MAX_RERANK_BYTES,
        )
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            raise BackendError("reranker returned no results")
        scores: list[float | None] = [None] * len(documents)
        for entry in results:
            if not isinstance(entry, dict):
                raise BackendError("reranker returned a malformed result")
            position = entry.get("index")
            if isinstance(position, bool) or not isinstance(position, int):
                raise BackendError("reranker returned a result without an index")
            if not 0 <= position < len(documents):
                raise BackendError("reranker returned an out-of-range index")
            # llama.cpp reports relevance_score; accept score from other builds.
            raw = entry.get("relevance_score", entry.get("score"))
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise BackendError("reranker returned a non-numeric score")
            value = float(raw)
            if not np.isfinite(value):
                raise BackendError("reranker returned a non-finite score")
            scores[position] = value
        if any(score is None for score in scores):
            raise BackendError("reranker skipped a candidate")
        return [float(score) for score in scores if score is not None]
