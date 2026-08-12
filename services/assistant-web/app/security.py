# SPDX-License-Identifier: AGPL-3.0-or-later
"""Password, session, CSRF, origin, and small in-memory rate-limit helpers."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import HTTPException, Request


SESSION_COOKIE = "kevinbellm_session"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,100}$")

# Argon2id with deliberately meaningful memory cost. Password work runs off the
# event loop in callers so one login cannot stall unrelated health checks.
PASSWORD_HASHER = PasswordHasher(time_cost=3, memory_cost=65_536, parallelism=2)
DUMMY_PASSWORD_HASH = PASSWORD_HASHER.hash(secrets.token_urlsafe(32))


def normalize_email(value: str) -> str:
    email = value.strip().casefold()
    if (
        not 3 <= len(email) <= 254
        or email.count("@") != 1
        or any(character.isspace() for character in email)
    ):
        raise ValueError("invalid email")
    local, domain = email.rsplit("@", 1)
    if not local or not domain or "." not in domain and domain != "localhost":
        raise ValueError("invalid email")
    return email


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(PASSWORD_HASHER.hash, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    candidate = password_hash or DUMMY_PASSWORD_HASH
    try:
        valid = await asyncio.to_thread(PASSWORD_HASHER.verify, candidate, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return bool(valid and password_hash is not None)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def session_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("ascii", "strict")).digest()


def valid_session_token(token: str | None) -> bool:
    return bool(token and _TOKEN_RE.fullmatch(token))


def csrf_token(session_token: str) -> str:
    return hmac.new(
        session_token.encode("ascii"), b"kevinbellm-csrf-v1", hashlib.sha256
    ).hexdigest()


def check_csrf(session_token: str, supplied: str | None) -> None:
    expected = csrf_token(session_token)
    if not supplied or not secrets.compare_digest(expected, supplied):
        raise HTTPException(status_code=403, detail="CSRF validation failed")


def _origin(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username or parsed.password:
            return None
        return f"{parsed.scheme}://{parsed.netloc}".lower()
    except ValueError:
        return None


def check_request_origin(request: Request, public_origin: str) -> None:
    """Reject browser cross-site writes while allowing non-browser API clients.

    Modern browsers send Origin and Sec-Fetch-Site on fetch() POSTs. Requests
    without either signal are still usable by CLI clients, which do not create a
    browser CSRF risk and must still supply CSRF for authenticated mutations.
    """

    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site == "cross-site":
        raise HTTPException(status_code=403, detail="Cross-site request rejected")

    supplied = request.headers.get("origin")
    if not supplied:
        referer = request.headers.get("referer")
        supplied = referer if referer else None
    if not supplied:
        return

    request_origin = _origin(str(request.base_url))
    supplied_origin = _origin(supplied)
    local_same_origin = False
    if supplied_origin and supplied_origin == request_origin:
        try:
            hostname = urlsplit(supplied_origin).hostname
            local_same_origin = hostname in {"localhost", "127.0.0.1", "::1"}
        except ValueError:
            local_same_origin = False
    if supplied_origin is None or not (
        supplied_origin == public_origin or local_same_origin
    ):
        raise HTTPException(status_code=403, detail="Request origin rejected")


@dataclass(frozen=True, slots=True)
class LimitResult:
    allowed: bool
    retry_after: int


class BoundedRateLimiter:
    """A process-local sliding-window limiter with an explicit key ceiling."""

    def __init__(self, requests: int, window_seconds: int, max_keys: int = 2_048):
        self.requests = requests
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._entries: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, key: str) -> LimitResult:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            timestamps = self._entries.pop(key, deque())
            while timestamps and timestamps[0] <= cutoff:
                timestamps.popleft()
            if len(timestamps) >= self.requests:
                self._entries[key] = timestamps
                retry = max(1, int(self.window_seconds - (now - timestamps[0])) + 1)
                return LimitResult(False, retry)
            timestamps.append(now)
            self._entries[key] = timestamps
            while len(self._entries) > self.max_keys:
                self._entries.popitem(last=False)
            return LimitResult(True, 0)
