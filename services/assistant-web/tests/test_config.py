# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import pytest

from app.config import load_settings


def _clear_inference_environment(monkeypatch) -> None:
    monkeypatch.delenv("INFERENCE_BASE_URL", raising=False)


def test_inference_defaults_to_llamacpp(monkeypatch) -> None:
    _clear_inference_environment(monkeypatch)
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)

    settings = load_settings()

    assert settings.inference_base_url == "http://127.0.0.1:8080"
    assert settings.default_model == "kevinbellm-27b"
    assert settings.chat_concurrency == 1


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
