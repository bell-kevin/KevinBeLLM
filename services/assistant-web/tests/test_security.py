# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import pytest

import asyncio

import httpx
from starlette.requests import Request

from app.tools import (
    ToolError,
    _is_public_address,
    _public_source_url,
    _safe_fetch_page,
    _validated_page_url,
    safe_fetch_page,
)
from app.security import check_request_origin
from app.main import MAX_REQUEST_BYTES, RequestBodyLimitMiddleware


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "0.0.0.0",
        "::1",
        "fe80::1",
        "fc00::1",
        "224.0.0.1",
    ],
)
def test_special_addresses_are_not_public(address: str) -> None:
    assert not _is_public_address(address)


def test_public_addresses_are_allowed() -> None:
    assert _is_public_address("1.1.1.1")
    assert _is_public_address("2606:4700:4700::1111")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://user:password@example.test/",
        "http://example.test:22/",
        "https://example.test:80/",
        "http://example.test:443/",
    ],
)
def test_unsafe_page_urls_are_rejected(url: str) -> None:
    with pytest.raises(ToolError):
        _validated_page_url(url)


def test_private_source_url_is_not_surfaced(monkeypatch) -> None:
    async def private_resolution(_hostname, _port):
        raise ToolError("private")

    monkeypatch.setattr("app.tools._resolve_public", private_resolution)
    assert asyncio.run(_public_source_url("http://127.0.0.1/")) is None


def test_https_fetch_uses_text_sni_and_idna_host(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeResponse:
        status_code = 200
        headers = httpx.Headers({"content-type": "text/plain; charset=utf-8"})

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def aiter_bytes(self):
            yield b"safe public text"

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def stream(self, _method, url, **kwargs):
            captured["url"] = url
            captured["host"] = kwargs["headers"]["Host"]
            captured["sni"] = kwargs["extensions"]["sni_hostname"]
            return FakeResponse()

    async def public_resolution(_hostname, _port):
        return ("203.0.113.10",)

    monkeypatch.setattr("app.tools.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("app.tools._resolve_public", public_resolution)
    result = asyncio.run(_safe_fetch_page("https://b\N{LATIN SMALL LETTER U WITH DIAERESIS}cher.example/", 65_536, 1_000))
    assert "safe public text" in result.content
    assert captured == {
        "url": "https://203.0.113.10/",
        "host": "xn--bcher-kva.example",
        "sni": "xn--bcher-kva.example",
    }


def test_fetch_has_absolute_deadline(monkeypatch) -> None:
    async def stalled_fetch(*_args):
        await asyncio.sleep(1)

    monkeypatch.setattr("app.tools._safe_fetch_page", stalled_fetch)
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ToolError, match="deadline"):
                await safe_fetch_page(client, "http://example.test", 1, 1, 0.01)

    asyncio.run(run())


def test_untrusted_host_cannot_expand_origin_allowlist() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "server": ("attacker.invalid", 443),
            "path": "/api/auth/login",
            "root_path": "",
            "query_string": b"",
            "headers": [
                (b"host", b"attacker.invalid"),
                (b"origin", b"https://attacker.invalid"),
            ],
        }
    )
    with pytest.raises(Exception) as caught:
        check_request_origin(request, "https://assistant.example")
    assert getattr(caught.value, "status_code", None) == 403


@pytest.mark.parametrize("declared_length", [None, "1"])
def test_body_limit_stops_chunked_or_lying_length(declared_length) -> None:
    consumed = 0
    inner_called = False
    sent = []
    chunks = [b"x" * (MAX_REQUEST_BYTES // 2)] * 3

    async def inner(_scope, receive, _send):
        nonlocal inner_called
        inner_called = True
        await receive()

    async def receive():
        nonlocal consumed
        chunk = chunks[consumed]
        consumed += 1
        return {
            "type": "http.request",
            "body": chunk,
            "more_body": consumed < len(chunks),
        }

    async def send(message):
        sent.append(message)

    headers = [] if declared_length is None else [(b"content-length", declared_length.encode())]
    scope = {"type": "http", "method": "POST", "headers": headers}
    asyncio.run(RequestBodyLimitMiddleware(inner, MAX_REQUEST_BYTES)(scope, receive, send))
    assert not inner_called
    assert consumed == 3
    assert sent[0]["status"] == 413


def test_declared_oversize_is_rejected_without_reading() -> None:
    receive_called = False
    inner_called = False
    sent = []

    async def inner(_scope, _receive, _send):
        nonlocal inner_called
        inner_called = True

    async def receive():
        nonlocal receive_called
        receive_called = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": "POST",
        "headers": [(b"content-length", str(MAX_REQUEST_BYTES + 1).encode())],
    }
    asyncio.run(RequestBodyLimitMiddleware(inner, MAX_REQUEST_BYTES)(scope, receive, send))
    assert not inner_called
    assert not receive_called
    assert sent[0]["status"] == 413
