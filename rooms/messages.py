"""The only module that reads or writes the messages table.

Keeping every message read and write here means end-to-end encryption (or any
other change to how bodies are stored) can be added in one place later. Callers
pass a sqlite3 connection they already hold the database lock for.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

_SELECT = """
SELECT m.id, m.room_id, m.thread_id, m.author_user_id, m.author_agent_id,
       m.body, m.mentions, m.system, m.created_at,
       u.name AS user_name, a.name AS agent_name
FROM messages m
JOIN users u ON u.id = m.author_user_id
LEFT JOIN agents a ON a.id = m.author_agent_id
"""


def _row(r: sqlite3.Row) -> dict[str, Any]:
    try:
        mentions = json.loads(r["mentions"] or "[]")
    except ValueError:
        mentions = []
    return {
        "id": r["id"],
        "room_id": r["room_id"],
        "thread_id": r["thread_id"],
        "author_user_id": r["author_user_id"],
        "author_agent_id": r["author_agent_id"],
        "body": r["body"],
        "mentions": mentions,
        "system": bool(r["system"]),
        "created_at": r["created_at"],
        "user_name": r["user_name"],
        "agent_name": r["agent_name"],
    }


def insert(
    conn: sqlite3.Connection,
    *,
    room_id: str,
    thread_id: int | None,
    author_user_id: str,
    author_agent_id: int | None,
    body: str,
    mentions: list[str],
    created_at: int,
    system: bool = False,
) -> int:
    cur = conn.execute(
        "INSERT INTO messages (room_id, thread_id, author_user_id, author_agent_id, body,"
        " mentions, system, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            room_id,
            thread_id,
            author_user_id,
            author_agent_id,
            body,
            json.dumps(mentions),
            1 if system else 0,
            created_at,
        ),
    )
    return int(cur.lastrowid)


def get(conn: sqlite3.Connection, room_id: str, message_id: int) -> dict[str, Any] | None:
    r = conn.execute(_SELECT + " WHERE m.room_id = ? AND m.id = ?", (room_id, message_id)).fetchone()
    return _row(r) if r else None


def list_after(
    conn: sqlite3.Connection,
    room_id: str,
    after_id: int,
    *,
    thread_id: int | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Messages in one room with id > after_id, oldest first."""
    sql = _SELECT + " WHERE m.room_id = ? AND m.id > ?"
    params: list[Any] = [room_id, after_id]
    if thread_id is not None:
        sql += " AND (m.id = ? OR m.thread_id = ?)"
        params += [thread_id, thread_id]
    sql += " ORDER BY m.id ASC LIMIT ?"
    params.append(limit)
    return [_row(r) for r in conn.execute(sql, params).fetchall()]


def latest(conn: sqlite3.Connection, room_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """The newest `limit` messages of a room, oldest first."""
    rows = conn.execute(
        _SELECT + " WHERE m.room_id = ? ORDER BY m.id DESC LIMIT ?", (room_id, limit)
    ).fetchall()
    return [_row(r) for r in reversed(rows)]


def _not_mine(agent_id: int | None, user_id: str | None) -> tuple[str, list[Any]]:
    """SQL filter: drop messages written by this reader. Agents also skip system notes
    (from Rooms, for people): a note must not wake listeners or count as unread."""
    if agent_id is not None:
        return " AND m.system = 0 AND (m.author_agent_id IS NULL OR m.author_agent_id != ?)", [agent_id]
    if user_id is not None:
        return " AND NOT (m.author_agent_id IS NULL AND m.author_user_id = ?)", [user_id]
    return "", []


def from_others(
    conn: sqlite3.Connection,
    after_by_room: dict[str, int],
    *,
    agent_id: int | None = None,
    user_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """New messages across rooms (id > that room's cursor), not written by the reader.

    For an agent reader pass agent_id; for a human reader pass user_id.
    Oldest first across all rooms.
    """
    if not after_by_room:
        return []
    clauses = " OR ".join("(m.room_id = ? AND m.id > ?)" for _ in after_by_room)
    params: list[Any] = []
    for room_id, after in after_by_room.items():
        params += [room_id, after]
    mine_sql, mine_params = _not_mine(agent_id, user_id)
    sql = _SELECT + f" WHERE ({clauses})" + mine_sql + " ORDER BY m.id ASC LIMIT ?"
    return [_row(r) for r in conn.execute(sql, params + mine_params + [limit]).fetchall()]


def count_from_others(
    conn: sqlite3.Connection,
    room_id: str,
    after_id: int,
    *,
    agent_id: int | None = None,
    user_id: str | None = None,
) -> int:
    mine_sql, mine_params = _not_mine(agent_id, user_id)
    r = conn.execute(
        "SELECT COUNT(*) FROM messages m WHERE m.room_id = ? AND m.id > ?" + mine_sql,
        [room_id, after_id, *mine_params],
    ).fetchone()
    return int(r[0])


def first_recent_from_others(
    conn: sqlite3.Connection,
    room_id: str,
    after_id: int,
    since: int,
    *,
    agent_id: int | None = None,
    user_id: str | None = None,
    skip_thread: int | None = None,
) -> int | None:
    """Oldest message id after `after_id`, created at or after `since`, not by the reader,
    and outside `skip_thread` (root id). None when there is none."""
    mine_sql, mine_params = _not_mine(agent_id, user_id)
    sql = "SELECT MIN(m.id) FROM messages m WHERE m.room_id = ? AND m.id > ? AND m.created_at >= ?"
    params: list[Any] = [room_id, after_id, since]
    if skip_thread is not None:
        sql += " AND m.id != ? AND (m.thread_id IS NULL OR m.thread_id != ?)"
        params += [skip_thread, skip_thread]
    r = conn.execute(sql + mine_sql, params + mine_params).fetchone()
    return int(r[0]) if r and r[0] is not None else None


def agent_count_since(conn: sqlite3.Connection, room_id: str, since: int) -> int:
    """Agent-written messages in a room created at or after `since` (unix seconds)."""
    r = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE room_id = ? AND author_agent_id IS NOT NULL"
        " AND created_at >= ?",
        (room_id, since),
    ).fetchone()
    return int(r[0])


def agent_created_at(conn: sqlite3.Connection, room_id: str, since: int, offset: int) -> int | None:
    """created_at of the agent message at position `offset` (0 = oldest) at or after `since`."""
    r = conn.execute(
        "SELECT created_at FROM messages WHERE room_id = ? AND author_agent_id IS NOT NULL"
        " AND created_at >= ? ORDER BY created_at, id LIMIT 1 OFFSET ?",
        (room_id, since, max(0, offset)),
    ).fetchone()
    return int(r[0]) if r else None


def last_id(conn: sqlite3.Connection, room_id: str) -> int:
    r = conn.execute("SELECT MAX(id) FROM messages WHERE room_id = ?", (room_id,)).fetchone()
    return int(r[0] or 0)


def purge_older_than(conn: sqlite3.Connection, room_id: str, cutoff: int) -> int:
    """Delete a room's messages created before `cutoff` (unix seconds)."""
    cur = conn.execute("DELETE FROM messages WHERE room_id = ? AND created_at < ?", (room_id, cutoff))
    return cur.rowcount or 0

