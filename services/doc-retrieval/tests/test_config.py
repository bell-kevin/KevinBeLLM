# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import pytest

from app.config import load_settings


SETTING_NAMES = (
    "RETRIEVAL_INDEX_DIR",
    "EMBEDDING_BASE_URL",
    "EMBEDDING_MODEL_ALIAS",
    "RERANKER_BASE_URL",
    "RERANKER_MODEL_ALIAS",
    "RETRIEVAL_CANDIDATES",
    "RETRIEVAL_MAX_RESULTS",
    "RETRIEVAL_EXCERPT_CHARS",
    "RETRIEVAL_MAX_CHUNKS",
    "RETRIEVAL_BACKEND_TIMEOUT_SECONDS",
)


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in SETTING_NAMES:
        monkeypatch.delenv(name, raising=False)
    yield


def test_defaults_target_machine_b_loopback() -> None:
    settings = load_settings()

    assert settings.embedding_base_url == "http://127.0.0.1:8081"
    assert settings.reranker_base_url == "http://127.0.0.1:8082"
    assert settings.embedding_model == "kevinbellm-embed"
    assert settings.reranker_model == "kevinbellm-rerank"
    assert settings.candidates == 40
    assert settings.max_results == 10


def test_model_aliases_use_the_same_names_the_units_pass_to_llama_server(
    monkeypatch,
) -> None:
    """The unit's --alias value and the request's model field must not drift."""
    monkeypatch.setenv("EMBEDDING_MODEL_ALIAS", "custom-embed")
    monkeypatch.setenv("RERANKER_MODEL_ALIAS", "custom-rerank")

    settings = load_settings()

    assert settings.embedding_model == "custom-embed"
    assert settings.reranker_model == "custom-rerank"


@pytest.mark.parametrize("name", ["EMBEDDING_BASE_URL", "RERANKER_BASE_URL"])
def test_a_non_loopback_model_endpoint_is_refused(monkeypatch, name: str) -> None:
    """These GPU servers have no authentication of their own."""
    monkeypatch.setenv(name, "http://192.168.0.11:8081")

    with pytest.raises(RuntimeError, match="loopback host"):
        load_settings()


@pytest.mark.parametrize(
    "value", ["ftp://127.0.0.1:8081", "http://user:pw@127.0.0.1:8081", "not-a-url"]
)
def test_a_malformed_model_endpoint_is_refused(monkeypatch, value: str) -> None:
    monkeypatch.setenv("EMBEDDING_BASE_URL", value)

    with pytest.raises(RuntimeError, match="EMBEDDING_BASE_URL"):
        load_settings()


def test_returning_more_results_than_candidates_is_refused(monkeypatch) -> None:
    """Ranking cannot return passages the reranker was never shown."""
    monkeypatch.setenv("RETRIEVAL_CANDIDATES", "3")
    monkeypatch.setenv("RETRIEVAL_MAX_RESULTS", "8")

    with pytest.raises(RuntimeError, match="must not exceed"):
        load_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("RETRIEVAL_CANDIDATES", "0"),
        ("RETRIEVAL_CANDIDATES", "9999"),
        ("RETRIEVAL_EXCERPT_CHARS", "10"),
        ("RETRIEVAL_MAX_CHUNKS", "0"),
        ("RETRIEVAL_BACKEND_TIMEOUT_SECONDS", "999"),
        ("RETRIEVAL_CANDIDATES", "not-a-number"),
    ],
)
def test_out_of_range_settings_are_refused(monkeypatch, name: str, value: str) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        load_settings()


def test_a_long_model_alias_is_refused(monkeypatch) -> None:
    monkeypatch.setenv("EMBEDDING_MODEL_ALIAS", "x" * 200)

    with pytest.raises(RuntimeError, match="EMBEDDING_MODEL_ALIAS"):
        load_settings()
