# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import Settings
from app.tools import SEARCH_LANGUAGE, ToolRunner


def _settings(tmp_path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        public_url="http://localhost:3000",
        public_origin="http://localhost:3000",
        secure_cookie=False,
        source_url="https://example.org/source",
        inference_base_url="http://127.0.0.1:8080",
        searxng_url="http://127.0.0.1:8888",
        live_tools_url="http://127.0.0.1:8090",
        default_model="test-model",
        preferred_models=(),
        session_ttl_seconds=3600,
        fetch_max_bytes=65_536,
        tool_result_max_chars=4_000,
        chat_concurrency=1,
        chat_pending=0,
        chat_queue_timeout_seconds=1,
        chat_deadline_seconds=60,
        fetch_deadline_seconds=5,
        database_concurrency=2,
    )


@pytest.mark.parametrize(
    ("tool_name", "expected_categories"),
    [("web_search", None), ("news_search", "news")],
)
def test_search_pins_a_locale_instead_of_leaking_the_hosts_geography(
    tmp_path, tool_name: str, expected_categories: str | None
) -> None:
    """Sending language=all let engines geolocate the exit IP.

    That turned neutral questions into nearby-business listings, so the locale
    is now explicit and must never regress to "all".
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"results": []})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = ToolRunner(client, _settings(tmp_path))
            return await runner.run(tool_name, {"query": "who is dr ben carson"})

    result = asyncio.run(run())
    assert result.sources == ()
    assert len(requests) == 1
    params = requests[0].url.params
    assert params["language"] == SEARCH_LANGUAGE != "all"
    assert params["q"] == "who is dr ben carson"
    assert params["safesearch"] == "1"
    assert params.get("categories") == expected_categories
