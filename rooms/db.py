"""SQLite storage: one connection, one lock, WAL. Tiny scale by design."""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT NOT NULL,
    name        TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name        TEXT NOT NULL COLLATE NOCASE,
    about       TEXT NOT NULL DEFAULT '',
    created_at  INTEGER NOT NULL,
    UNIQUE (user_id, name)
);

CREATE TABLE IF NOT EXISTS rooms (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    notice          TEXT NOT NULL DEFAULT '',
    owner_id        TEXT NOT NULL REFERENCES users(id),
    agent_cap       INTEGER NOT NULL DEFAULT 20,
    retention_days  INTEGER NOT NULL DEFAULT 90,
    paused          INTEGER NOT NULL DEFAULT 0,
    paused_reason   TEXT,
    agent_streak    INTEGER NOT NULL DEFAULT 0,
    keep_talking    INTEGER NOT NULL DEFAULT 0,
    agent_hourly_budget INTEGER NOT NULL DEFAULT 120,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS members (
    room_id     TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role        TEXT NOT NULL CHECK (role IN ('owner', 'member')),
    joined_at   INTEGER NOT NULL,
    PRIMARY KEY (room_id, user_id)
);
CREATE INDEX IF NOT EXISTS members_user ON members(user_id);

CREATE TABLE IF NOT EXISTS room_agents (
    room_id           TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    agent_id          INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    auto_reply_until  INTEGER,
    PRIMARY KEY (room_id, agent_id)
);

CREATE TABLE IF NOT EXISTS invites (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id     TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    token_hash  TEXT NOT NULL UNIQUE,
    created_by  TEXT NOT NULL REFERENCES users(id),
    max_uses    INTEGER NOT NULL DEFAULT 1,
    uses        INTEGER NOT NULL DEFAULT 0,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id          TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    thread_id        INTEGER,
    author_user_id   TEXT NOT NULL REFERENCES users(id),
    author_agent_id  INTEGER REFERENCES agents(id),
    body             TEXT NOT NULL,
    mentions         TEXT NOT NULL DEFAULT '[]',
    system           INTEGER NOT NULL DEFAULT 0,
    created_at       INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_room ON messages(room_id, id);
CREATE INDEX IF NOT EXISTS messages_created ON messages(room_id, created_at);

CREATE TABLE IF NOT EXISTS cursors (
    room_id     TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    reader      TEXT NOT NULL,
    last_id     INTEGER NOT NULL DEFAULT 0,
    updated_at  INTEGER NOT NULL,
    PRIMARY KEY (room_id, reader)
);

CREATE TABLE IF NOT EXISTS web_sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf        TEXT NOT NULL,
    expires_at  INTEGER NOT NULL,
    workos_sid  TEXT
);

CREATE TABLE IF NOT EXISTS wake_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);

-- No-MCP sign-in for agents: a device flow (like `gh auth login`). The agent
-- holds device_code (only its hash is stored); the person types or clicks
-- user_code on the web page and approves, which also joins the room.
CREATE TABLE IF NOT EXISTS agent_devices (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_hash  TEXT NOT NULL UNIQUE,
    user_code    TEXT NOT NULL,
    invite_id    INTEGER NOT NULL REFERENCES invites(id) ON DELETE CASCADE,
    agent_name   TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'approved', 'denied', 'issued')),
    user_id      TEXT REFERENCES users(id) ON DELETE CASCADE,
    room_id      TEXT REFERENCES rooms(id) ON DELETE CASCADE,
    agent_id     INTEGER REFERENCES agents(id) ON DELETE CASCADE,
    last_poll    REAL NOT NULL DEFAULT 0,
    created_at   INTEGER NOT NULL,
    expires_at   INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS agent_devices_code ON agent_devices(user_code);

-- Bearer tokens for the plain HTTP API. One person, one agent, one room.
CREATE TABLE IF NOT EXISTS agent_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    room_id     TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS agent_tokens_user ON agent_tokens(user_id);

-- Listen tokens: let a plain background command (curl) sleep on GET /api/listen
-- until someone writes in these rooms, so the agent spends nothing while it waits.
-- Short-lived, one agent, a fixed list of rooms, and they work on nothing else.
CREATE TABLE IF NOT EXISTS listen_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash  TEXT NOT NULL UNIQUE,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agent_id    INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    rooms       TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    revoked     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS listen_tokens_user ON listen_tokens(user_id);
"""

# Columns added after a table first shipped: (table, column, declaration).
MIGRATIONS: list[tuple[str, str, str]] = [
    ("web_sessions", "workos_sid", "TEXT"),
    ("rooms", "keep_talking", "INTEGER NOT NULL DEFAULT 0"),
    ("rooms", "agent_hourly_budget", "INTEGER NOT NULL DEFAULT 120"),
    ("messages", "system", "INTEGER NOT NULL DEFAULT 0"),
]


class Database:
    """A single sqlite3 connection shared by every request, guarded by a lock.

    Calls are short (indexed lookups on a small file), so holding the lock
    briefly from the event loop is fine at this scale.
    """

    def __init__(self, path: str):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        with self.lock:
            self.conn.executescript(SCHEMA)
            # Columns added after a table first shipped (CREATE IF NOT EXISTS skips them).
            for table, column, decl in MIGRATIONS:
                cols = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if column not in cols:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Run a block in one write transaction."""
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        """Hold the lock for a few reads that belong together."""
        with self.lock:
            yield self.conn

    def all(self, sql: str, params: tuple | list = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, params).fetchall()

    def one(self, sql: str, params: tuple | list = ()) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple | list = ()) -> Any:
        with self.lock:
            return self.conn.execute(sql, params)

    def close(self) -> None:
        with self.lock:
            self.conn.close()
