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
    assert settings.default_model == "kevinbellm-27b"


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


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("INFERENCE_BACKEND", "vllm", "INFERENCE_BACKEND"),
        ("INFERENCE_BASE_URL", "http://192.168.1.25:8080", "loopback host"),
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
