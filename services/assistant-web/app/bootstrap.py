# SPDX-License-Identifier: AGPL-3.0-or-later
"""Explicit one-shot creation of the initial administrator account."""

from __future__ import annotations

import asyncio
import os

from .config import load_settings
from .database import Database
from .security import hash_password, normalize_email


async def _bootstrap() -> int:
    settings = load_settings()
    database = Database(
        settings.data_dir, settings.session_ttl_seconds, settings.database_concurrency
    )
    await database.initialize()
    try:
        email = normalize_email(os.getenv("ADMIN_EMAIL", ""))
    except ValueError as exc:
        raise RuntimeError("ADMIN_EMAIL must be a valid address") from exc
    name = os.getenv("ADMIN_NAME", "KevinBeLLM Admin").strip()
    password = os.getenv("ADMIN_PASSWORD", "")
    if not 1 <= len(name) <= 100:
        raise RuntimeError("ADMIN_NAME must be 1 to 100 characters")
    if not 14 <= len(password) <= 256:
        raise RuntimeError("ADMIN_PASSWORD must be 14 to 256 characters")
    password_hash = await hash_password(password)
    if not await database.bootstrap_user(email, name, password_hash):
        # Idempotent setup must not reveal account fields or replace credentials.
        print("Administrator already exists; no changes made.")
        return 0
    print("Initial administrator created. Remove bootstrap credentials from runtime environment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_bootstrap()))
