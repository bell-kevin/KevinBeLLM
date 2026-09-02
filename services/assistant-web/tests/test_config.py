# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import pytest

from app.config import DEFAULT_REASONING_BUDGET_MESSAGE, load_settings


def _clear_inference_environment(monkeypatch) -> None:
    monkeypatch.delenv("INFERENCE_BASE_URL", raising=False)
    monkeypatch.delenv("ZOO_API_BASE_URL", raising=False)


def test_inference_defaults_to_llamacpp(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)

    settings = load_settings()

    assert settings.inference_base_url == "http://127.0.0.1:8080"
    assert settings.default_model == "kevinbellm-27b"
    assert settings.chat_concurrency == 1
    assert settings.zoo_api_base_url == "http://127.0.0.1:3000/v1"
    assert settings.api_token_ttl_seconds == 30 * 24 * 3600


def test_llamacpp_inference_configuration(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("INFERENCE_BASE_URL", "http://127.0.0.1:8081/")

    settings = load_settings()

    assert settings.inference_base_url == "http://127.0.0.1:8081"


def test_non_loopback_inference_configuration_is_rejected(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    # The tunnel terminates on Machine A's loopback. A LAN address here would
    # bypass that boundary and is therefore rejected.
    monkeypatch.setenv("INFERENCE_BASE_URL", "http://192.168.1.25:8080")

    with pytest.raises(RuntimeError, match="loopback host"):
        load_settings()


def test_remote_zoo_api_requires_https_and_v1_path(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("ZOO_API_BASE_URL", "http://api.example.test/v1")
    with pytest.raises(RuntimeError, match="HTTPS"):
        load_settings()

    monkeypatch.setenv("ZOO_API_BASE_URL", "https://api.example.test/openai")
    with pytest.raises(RuntimeError, match="end in /v1"):
        load_settings()


def test_zoo_output_budget_must_leave_input_context(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.setenv("ZOO_CONTEXT_WINDOW", "4096")
    monkeypatch.setenv("ZOO_MAX_OUTPUT_TOKENS", "4096")

    with pytest.raises(RuntimeError, match="must be smaller"):
        load_settings()


def test_reasoning_budget_message_defaults_and_is_bounded(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.delenv("REASONING_BUDGET_MESSAGE", raising=False)
    assert load_settings().reasoning_budget_message == DEFAULT_REASONING_BUDGET_MESSAGE

    monkeypatch.setenv("REASONING_BUDGET_MESSAGE", "  Answer now.  ")
    assert load_settings().reasoning_budget_message == "Answer now."

    # Whitespace-only falls back to the default rather than injecting nothing.
    monkeypatch.setenv("REASONING_BUDGET_MESSAGE", "   ")
    assert load_settings().reasoning_budget_message == DEFAULT_REASONING_BUDGET_MESSAGE

    monkeypatch.setenv("REASONING_BUDGET_MESSAGE", "x" * 501)
    with pytest.raises(RuntimeError, match="at most 500"):
        load_settings()
