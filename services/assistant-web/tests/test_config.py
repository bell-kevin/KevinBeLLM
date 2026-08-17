# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import pytest

from app.config import load_settings


def _clear_inference_environment(monkeypatch) -> None:
    for name in (
        "INFERENCE_BACKEND",
        "INFERENCE_BASE_URL",
        "OLLAMA_URL",
        "OLLAMA_BASE_URL",
        "DOC_RETRIEVAL_URL",
        "DOC_RETRIEVAL_TIMEOUT_SECONDS",
        "DOC_RETRIEVAL_MAX_RESULTS",
        "DOC_RETRIEVAL_FAILURE_THRESHOLD",
        "DOC_RETRIEVAL_COOLDOWN_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_inference_defaults_remain_ollama_compatible(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11435")

    settings = load_settings()

    assert settings.inference_backend == "ollama"
    assert settings.inference_base_url == "http://localhost:11435"
    assert settings.ollama_url == settings.inference_base_url


def test_new_inference_default_is_llamacpp(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)

    settings = load_settings()

    assert settings.inference_backend == "llamacpp"
    assert settings.inference_base_url == "http://127.0.0.1:8080"
    assert settings.ollama_url == settings.inference_base_url
    assert settings.default_model == "kevinbellm-9b"
    assert settings.chat_concurrency == 1


def test_llamacpp_inference_configuration(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("INFERENCE_BACKEND", "llamacpp")
    monkeypatch.setenv("INFERENCE_BASE_URL", "http://127.0.0.1:8081/")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")

    settings = load_settings()

    assert settings.inference_backend == "llamacpp"
    assert settings.inference_base_url == "http://127.0.0.1:8081"
    assert settings.ollama_url == settings.inference_base_url


def test_explicit_llamacpp_ignores_legacy_ollama_url(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("INFERENCE_BACKEND", "llamacpp")
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:11434")

    settings = load_settings()

    assert settings.inference_base_url == "http://127.0.0.1:8080"


def test_document_retrieval_is_off_unless_a_url_is_configured(monkeypatch) -> None:
    """Machine B is optional, so an unset URL is a normal state, not an error."""
    _clear_inference_environment(monkeypatch)

    settings = load_settings()

    assert settings.doc_retrieval_url == ""
    assert settings.doc_retrieval_enabled is False


def test_document_retrieval_defaults_are_conservative(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("DOC_RETRIEVAL_URL", "http://127.0.0.1:8091/")

    settings = load_settings()

    assert settings.doc_retrieval_url == "http://127.0.0.1:8091"
    assert settings.doc_retrieval_enabled is True
    assert settings.doc_retrieval_timeout_seconds == 8.0
    assert settings.doc_retrieval_connect_timeout_seconds == 2.0
    assert settings.doc_retrieval_max_results == 5
    assert settings.doc_retrieval_failure_threshold == 3
    assert settings.doc_retrieval_cooldown_seconds == 120


def test_a_short_retrieval_deadline_also_shortens_the_connect_budget(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("DOC_RETRIEVAL_URL", "http://127.0.0.1:8091")
    monkeypatch.setenv("DOC_RETRIEVAL_TIMEOUT_SECONDS", "1.5")

    settings = load_settings()

    assert settings.doc_retrieval_connect_timeout_seconds == 1.5


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("INFERENCE_BACKEND", "vllm", "INFERENCE_BACKEND"),
        ("INFERENCE_BASE_URL", "http://192.168.1.25:8080", "loopback host"),
        # The tunnel terminates on Machine A's loopback. A LAN address here
        # would mean an unauthenticated retrieval API published to the network.
        ("DOC_RETRIEVAL_URL", "http://192.168.1.26:8091", "loopback host"),
        ("DOC_RETRIEVAL_URL", "ftp://127.0.0.1:8091", "http"),
        ("DOC_RETRIEVAL_TIMEOUT_SECONDS", "600", "between"),
        ("DOC_RETRIEVAL_MAX_RESULTS", "99", "between"),
    ],
)
def test_invalid_inference_configuration_is_rejected(
    monkeypatch, name: str, value: str, message: str
) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("INFERENCE_BACKEND", "llamacpp")
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=message):
        load_settings()
