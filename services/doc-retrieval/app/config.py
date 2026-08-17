# SPDX-License-Identifier: AGPL-3.0-or-later
"""Machine B retrieval settings.

Every value here describes work that happens on Machine B's RTX 3070. Machine A
never imports this module; it only issues one HTTP request per tool call.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


INDEX_FORMAT_VERSION = 1


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _loopback_url(name: str, default: str) -> str:
    """Both model backends stay on Machine B's own loopback interface.

    The GPU servers have no authentication, so a bind or target address that
    leaves loopback would publish an unauthenticated model API to the LAN.
    """
    value = os.getenv(name, default).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"{name} must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(f"{name} must not contain credentials, a query, or a fragment")
    if parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"{name} must use a loopback host")
    return value


def _model_alias(name: str, default: str) -> str:
    value = os.getenv(name, default).strip()
    if not 1 <= len(value) <= 128:
        raise RuntimeError(f"{name} must be between 1 and 128 characters")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    index_dir: Path
    embedding_base_url: str
    embedding_model: str
    reranker_base_url: str
    reranker_model: str
    candidates: int
    max_results: int
    excerpt_chars: int
    max_chunks: int
    backend_timeout_seconds: float


def load_settings() -> Settings:
    default_index = Path.home() / ".local/share/kevinbellm-retrieval/index"
    candidates = _bounded_int("RETRIEVAL_CANDIDATES", 40, 1, 200)
    max_results = _bounded_int("RETRIEVAL_MAX_RESULTS", 10, 1, 20)
    if max_results > candidates:
        raise RuntimeError("RETRIEVAL_MAX_RESULTS must not exceed RETRIEVAL_CANDIDATES")
    return Settings(
        index_dir=Path(
            os.getenv("RETRIEVAL_INDEX_DIR", str(default_index))
        ).expanduser(),
        embedding_base_url=_loopback_url("EMBEDDING_BASE_URL", "http://127.0.0.1:8081"),
        # The same variable names the llama-server units pass to --alias, so the
        # model name in a request always matches what the server advertises.
        embedding_model=_model_alias("EMBEDDING_MODEL_ALIAS", "kevinbellm-embed"),
        reranker_base_url=_loopback_url("RERANKER_BASE_URL", "http://127.0.0.1:8082"),
        reranker_model=_model_alias("RERANKER_MODEL_ALIAS", "kevinbellm-rerank"),
        candidates=candidates,
        max_results=max_results,
        excerpt_chars=_bounded_int("RETRIEVAL_EXCERPT_CHARS", 1_200, 200, 4_000),
        max_chunks=_bounded_int("RETRIEVAL_MAX_CHUNKS", 50_000, 1, 500_000),
        backend_timeout_seconds=_bounded_float(
            "RETRIEVAL_BACKEND_TIMEOUT_SECONDS", 20.0, 1.0, 120.0
        ),
    )
