# SPDX-License-Identifier: AGPL-3.0-or-later
"""Minimal SQLite persistence for the single administrator and sessions."""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator

import aiosqlite

from .security import new_session_token, session_digest


@dataclass(frozen=True, slots=True)
class User:
    id: int
    email: str
    name: str
    password_hash: str | None = None


class Database:
    def __init__(
        self, data_dir: Path, session_ttl_seconds: int, concurrency: int = 8
    ):
        self.data_dir = data_dir
        self.path = data_dir / "assistant.sqlite3"
        self.session_ttl_seconds = session_ttl_seconds
        self._slots = asyncio.Semaphore(concurrency)

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._slots:
            database = await aiosqlite.connect(self.path, timeout=10)
            try:
                database.row_factory = aiosqlite.Row
                await database.execute("PRAGMA foreign_keys = ON")
                await database.execute("PRAGMA busy_timeout = 10000")
                yield database
            finally:
                await database.close()

    async def initialize(self) -> None:
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
        except OSError:
            pass

        async with self._connect() as database:
            await database.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA synchronous = NORMAL;
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash BLOB PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user_id_idx ON sessions(user_id);
                CREATE INDEX IF NOT EXISTS sessions_expires_at_idx ON sessions(expires_at);
                """
            )
            await database.commit()

        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    async def user_count(self) -> int:
        async with self._connect() as database:
            cursor = await database.execute("SELECT COUNT(*) AS count FROM users")
            row = await cursor.fetchone()
        return int(row["count"] if row else 0)

    async def bootstrap_user(self, email: str, name: str, password_hash: str) -> bool:
        """Create the only initial account atomically; never overwrite an account."""
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute("SELECT COUNT(*) AS count FROM users")
            row = await cursor.fetchone()
            if row and row["count"]:
                await database.rollback()
                return False
            await database.execute(
                "INSERT INTO users(email, name, password_hash, created_at) VALUES(?, ?, ?, ?)",
                (email, name, password_hash, int(time.time())),
            )
            await database.commit()
        return True

    async def user_for_login(self, email: str) -> User | None:
        async with self._connect() as database:
            cursor = await database.execute(
                "SELECT id, email, name, password_hash FROM users WHERE email = ?",
                (email,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return User(
            id=row["id"],
            email=row["email"],
            name=row["name"],
            password_hash=row["password_hash"],
        )

    async def create_session(self, user_id: int) -> str:
        token = new_session_token()
        now = int(time.time())
        expires_at = now + self.session_ttl_seconds
        async with self._connect() as database:
            await database.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            # Keep the session table bounded even if one user's browser repeatedly logs in.
            await database.execute(
                """
                DELETE FROM sessions
                WHERE user_id = ? AND token_hash NOT IN (
                    SELECT token_hash FROM sessions WHERE user_id = ?
                    ORDER BY created_at DESC LIMIT 9
                )
                """,
                (user_id, user_id),
            )
            await database.execute(
                "INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?, ?, ?, ?)",
                (session_digest(token), user_id, now, expires_at),
            )
            await database.commit()
        return token

    async def user_for_session(self, token: str) -> User | None:
        now = int(time.time())
        async with self._connect() as database:
            cursor = await database.execute(
                """
                SELECT users.id, users.email, users.name
                FROM sessions JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (session_digest(token), now),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return User(id=row["id"], email=row["email"], name=row["name"])

    async def delete_session(self, token: str) -> None:
        async with self._connect() as database:
            await database.execute(
                "DELETE FROM sessions WHERE token_hash = ?", (session_digest(token),)
            )
            await database.commit()

    async def change_password(self, user_id: int, password_hash: str) -> None:
        """Atomically rotate the hash and revoke every session for the user."""
        async with self._connect() as database:
            await database.execute("BEGIN IMMEDIATE")
            cursor = await database.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )
            if cursor.rowcount != 1:
                await database.rollback()
                raise RuntimeError("User no longer exists")
            await database.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            await database.commit()
