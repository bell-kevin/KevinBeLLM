# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configuration with conservative, self-hosted defaults."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


INFERENCE_BACKENDS = frozenset({"ollama", "llamacpp"})


def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value


def _http_url(name: str, default: str, *, loopback_only: bool = False) -> str:
    value = os.getenv(name, default).strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RuntimeError(f"{name} must be an http(s) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise RuntimeError(f"{name} must not contain credentials, a query, or a fragment")
    if loopback_only and parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"{name} must use a loopback host")
    return value


def _inference_backend() -> str:
    backend = os.getenv("INFERENCE_BACKEND", "ollama").strip().lower()
    if backend not in INFERENCE_BACKENDS:
        choices = " or ".join(sorted(INFERENCE_BACKENDS))
        raise RuntimeError(f"INFERENCE_BACKEND must be {choices}")
    return backend


def _inference_url(backend: str) -> str:
    default = (
        "http://127.0.0.1:11434"
        if backend == "ollama"
        else "http://127.0.0.1:8080"
    )
    # OLLAMA_URL and OLLAMA_BASE_URL remain supported when the new generic
    # setting is absent so existing deployments do not need a flag day.
    if "OLLAMA_URL" in os.environ:
        default = os.environ["OLLAMA_URL"]
    elif "OLLAMA_BASE_URL" in os.environ:
        default = os.environ["OLLAMA_BASE_URL"]
    return _http_url("INFERENCE_BASE_URL", default, loopback_only=True)


def _preferred_models() -> tuple[str, ...]:
    raw = (
        os.getenv("PREFERRED_MODELS")
        or os.getenv("MODEL_ORDER_LIST")
        or os.getenv("PINNED_MODELS")
        or ""
    ).strip()
    if not raw:
        return ()
    if raw.startswith("["):
        try:
            values = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("MODEL_ORDER_LIST must be a JSON array") from exc
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            raise RuntimeError("MODEL_ORDER_LIST must contain only strings")
    else:
        values = raw.split(",")
    cleaned: list[str] = []
    for value in values:
        model = value.strip()
        if model and model not in cleaned:
            cleaned.append(model)
    return tuple(cleaned[:50])


@dataclass(frozen=True, slots=True)
class Settings:
    data_dir: Path
    public_url: str
    public_origin: str
    secure_cookie: bool
    source_url: str
    ollama_url: str
    searxng_url: str
    live_tools_url: str
    default_model: str
    preferred_models: tuple[str, ...]
    session_ttl_seconds: int
    fetch_max_bytes: int
    tool_result_max_chars: int
    chat_concurrency: int
    chat_pending: int
    chat_queue_timeout_seconds: int
    chat_deadline_seconds: int
    fetch_deadline_seconds: int
    database_concurrency: int
    ollama_context_length: int
    # Defaults keep keyword construction by older callers source-compatible.
    inference_backend: str = "ollama"
    inference_base_url: str | None = None


def load_settings() -> Settings:
    public_url = _http_url("PUBLIC_URL", "http://localhost:3000")
    parsed_public = urlsplit(public_url)
    public_origin = f"{parsed_public.scheme}://{parsed_public.netloc}".lower()
    inference_backend = _inference_backend()
    inference_base_url = _inference_url(inference_backend)
    default_model = os.getenv("DEFAULT_MODEL", "qwen3.6:35b-a3b-q4_K_M").strip()
    if not default_model or len(default_model) > 200:
        raise RuntimeError("DEFAULT_MODEL must be between 1 and 200 characters")
    return Settings(
        data_dir=Path(os.getenv("DATA_DIR", "/data")).expanduser().resolve(),
        public_url=public_url,
        public_origin=public_origin,
        secure_cookie=parsed_public.scheme == "https",
        source_url=_http_url(
            "SOURCE_URL", "https://github.com/bell-kevin/ASUS-KevinBeLLM"
        ),
        # Keep this alias populated for older code that still reads ollama_url.
        ollama_url=inference_base_url,
        searxng_url=_http_url(
            "SEARXNG_URL", "http://127.0.0.1:8888", loopback_only=True
        ),
        live_tools_url=_http_url(
            "LIVE_TOOLS_URL", "http://127.0.0.1:8090", loopback_only=True
        ),
        default_model=default_model,
        preferred_models=_preferred_models(),
        session_ttl_seconds=_bounded_int("SESSION_TTL_HOURS", 24, 1, 24 * 30) * 3600,
        fetch_max_bytes=_bounded_int("FETCH_MAX_BYTES", 262_144, 65_536, 1_048_576),
        tool_result_max_chars=_bounded_int("TOOL_RESULT_MAX_CHARS", 12_000, 2_000, 30_000),
        chat_concurrency=_bounded_int("CHAT_CONCURRENCY", 2, 1, 8),
        chat_pending=_bounded_int("CHAT_PENDING", 2, 0, 16),
        chat_queue_timeout_seconds=_bounded_int("CHAT_QUEUE_TIMEOUT_SECONDS", 30, 1, 120),
        chat_deadline_seconds=_bounded_int("CHAT_DEADLINE_SECONDS", 1_200, 60, 3_600),
        fetch_deadline_seconds=_bounded_int("FETCH_DEADLINE_SECONDS", 30, 5, 120),
        database_concurrency=_bounded_int("DATABASE_CONCURRENCY", 8, 1, 32),
        ollama_context_length=_bounded_int(
            "OLLAMA_CONTEXT_LENGTH", 16_384, 2_048, 131_072
        ),
        inference_backend=inference_backend,
        inference_base_url=inference_base_url,
    )


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
