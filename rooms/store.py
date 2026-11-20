"""Domain logic. Every function that touches a room takes the acting user and
checks membership. A non-member and a missing room get the same NotFound.
"""

from __future__ import annotations

import hashlib
import re
import json
import secrets
import sqlite3
import string
import threading
import time
import unicodedata
from collections import defaultdict, deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from . import messages
from .db import Database

BODY_MAX = 8000
RACE_SECONDS = 120  # see send(): recent unseen messages stay unread
READ_LIMIT = 50
ROOM_NAME_MAX = 80
NOTICE_MAX = 4000
AGENT_NAME_MAX = 40
MENTIONS_MAX = 20
SESSION_DAYS = 14
AUTO_REPLY_HOURS = (0, 1, 8, 24)
DEFAULT_AGENT_CAP = 20
AGENT_CAP_MAX = 500
# With "agents keep talking" on, the loop cap no longer pauses the room; this many agent
# messages per room per rolling hour is the backstop instead.
DEFAULT_HOURLY_BUDGET = 120
HOURLY_BUDGET_MAX = 5000
SYSTEM_AUTHOR = "Rooms"
DEFAULT_RETENTION_DAYS = 90
NAME_MAX = 60
MAX_MEMBERSHIPS = 100
MAX_OWNED_ROOMS = 50
MAX_WAKE_TOKENS = 10
MAX_LISTEN_TOKENS = 20  # live listen tokens per person
LISTEN_HOURS_MAX = 24
# No-MCP agent sign-in (device flow) and its HTTP API tokens.
DEVICE_TTL = 600  # seconds a device code / user code lives
DEVICE_INTERVAL = 5  # seconds between token polls
MAX_PENDING_DEVICES = 500
MAX_PENDING_PER_INVITER = 20
AGENT_TOKEN_DAYS = 30
MAX_AGENT_TOKENS = 20
_USER_CODE_ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"  # no vowels: no accidental words; no 0/O, 1/I
# action -> (max events, window in seconds), per user (or per key, for the
# unauthenticated agent start: per invite, and for everyone together)
DEFAULT_LIMITS: dict[str, tuple[int, int]] = {
    "send": (30, 60),
    "room_create": (10, 3600),
    "invite": (20, 3600),
    "agent_start": (20, 3600),
    "agent_start_owner": (60, 3600),
    "device_view": (30, 60),
}

_AGENT_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,40}$")
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_.\-]{0,39})")
_ROOM_ID_ALPHABET = string.ascii_lowercase + string.digits


class RoomsError(Exception):
    """An error whose message is safe to show to the caller."""


class NotFound(RoomsError):
    pass


class Forbidden(RoomsError):
    pass


class Invalid(RoomsError):
    pass


class RateLimited(RoomsError):
    pass


class BudgetReached(RateLimited):
    """A room with agents keep talking on hit its hourly agent message budget."""

    def __init__(self, message: str, next_hint: str, retry_after: int):
        super().__init__(message)
        self.next = next_hint
        self.retry_after = retry_after


ROOM_NOT_FOUND = "room not found"
INVITE_NOT_VALID = "invite not valid"
DEVICE_NOT_FOUND = "this code expired or does not exist; ask your agent to start again"
DEVICE_USED = "this code was already used"


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def iso(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), UTC).isoformat().replace("+00:00", "Z")


# Characters that could fake the "Agent · Name #tag" layout, after NFKC.
_NAME_STRIP = set("·#@\u2022\u2027\u2219\u22c5\u30fb\u0387\u16eb\u2e31\u2e33")


def clean_display_name(name: str | None) -> str:
    """A person's display name, safe to show next to the author tag.

    NFKC folds full-width look-alikes; then control, format (bidi, zero-width),
    line/paragraph separator and private-use characters go, as do '·', '#',
    '@' and middle-dot look-alikes. Whitespace collapses; max 60 characters.
    """
    value = unicodedata.normalize("NFKC", name or "")
    kept = []
    for ch in value:
        if ch in _NAME_STRIP:
            kept.append(" ")
            continue
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"):
            kept.append(" " if cat in ("Cc", "Zl", "Zp") else "")
            continue
        kept.append(" " if ch.isspace() else ch)
    value = " ".join("".join(kept).split())[:NAME_MAX].strip()
    return value or "someone"


def user_tag(user_id: str) -> str:
    """4 stable hex chars from the user id, so two people named alike differ."""
    return hashlib.sha256(user_id.encode()).hexdigest()[:4]


def person_label(name: str, user_id: str) -> str:
    return f"{name} #{user_tag(user_id)}"


def author_label(m: dict[str, Any]) -> str:
    if m.get("system"):
        return SYSTEM_AUTHOR
    person = person_label(m["user_name"], m["author_user_id"])
    if m["author_agent_id"] is None:
        return person
    return f"{m['agent_name']} · {person}"


def public_message(m: dict[str, Any]) -> dict[str, Any]:
    """The message shape every client sees."""
    return {
        "id": m["id"],
        "room": m["room_id"],
        "thread": m["thread_id"],
        "author": author_label(m),
        "kind": "system" if m.get("system") else "human" if m["author_agent_id"] is None else "agent",
        "body": m["body"],
        "mentions": m["mentions"],
        "at": iso(m["created_at"]),
    }


def clean_agent_name(name: str | None) -> str:
    value = " ".join((name or "").split())
    if not _AGENT_NAME_RE.match(value):
        raise Invalid(
            "agent name must be 1-40 characters: letters, digits, spaces, '-', '_' or '.'"
        )
    return value


def parse_mentions(body: str, explicit: list[str] | None = None) -> list[str]:
    seen: dict[str, str] = {}
    candidates = [m.rstrip(".") for m in _MENTION_RE.findall(body)]
    for raw in explicit or []:
        candidates.append(str(raw).strip().lstrip("@"))
    for name in candidates:
        name = " ".join(name.split())
        if not name or len(name) > AGENT_NAME_MAX:
            continue
        seen.setdefault(name.lower(), name)
        if len(seen) >= MENTIONS_MAX:
            break
    return list(seen.values())


def safe_next(value: str | None) -> str:
    """Only same-site relative paths: starts with '/', not '//' or '/\\'. Else the room list."""
    if not value or not isinstance(value, str):
        return "/rooms"
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/rooms"
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return "/rooms"
    return value


def _room_id() -> str:
    return "r_" + "".join(secrets.choice(_ROOM_ID_ALPHABET) for _ in range(10))


class RateLimiter:
    """Per-user sliding windows, in memory (one process)."""

    def __init__(self, clock: Callable[[], float]):
        self.clock = clock
        self.lock = threading.Lock()
        self.hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def hit(self, user_id: str, action: str, limit: int, window: int) -> float | None:
        """Record one event. Returns seconds to wait if over the limit (not recorded)."""
        now = self.clock()
        key = (user_id, action)
        with self.lock:
            q = self.hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return max(1.0, q[0] + window - now)
            q.append(now)
            return None


_LIMIT_TEXT = {
    "send": "too many messages",
    "room_create": "too many new rooms",
    "invite": "too many invites",
    "agent_start": "too many agent sign-ins for this invite",
    "agent_start_owner": "too many agent sign-ins for this person's invites",
    "device_view": "too many code lookups",
}


class Store:
    def __init__(self, db: Database, clock: Callable[[], float] = time.time):
        self.db = db
        self.clock = clock
        self.limits: dict[str, tuple[int, int]] = dict(DEFAULT_LIMITS)
        self.limiter = RateLimiter(clock)

    def _rate(self, user_id: str, action: str) -> None:
        limit, window = self.limits[action]
        wait = self.limiter.hit(user_id, action, limit, window)
        if wait is not None:
            per = "minute" if window == 60 else "hour" if window == 3600 else f"{window} s"
            raise RateLimited(
                f"{_LIMIT_TEXT[action]}: at most {limit} per {per}. Try again in {int(wait + 0.999)} s."
            )

    def now(self) -> int:
        return int(self.clock())

    # ---- users -------------------------------------------------------

    def upsert_user(self, user_id: str, email: str, name: str) -> dict[str, Any]:
        name = clean_display_name(name or email.split("@", 1)[0])
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO users (id, email, name, created_at) VALUES (?, ?, ?, ?)"
                " ON CONFLICT(id) DO UPDATE SET email = excluded.email, name = excluded.name",
                (user_id, email.lower(), name, self.now()),
            )
        return {"id": user_id, "email": email.lower(), "name": name}

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        r = self.db.one("SELECT id, email, name FROM users WHERE id = ?", (user_id,))
        return dict(r) if r else None

    def ensure_user(self, user_id: str, email: str, name: str) -> dict[str, Any]:
        """Return the user row, writing only when it is new or changed."""
        name = clean_display_name(name or email.split("@", 1)[0])
        current = self.get_user(user_id)
        if current and current["email"] == email.lower() and current["name"] == name:
            return current
        return self.upsert_user(user_id, email, name)

    # ---- agents ------------------------------------------------------

    def _agent(self, c: sqlite3.Connection, user_id: str, name: str, create: bool) -> dict[str, Any]:
        name = clean_agent_name(name)
        r = c.execute(
            "SELECT id, user_id, name FROM agents WHERE user_id = ? AND name = ?", (user_id, name)
        ).fetchone()
        if r:
            return dict(r)
        if not create:
            raise NotFound("agent not found")
        cur = c.execute(
            "INSERT INTO agents (user_id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, self.now()),
        )
        return {"id": int(cur.lastrowid), "user_id": user_id, "name": name}

    def agent(self, user_id: str, name: str, create: bool = True) -> dict[str, Any]:
        """Get (or create) the caller's agent by name. Agents belong to one user."""
        with self.db.tx() as c:
            return self._agent(c, user_id, name, create)

    def my_agents(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.all(
            "SELECT id, user_id, name FROM agents WHERE user_id = ? ORDER BY name", (user_id,)
        )
        return [dict(r) for r in rows]

    def _owned_agent(self, c: sqlite3.Connection, user_id: str, agent_id: int) -> dict[str, Any]:
        r = c.execute(
            "SELECT id, user_id, name FROM agents WHERE id = ? AND user_id = ?", (agent_id, user_id)
        ).fetchone()
        if not r:
            raise NotFound("agent not found")
        return dict(r)

    # ---- rooms -------------------------------------------------------

    def _room(self, c: sqlite3.Connection, user_id: str, room_id: str) -> dict[str, Any]:
        """The room plus the caller's role, or NotFound for non-members and missing rooms."""
        r = c.execute(
            "SELECT r.*, m.role FROM rooms r JOIN members m ON m.room_id = r.id"
            " WHERE r.id = ? AND m.user_id = ?",
            (str(room_id or ""), user_id),
        ).fetchone()
        if not r:
            raise NotFound(ROOM_NOT_FOUND)
        return dict(r)

    def _owner_room(self, c: sqlite3.Connection, user_id: str, room_id: str, action: str) -> dict[str, Any]:
        room = self._room(c, user_id, room_id)
        if room["role"] != "owner":
            raise Forbidden(f"only the room owner can {action}")
        return room

    def room(self, user_id: str, room_id: str) -> dict[str, Any]:
        with self.db.read() as c:
            return self._room(c, user_id, room_id)

    @staticmethod
    def _check_membership_cap(c: sqlite3.Connection, user_id: str) -> None:
        n = c.execute("SELECT COUNT(*) FROM members WHERE user_id = ?", (user_id,)).fetchone()[0]
        if n >= MAX_MEMBERSHIPS:
            raise Invalid(f"you are in {MAX_MEMBERSHIPS} rooms already; leave one first")

    @staticmethod
    def room_summary(room: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": room["id"],
            "name": room["name"],
            "notice": room["notice"],
            "paused": bool(room["paused"]),
            "paused_reason": room["paused_reason"],
            "agent_cap": room["agent_cap"],
            "keep_talking": bool(room["keep_talking"]),
            "agent_hourly_budget": room["agent_hourly_budget"],
            "retention_days": room["retention_days"],
            "role": room.get("role"),
        }

    def create_room(self, user_id: str, name: str, notice: str | None = None) -> dict[str, Any]:
        name = " ".join((name or "").split())
        if not 1 <= len(name) <= ROOM_NAME_MAX:
            raise Invalid(f"room name must be 1-{ROOM_NAME_MAX} characters")
        notice = (notice or "").strip()
        if len(notice) > NOTICE_MAX:
            raise Invalid(f"notice is longer than {NOTICE_MAX} characters")
        now = self.now()
        with self.db.tx() as c:
            owned = c.execute(
                "SELECT COUNT(*) FROM members WHERE user_id = ? AND role = 'owner'", (user_id,)
            ).fetchone()[0]
            if owned >= MAX_OWNED_ROOMS:
                raise Invalid(f"you own {MAX_OWNED_ROOMS} rooms already")
            self._check_membership_cap(c, user_id)
            self._rate(user_id, "room_create")
            room_id = _room_id()
            while c.execute("SELECT 1 FROM rooms WHERE id = ?", (room_id,)).fetchone():
                room_id = _room_id()
            c.execute(
                "INSERT INTO rooms (id, name, notice, owner_id, agent_cap, retention_days, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (room_id, name, notice, user_id, DEFAULT_AGENT_CAP, DEFAULT_RETENTION_DAYS, now),
            )
            c.execute(
                "INSERT INTO members (room_id, user_id, role, joined_at) VALUES (?, ?, 'owner', ?)",
                (room_id, user_id, now),
            )
            return self._room(c, user_id, room_id)

    def list_rooms(self, user_id: str, agent_name: str | None = None) -> list[dict[str, Any]]:
        now = self.now()
        with self.db.tx() as c:
            agent = self._agent(c, user_id, agent_name, True) if agent_name else None
            rooms = c.execute(
                "SELECT r.*, m.role FROM rooms r JOIN members m ON m.room_id = r.id"
                " WHERE m.user_id = ? ORDER BY r.name COLLATE NOCASE",
                (user_id,),
            ).fetchall()
            out = []
            for r in rooms:
                room = dict(r)
                item = self.room_summary(room)
                if agent:
                    cursor = self._cursor(c, room["id"], f"a:{agent['id']}")
                    item["unread"] = messages.count_from_others(c, room["id"], cursor, agent_id=agent["id"])
                else:
                    cursor = self._cursor(c, room["id"], f"u:{user_id}")
                    item["unread"] = messages.count_from_others(c, room["id"], cursor, user_id=user_id)
                item["auto_reply"] = [
                    {"agent": a["name"], "until": iso(a["auto_reply_until"])}
                    for a in c.execute(
                        "SELECT a.name, ra.auto_reply_until FROM room_agents ra"
                        " JOIN agents a ON a.id = ra.agent_id"
                        " WHERE ra.room_id = ? AND a.user_id = ? AND ra.auto_reply_until > ?"
                        " ORDER BY a.name",
                        (room["id"], user_id, now),
                    ).fetchall()
                ]
                out.append(item)
            return out

    def members(self, user_id: str, room_id: str) -> list[dict[str, Any]]:
        with self.db.read() as c:
            self._room(c, user_id, room_id)
            rows = c.execute(
                "SELECT u.id, u.name, u.email, m.role FROM members m JOIN users u ON u.id = m.user_id"
                " WHERE m.room_id = ? ORDER BY m.role DESC, u.name COLLATE NOCASE",
                (room_id,),
            ).fetchall()
            return [dict(r, label=person_label(r["name"], r["id"])) for r in rows]

    def room_agents(self, user_id: str, room_id: str) -> list[dict[str, Any]]:
        """Agents that have taken part in the room, with their auto-reply expiry."""
        now = self.now()
        with self.db.read() as c:
            self._room(c, user_id, room_id)
            rows = c.execute(
                "SELECT a.id, a.name, a.user_id, u.name AS owner, ra.auto_reply_until"
                " FROM room_agents ra JOIN agents a ON a.id = ra.agent_id"
                " JOIN users u ON u.id = a.user_id"
                " JOIN members m ON m.room_id = ra.room_id AND m.user_id = a.user_id"
                " WHERE ra.room_id = ? ORDER BY u.name COLLATE NOCASE, a.name",
                (room_id,),
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                until = d["auto_reply_until"]
                d["auto_reply_until"] = until if until and until > now else None
                d["label"] = f"{d['name']} · {person_label(d['owner'], d['user_id'])}"
                out.append(d)
            return out

    def update_room(
        self,
        user_id: str,
        room_id: str,
        *,
        notice: str | None = None,
        agent_cap: int | None = None,
        retention_days: int | None = None,
        keep_talking: bool | None = None,
        agent_hourly_budget: int | None = None,
    ) -> dict[str, Any]:
        with self.db.tx() as c:
            self._owner_room(c, user_id, room_id, "change room settings")
            if notice is not None:
                notice = notice.strip()
                if len(notice) > NOTICE_MAX:
                    raise Invalid(f"notice is longer than {NOTICE_MAX} characters")
                c.execute("UPDATE rooms SET notice = ? WHERE id = ?", (notice, room_id))
            if agent_cap is not None:
                if not 1 <= int(agent_cap) <= AGENT_CAP_MAX:
                    raise Invalid(f"agent cap must be between 1 and {AGENT_CAP_MAX}")
                c.execute("UPDATE rooms SET agent_cap = ? WHERE id = ?", (int(agent_cap), room_id))
            if retention_days is not None:
                if not 1 <= int(retention_days) <= 3650:
                    raise Invalid("retention must be between 1 and 3650 days")
                c.execute(
                    "UPDATE rooms SET retention_days = ? WHERE id = ?", (int(retention_days), room_id)
                )
            if agent_hourly_budget is not None:
                if not 1 <= int(agent_hourly_budget) <= HOURLY_BUDGET_MAX:
                    raise Invalid(f"hourly agent budget must be between 1 and {HOURLY_BUDGET_MAX}")
                c.execute(
                    "UPDATE rooms SET agent_hourly_budget = ? WHERE id = ?",
                    (int(agent_hourly_budget), room_id),
                )
            if keep_talking is not None:
                c.execute(
                    "UPDATE rooms SET keep_talking = ? WHERE id = ?", (1 if keep_talking else 0, room_id)
                )
            return self._room(c, user_id, room_id)

    def pause(self, user_id: str, room_id: str, reason: str | None = None) -> None:
        user = self.get_user(user_id)
        reason = (reason or "").strip()[:200] or f"paused by {user['name'] if user else 'a member'}"
        with self.db.tx() as c:
            self._room(c, user_id, room_id)
            c.execute("UPDATE rooms SET paused = 1, paused_reason = ? WHERE id = ?", (reason, room_id))

    def resume(self, user_id: str, room_id: str, *, owner_only: bool = False) -> dict[str, Any]:
        """Unpause and reset the streak. Any member may on the web page; agents (MCP) need
        the owner, so one member's agent cannot undo a pause on its own."""
        with self.db.tx() as c:
            if owner_only:
                self._owner_room(c, user_id, room_id, "resume the room")
            else:
                self._room(c, user_id, room_id)
            c.execute(
                "UPDATE rooms SET paused = 0, paused_reason = NULL, agent_streak = 0 WHERE id = ?",
                (room_id,),
            )
            return self._room(c, user_id, room_id)

    def leave(self, user_id: str, room_id: str) -> None:
        with self.db.tx() as c:
            room = self._room(c, user_id, room_id)
            if room["role"] == "owner":
                raise Invalid("the owner cannot leave the room")
            c.execute("DELETE FROM members WHERE room_id = ? AND user_id = ?", (room_id, user_id))
            agent_ids = [
                r[0] for r in c.execute("SELECT id FROM agents WHERE user_id = ?", (user_id,)).fetchall()
            ]
            readers = [f"u:{user_id}"] + [f"a:{a}" for a in agent_ids]
            for agent_id in agent_ids:
                c.execute("DELETE FROM room_agents WHERE room_id = ? AND agent_id = ?", (room_id, agent_id))
            for reader in readers:
                c.execute("DELETE FROM cursors WHERE room_id = ? AND reader = ?", (room_id, reader))
            # Listen tokens that cover this room end too (they cannot outlive membership).
            for r in c.execute(
                "SELECT id, rooms FROM listen_tokens WHERE user_id = ? AND revoked = 0", (user_id,)
            ).fetchall():
                if room_id in json.loads(r["rooms"]):
                    c.execute("UPDATE listen_tokens SET revoked = 1 WHERE id = ?", (r["id"],))

    # ---- invites -----------------------------------------------------

    def create_invite(
        self, user_id: str, room_id: str, uses: int = 1, expires_hours: float = 72
    ) -> tuple[str, dict[str, Any]]:
        """Returns (plaintext token, invite row). Only the hash is stored."""
        uses = int(uses)
        if not 1 <= uses <= 1000:
            raise Invalid("uses must be between 1 and 1000")
        expires_hours = float(expires_hours)
        if not 0 < expires_hours <= 24 * 30:
            raise Invalid("expires_hours must be more than 0 and at most 720 (30 days)")
        token = secrets.token_urlsafe(24)
        now = self.now()
        with self.db.tx() as c:
            self._owner_room(c, user_id, room_id, "create invites")
            self._rate(user_id, "invite")
            cur = c.execute(
                "INSERT INTO invites (room_id, token_hash, created_by, max_uses, uses, expires_at,"
                " revoked, created_at) VALUES (?, ?, ?, ?, 0, ?, 0, ?)",
                (room_id, sha256(token), user_id, uses, now + int(expires_hours * 3600), now),
            )
            row = dict(c.execute("SELECT * FROM invites WHERE id = ?", (cur.lastrowid,)).fetchone())
        return token, row

    def list_invites(self, user_id: str, room_id: str) -> list[dict[str, Any]]:
        """Invites that still work (owner only)."""
        with self.db.read() as c:
            self._owner_room(c, user_id, room_id, "see invites")
            rows = c.execute(
                "SELECT id, max_uses, uses, expires_at, created_at FROM invites"
                " WHERE room_id = ? AND revoked = 0 AND uses < max_uses AND expires_at > ?"
                " ORDER BY id DESC",
                (room_id, self.now()),
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_invite(self, user_id: str, room_id: str, invite_id: int) -> None:
        with self.db.tx() as c:
            self._owner_room(c, user_id, room_id, "revoke invites")
            cur = c.execute(
                "UPDATE invites SET revoked = 1 WHERE id = ? AND room_id = ?", (int(invite_id), room_id)
            )
            if not cur.rowcount:
                raise NotFound("invite not found")

    @staticmethod
    def invite_token(value: str) -> str:
        """Accept a full link (…/join/<token>) or a bare token."""
        value = (value or "").strip()
        if "/join/" in value:
            value = value.split("/join/", 1)[1]
        return value.split("?", 1)[0].split("#", 1)[0].strip("/ ")

    def _valid_invite(self, c: sqlite3.Connection, token: str, allow_used: bool = False) -> dict[str, Any]:
        token = self.invite_token(token)
        if not token:
            raise Invalid(INVITE_NOT_VALID)
        r = c.execute(
            "SELECT i.*, r.name AS room_name, u.name AS inviter FROM invites i"
            " JOIN rooms r ON r.id = i.room_id JOIN users u ON u.id = i.created_by"
            " WHERE i.token_hash = ?",
            (sha256(token),),
        ).fetchone()
        if not r or r["revoked"] or r["expires_at"] <= self.now():
            raise Invalid(INVITE_NOT_VALID)
        if r["uses"] >= r["max_uses"] and not allow_used:
            raise Invalid(INVITE_NOT_VALID)
        return dict(r)

    def peek_invite(self, token: str) -> dict[str, Any]:
        with self.db.read() as c:
            inv = self._valid_invite(c, token)
            return {
                "room_id": inv["room_id"],
                "room_name": inv["room_name"],
                "inviter": person_label(inv["inviter"], inv["created_by"]),
            }

    def join(self, user_id: str, token: str) -> dict[str, Any]:
        """Use an invite. Already a member: no use is spent."""
        with self.db.tx() as c:
            return self._join(c, user_id, self._valid_invite(c, token))

    def invite_alive(self, token: str) -> bool:
        """Not expired and not revoked (uses may be spent: members can still use it
        to connect an agent). For the agent steps on GET /join."""
        try:
            with self.db.read() as c:
                self._valid_invite(c, token, allow_used=True)
            return True
        except Invalid:
            return False

    def _invite_by_id(self, c: sqlite3.Connection, invite_id: int, allow_used: bool = False) -> dict[str, Any]:
        """The same validity rules as _valid_invite, for an invite we hold by id."""
        r = c.execute(
            "SELECT i.*, r.name AS room_name, u.name AS inviter FROM invites i"
            " JOIN rooms r ON r.id = i.room_id JOIN users u ON u.id = i.created_by"
            " WHERE i.id = ?",
            (int(invite_id),),
        ).fetchone()
        if not r or r["revoked"] or r["expires_at"] <= self.now():
            raise Invalid(INVITE_NOT_VALID)
        if r["uses"] >= r["max_uses"] and not allow_used:
            raise Invalid(INVITE_NOT_VALID)
        return dict(r)

    def _join(self, c: sqlite3.Connection, user_id: str, inv: dict[str, Any]) -> dict[str, Any]:
        room_id = inv["room_id"]
        already = c.execute(
            "SELECT 1 FROM members WHERE room_id = ? AND user_id = ?", (room_id, user_id)
        ).fetchone()
        if not already:
            self._check_membership_cap(c, user_id)
            cur = c.execute(
                "UPDATE invites SET uses = uses + 1 WHERE id = ? AND uses < max_uses AND revoked = 0",
                (inv["id"],),
            )
            if not cur.rowcount:
                raise Invalid(INVITE_NOT_VALID)
            c.execute(
                "INSERT INTO members (room_id, user_id, role, joined_at) VALUES (?, ?, 'member', ?)",
                (room_id, user_id, self.now()),
            )
        return self._room(c, user_id, room_id)

    # ---- messages ----------------------------------------------------

    @staticmethod
    def _cursor(c: sqlite3.Connection, room_id: str, reader: str) -> int:
        r = c.execute(
            "SELECT last_id FROM cursors WHERE room_id = ? AND reader = ?", (room_id, reader)
        ).fetchone()
        return int(r[0]) if r else 0

    def _advance(self, c: sqlite3.Connection, room_id: str, reader: str, last_id: int) -> None:
        c.execute(
            "INSERT INTO cursors (room_id, reader, last_id, updated_at) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(room_id, reader) DO UPDATE SET"
            " last_id = MAX(last_id, excluded.last_id), updated_at = excluded.updated_at",
            (room_id, reader, last_id, self.now()),
        )

    def _touch_agent(self, c: sqlite3.Connection, room_id: str, agent_id: int) -> None:
        c.execute(
            "INSERT OR IGNORE INTO room_agents (room_id, agent_id, auto_reply_until) VALUES (?, ?, NULL)",
            (room_id, agent_id),
        )

    def send(
        self,
        user_id: str,
        room_id: str,
        body: str,
        *,
        agent_name: str | None = None,
        thread: int | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """Post a message. agent_name=None posts as the person (web page only)."""
        body = body if isinstance(body, str) else ""
        if not body.strip():
            raise Invalid("message body is empty")
        if len(body) > BODY_MAX:
            raise Invalid(f"message body is longer than {BODY_MAX} characters")
        paused_now: str | None = None
        with self.db.tx() as c:
            room = self._room(c, user_id, room_id)
            agent = self._agent(c, user_id, agent_name, True) if agent_name is not None else None
            self._rate(user_id, "send")
            thread_id = None
            if thread is not None:
                parent = messages.get(c, room_id, int(thread))
                if not parent:
                    raise NotFound("thread message not found in this room")
                thread_id = parent["thread_id"] or parent["id"]
            if agent:
                if room["paused"]:
                    raise Invalid(
                        f"room is paused ({room['paused_reason'] or 'paused'}); agents cannot post"
                        " until a person resumes it. Stop and tell your person"
                    )
                if room["keep_talking"]:
                    self._check_budget(c, room)
                    if room["agent_streak"] + 1 > room["agent_cap"]:
                        # Past the loop cap: no pause, start a new stretch and say so once.
                        messages.insert(
                            c,
                            room_id=room_id,
                            thread_id=None,
                            author_user_id=room["owner_id"],
                            author_agent_id=None,
                            body=(
                                f"{room['agent_cap']} agent messages in a row; agents keep talking is on"
                                f" (up to {room['agent_hourly_budget']} agent messages an hour)."
                            ),
                            mentions=[],
                            created_at=self.now(),
                            system=True,
                        )
                        c.execute("UPDATE rooms SET agent_streak = 1 WHERE id = ?", (room_id,))
                    else:
                        c.execute(
                            "UPDATE rooms SET agent_streak = agent_streak + 1 WHERE id = ?", (room_id,)
                        )
                elif room["agent_streak"] + 1 > room["agent_cap"]:
                    paused_now = (
                        f"loop cap: {room['agent_cap']} agent messages in a row without a person"
                    )
                    c.execute(
                        "UPDATE rooms SET paused = 1, paused_reason = ? WHERE id = ?",
                        (paused_now, room_id),
                    )
                else:
                    c.execute(
                        "UPDATE rooms SET agent_streak = agent_streak + 1 WHERE id = ?", (room_id,)
                    )
            else:
                c.execute("UPDATE rooms SET agent_streak = 0 WHERE id = ?", (room_id,))
            if not paused_now:
                msg_id = messages.insert(
                    c,
                    room_id=room_id,
                    thread_id=thread_id,
                    author_user_id=user_id,
                    author_agent_id=agent["id"] if agent else None,
                    body=body,
                    mentions=parse_mentions(body, mentions),
                    created_at=self.now(),
                )
                reader = f"a:{agent['id']}" if agent else f"u:{user_id}"
                if agent:
                    self._touch_agent(c, room_id, agent["id"])
                # Your own message is read by definition, so the cursor moves past it,
                # as before, past older history too. The exception: messages from others
                # that landed in the last RACE_SECONDS (while this one was being written)
                # stay unread, so the next wait still returns them. Messages in the thread
                # being replied to count as seen.
                first = messages.first_recent_from_others(
                    c,
                    room_id,
                    self._cursor(c, room_id, reader),
                    self.now() - RACE_SECONDS,
                    agent_id=agent["id"] if agent else None,
                    user_id=None if agent else user_id,
                    skip_thread=thread_id,
                )
                self._advance(c, room_id, reader, msg_id if first is None else first - 1)
                msg = messages.get(c, room_id, msg_id)
        if paused_now:
            raise Invalid(
                f"message refused and room paused: {paused_now}. A person must post or resume"
                " the room before agents can continue. Stop and tell your person; the room owner"
                " can also turn on agents keep talking."
            )
        return public_message(msg)

    def _check_budget(self, c: sqlite3.Connection, room: dict[str, Any]) -> None:
        """Refuse (without pausing) when agents already sent the hourly budget here."""
        now = self.now()
        since = now - 3600
        budget = room["agent_hourly_budget"]
        used = messages.agent_count_since(c, room["id"], since)
        if used < budget:
            return
        # Sends open again once enough messages age out to drop below the budget (more than
        # one when the owner just lowered it).
        oldest = messages.agent_created_at(c, room["id"], since, used - budget) or now
        wait = max(1, oldest + 3600 - now + 1)
        minutes = max(1, round(wait / 60))
        raise BudgetReached(
            f"message refused: this room's agents sent {budget} messages in the last hour, its"
            f" hourly budget. The room is not paused; a slot frees in about {minutes} min.",
            f"Wait about {minutes} minutes before sending here again (keep listening meanwhile),"
            " or tell your person if this cannot wait.",
            wait,
        )

    def unread_for_agent(self, user_id: str, room_id: str, agent_name: str) -> int:
        """Messages from others this agent has not read yet in one room."""
        with self.db.read() as c:
            self._room(c, user_id, room_id)
            agent = self._agent(c, user_id, agent_name, False)
            cursor = self._cursor(c, room_id, f"a:{agent['id']}")
            return messages.count_from_others(c, room_id, cursor, agent_id=agent["id"])

    def read(
        self,
        user_id: str,
        room_id: str,
        agent_name: str,
        *,
        since: int | None = None,
        thread: int | None = None,
        limit: int = READ_LIMIT,
    ) -> list[dict[str, Any]]:
        """Messages after `since` (default: the agent's cursor). Advances the cursor,
        except for a thread-only read, which would skip other unread messages."""
        limit = max(1, min(int(limit or READ_LIMIT), READ_LIMIT))
        with self.db.tx() as c:
            self._room(c, user_id, room_id)
            agent = self._agent(c, user_id, agent_name, True)
            self._touch_agent(c, room_id, agent["id"])
            reader = f"a:{agent['id']}"
            after = self._cursor(c, room_id, reader) if since is None else max(0, int(since))
            rows = messages.list_after(c, room_id, after, thread_id=thread, limit=limit)
            if rows and thread is None:
                self._advance(c, room_id, reader, rows[-1]["id"])
        return [public_message(m) for m in rows]

    def recent_for_person(self, user_id: str, room_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """The web room page: newest messages, and mark them read for the person."""
        with self.db.tx() as c:
            self._room(c, user_id, room_id)
            rows = messages.latest(c, room_id, limit)
            if rows:
                self._advance(c, room_id, f"u:{user_id}", rows[-1]["id"])
        return [public_message(m) for m in rows]

    def after_for_person(self, user_id: str, room_id: str, after: int) -> list[dict[str, Any]]:
        """The web page's live poll: everything after `after` in one room."""
        with self.db.read() as c:
            self._room(c, user_id, room_id)
            rows = messages.list_after(c, room_id, max(0, int(after)), limit=READ_LIMIT)
        if rows:  # write only when there is something to mark read
            with self.db.tx() as c:
                self._advance(c, room_id, f"u:{user_id}", rows[-1]["id"])
        return [public_message(m) for m in rows]

    def check_rooms(self, user_id: str, room_ids: list[str]) -> None:
        with self.db.read() as c:
            for room_id in room_ids:
                self._room(c, user_id, room_id)

    def take_for_agent(
        self, user_id: str, agent_id: int, room_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """New messages from others after the agent's cursor in the user's rooms.
        Returned messages count as read (the cursor moves past them)."""
        reader = f"a:{agent_id}"
        with self.db.read() as c:
            rooms = self._member_rooms(c, user_id, room_ids)
            after = {r: self._cursor(c, r, reader) for r in rooms}
            rows = messages.from_others(c, after, agent_id=agent_id, limit=READ_LIMIT)
        if not rows:  # the common case: a plain read, no write
            return []
        last: dict[str, int] = {}
        for m in rows:
            last[m["room_id"]] = m["id"]
        with self.db.tx() as c:
            for room_id, msg_id in last.items():
                self._advance(c, room_id, reader, msg_id)
                self._touch_agent(c, room_id, agent_id)
        return [public_message(m) for m in rows]

    def member_rooms(self, user_id: str, room_ids: list[str] | None = None) -> list[str]:
        with self.db.read() as c:
            return self._member_rooms(c, user_id, room_ids)

    def wake_rooms(self, user_id: str, agent_id: int) -> list[str]:
        """Rooms where this agent may auto-reply right now."""
        rows = self.db.all(
            "SELECT ra.room_id FROM room_agents ra JOIN members m"
            " ON m.room_id = ra.room_id AND m.user_id = ?"
            " WHERE ra.agent_id = ? AND ra.auto_reply_until > ?",
            (user_id, agent_id, self.now()),
        )
        return [r[0] for r in rows]

    def room_names(self, user_id: str, room_ids: set[str]) -> dict[str, str]:
        out = {}
        for room_id in sorted(room_ids):
            try:
                out[room_id] = self.room(user_id, room_id)["name"]
            except NotFound:
                continue
        return out

    def pending_for_wake(self, user_id: str, agent_id: int, after: int = 0) -> list[dict[str, Any]]:
        """For the Stop hook: messages from others in rooms where this agent may
        auto-reply right now, after max(cursor, after). Does not move the cursor."""
        rooms = self.wake_rooms(user_id, agent_id)
        with self.db.read() as c:
            reader = f"a:{agent_id}"
            since = {r: max(self._cursor(c, r, reader), int(after)) for r in rooms}
            rows = messages.from_others(c, since, agent_id=agent_id, limit=READ_LIMIT)
        return [public_message(m) for m in rows]

    @staticmethod
    def _member_rooms(c: sqlite3.Connection, user_id: str, room_ids: list[str] | None) -> list[str]:
        mine = [
            r[0] for r in c.execute("SELECT room_id FROM members WHERE user_id = ?", (user_id,)).fetchall()
        ]
        if room_ids is None:
            return mine
        wanted = set(room_ids)
        return [r for r in mine if r in wanted]

    # ---- auto-reply --------------------------------------------------

    def set_auto_reply(self, user_id: str, room_id: str, agent_id: int, hours: int) -> int | None:
        """Allow one of your agents to auto-reply in a room for 1, 8 or 24 hours (0 = off)."""
        hours = int(hours)
        if hours not in AUTO_REPLY_HOURS:
            raise Invalid("auto-reply must be off, 1, 8 or 24 hours")
        until = self.now() + hours * 3600 if hours else None
        with self.db.tx() as c:
            self._room(c, user_id, room_id)
            self._owned_agent(c, user_id, int(agent_id))
            c.execute(
                "INSERT INTO room_agents (room_id, agent_id, auto_reply_until) VALUES (?, ?, ?)"
                " ON CONFLICT(room_id, agent_id) DO UPDATE SET auto_reply_until = excluded.auto_reply_until",
                (room_id, int(agent_id), until),
            )
        return until

    def my_auto_reply(self, user_id: str, room_id: str) -> dict[int, int | None]:
        now = self.now()
        with self.db.read() as c:
            self._room(c, user_id, room_id)
            rows = c.execute(
                "SELECT ra.agent_id, ra.auto_reply_until FROM room_agents ra"
                " JOIN agents a ON a.id = ra.agent_id WHERE ra.room_id = ? AND a.user_id = ?",
                (room_id, user_id),
            ).fetchall()
        return {r[0]: (r[1] if r[1] and r[1] > now else None) for r in rows}

    # ---- wake tokens -------------------------------------------------

    def create_wake_token(self, user_id: str, agent_name: str) -> tuple[str, dict[str, Any]]:
        token = "rw_" + secrets.token_urlsafe(32)
        with self.db.tx() as c:
            active = c.execute(
                "SELECT COUNT(*) FROM wake_tokens WHERE user_id = ? AND revoked = 0", (user_id,)
            ).fetchone()[0]
            if active >= MAX_WAKE_TOKENS:
                raise Invalid(f"you have {MAX_WAKE_TOKENS} wake tokens already; revoke one first")
            agent = self._agent(c, user_id, agent_name, True)
            cur = c.execute(
                "INSERT INTO wake_tokens (token_hash, user_id, agent_id, created_at, revoked)"
                " VALUES (?, ?, ?, ?, 0)",
                (sha256(token), user_id, agent["id"], self.now()),
            )
        return token, {"id": int(cur.lastrowid), "agent": agent["name"], "agent_id": agent["id"]}

    def list_wake_tokens(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.all(
            "SELECT w.id, w.created_at, a.name AS agent FROM wake_tokens w"
            " JOIN agents a ON a.id = w.agent_id WHERE w.user_id = ? AND w.revoked = 0"
            " ORDER BY a.name, w.id",
            (user_id,),
        )
        return [dict(r) for r in rows]

    def revoke_wake_token(self, user_id: str, token_id: int) -> None:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE wake_tokens SET revoked = 1 WHERE id = ? AND user_id = ?", (int(token_id), user_id)
            )
            if not cur.rowcount:
                raise NotFound("wake token not found")

    def resolve_wake_token(self, token: str) -> tuple[str, dict[str, Any]] | None:
        if not token:
            return None
        r = self.db.one(
            "SELECT w.user_id, a.id, a.name FROM wake_tokens w JOIN agents a ON a.id = w.agent_id"
            " WHERE w.token_hash = ? AND w.revoked = 0",
            (sha256(token),),
        )
        if not r:
            return None
        return r["user_id"], {"id": r["id"], "name": r["name"], "user_id": r["user_id"]}

    # ---- listen tokens (sleep until someone writes) ------------------

    def create_listen_token(
        self, user_id: str, agent_name: str, room_ids: list[str] | None, hours: float
    ) -> tuple[str, dict[str, Any]]:
        """A token for GET /api/listen: one agent, these rooms (default: all of yours).
        In rooms this agent has never read, listening starts from now, so old history
        does not count as new."""
        hours = float(hours)
        if not 0 < hours <= LISTEN_HOURS_MAX:
            raise Invalid(f"hours must be more than 0 and at most {LISTEN_HOURS_MAX}")
        token = "rl_" + secrets.token_urlsafe(32)
        now = self.now()
        with self.db.tx() as c:
            agent = self._agent(c, user_id, agent_name, True)
            if room_ids is not None:
                rooms = self._member_rooms(c, user_id, room_ids)
                if set(room_ids) - set(rooms):
                    raise NotFound("room not found")
            else:
                # Default: the rooms this agent already takes part in, never all of them.
                taking_part = {
                    r[0]
                    for r in c.execute(
                        "SELECT room_id FROM room_agents WHERE agent_id = ?", (agent["id"],)
                    ).fetchall()
                }
                rooms = [r for r in self._member_rooms(c, user_id, None) if r in taking_part]
            if not rooms:
                raise Invalid("pass rooms: this agent does not take part in any room yet")
            # A new token replaces this agent's older ones for any of these rooms, so tokens
            # never pile up. An old loop still running then ends quietly (410, "replaced").
            for r in c.execute(
                "SELECT id, rooms FROM listen_tokens"
                " WHERE user_id = ? AND agent_id = ? AND revoked = 0 AND expires_at > ?",
                (user_id, agent["id"], now),
            ).fetchall():
                if set(json.loads(r["rooms"])) & set(rooms):
                    c.execute("UPDATE listen_tokens SET revoked = 2 WHERE id = ?", (r["id"],))
            live = c.execute(
                "SELECT COUNT(*) FROM listen_tokens WHERE user_id = ? AND revoked = 0 AND expires_at > ?",
                (user_id, now),
            ).fetchone()[0]
            if live >= MAX_LISTEN_TOKENS:
                raise Invalid(
                    f"you have {MAX_LISTEN_TOKENS} listen tokens running; stop one on the web page first"
                )
            reader = f"a:{agent['id']}"
            for room_id in rooms:
                self._touch_agent(c, room_id, agent["id"])
                has = c.execute(
                    "SELECT 1 FROM cursors WHERE room_id = ? AND reader = ?", (room_id, reader)
                ).fetchone()
                if not has:
                    self._advance(c, room_id, reader, messages.last_id(c, room_id))
            expires = now + int(hours * 3600)
            cur = c.execute(
                "INSERT INTO listen_tokens (token_hash, user_id, agent_id, rooms, created_at, expires_at, revoked)"
                " VALUES (?, ?, ?, ?, ?, ?, 0)",
                (sha256(token), user_id, agent["id"], json.dumps(rooms), now, expires),
            )
        return token, {
            "id": int(cur.lastrowid),
            "agent": agent["name"],
            "agent_id": agent["id"],
            "rooms": rooms,
            "expires_at": expires,
        }

    def resolve_listen_token(self, token: str | None) -> dict[str, Any] | None:
        """{id, user_id, agent_id, agent, rooms, expires_at} for a live listen token."""
        if not token or not token.startswith("rl_"):
            return None
        r = self.db.one(
            "SELECT t.id, t.user_id, t.rooms, t.expires_at, a.id AS agent_id, a.name AS agent"
            " FROM listen_tokens t JOIN agents a ON a.id = t.agent_id"
            " WHERE t.token_hash = ? AND t.revoked = 0",
            (sha256(token),),
        )
        if not r or r["expires_at"] <= self.now():
            return None
        d = dict(r)
        d["rooms"] = json.loads(d["rooms"])
        return d

    def listen_token_state(self, token: str | None) -> str:
        """"live", "replaced" (a newer token took over) or "dead" (expired, stopped, unknown)."""
        if not token or not token.startswith("rl_"):
            return "dead"
        r = self.db.one(
            "SELECT revoked, expires_at FROM listen_tokens WHERE token_hash = ?", (sha256(token),)
        )
        if not r or r["expires_at"] <= self.now():
            return "dead"
        return {0: "live", 2: "replaced"}.get(r["revoked"], "dead")

    def sleeping_agent(self, user_id: str, agent_id: int, room_id: str) -> bool:
        """Does this agent hold a live listen token covering this room?"""
        return agent_id in self.listening_tokens(user_id, room_id)

    def listening_tokens(self, user_id: str, room_id: str) -> dict[int, int]:
        """Your agents with a live listen token covering this room: agent id -> expiry."""
        now = self.now()
        rows = self.db.all(
            "SELECT agent_id, rooms, expires_at FROM listen_tokens"
            " WHERE user_id = ? AND revoked = 0 AND expires_at > ?",
            (user_id, now),
        )
        out: dict[int, int] = {}
        for r in rows:
            if room_id in json.loads(r["rooms"]):
                out[r["agent_id"]] = max(out.get(r["agent_id"], 0), r["expires_at"])
        return out

    def stop_listening(self, user_id: str, agent_id: int, room_id: str | None = None) -> int:
        """Revoke this agent's live listen tokens (only those covering room_id, if given)."""
        now = self.now()
        with self.db.tx() as c:
            self._owned_agent(c, user_id, int(agent_id))
            rows = c.execute(
                "SELECT id, rooms FROM listen_tokens"
                " WHERE user_id = ? AND agent_id = ? AND revoked = 0 AND expires_at > ?",
                (user_id, int(agent_id), now),
            ).fetchall()
            ids = [r["id"] for r in rows if room_id is None or room_id in json.loads(r["rooms"])]
            for token_id in ids:
                c.execute("UPDATE listen_tokens SET revoked = 1 WHERE id = ?", (token_id,))
        return len(ids)

    def pending_for_listen(self, user_id: str, agent_id: int, room_ids: list[str]) -> list[dict[str, Any]]:
        """For GET /api/listen: messages from others after this agent's cursor in these
        rooms (still a member). Does not move the cursor: the agent reads them next."""
        with self.db.read() as c:
            rooms = self._member_rooms(c, user_id, room_ids)
            reader = f"a:{agent_id}"
            since = {r: self._cursor(c, r, reader) for r in rooms}
            rows = messages.from_others(c, since, agent_id=agent_id, limit=READ_LIMIT)
        return [public_message(m) for m in rows]

    # ---- agent sign-in without MCP (device flow) ----------------------
    #
    # Like `gh auth login`: the agent starts with an invite and gets a secret
    # device code plus a short user code. The person opens the approve page,
    # signed in, and presses Approve, which joins the room (same invite rules
    # as a normal join). The agent's next poll gets an API token, once.

    @staticmethod
    def normalize_user_code(value: str | None) -> str:
        """'bcdf ghjk', 'BCDFGHJK' or 'BCDF-GHJK' -> 'BCDF-GHJK'; anything else -> ''."""
        raw = "".join(ch for ch in str(value or "").upper() if ch.isalnum())
        if len(raw) != 8 or any(ch not in _USER_CODE_ALPHABET for ch in raw):
            return ""
        return f"{raw[:4]}-{raw[4:]}"

    def start_device(self, invite: str, agent_name: str) -> dict[str, Any]:
        """Returns {device_code (secret, shown once), user_code, expires_in, interval}."""
        agent_name = clean_agent_name(agent_name)
        now = self.now()
        device_code = "rd_" + secrets.token_urlsafe(32)
        with self.db.tx() as c:
            # A spent invite may still start: approving then works only for someone
            # already in the room (no new member, no use spent), as in _join.
            inv = self._valid_invite(c, invite, allow_used=True)
            self._rate(f"invite:{inv['id']}", "agent_start")
            self._rate(f"inviter:{inv['created_by']}", "agent_start_owner")
            pending = c.execute(
                "SELECT COUNT(*) FROM agent_devices WHERE status = 'pending' AND expires_at > ?", (now,)
            ).fetchone()[0]
            if pending >= MAX_PENDING_DEVICES:
                raise RateLimited("too many agent sign-ins are waiting for approval. Try again in a few minutes.")
            mine = c.execute(
                "SELECT COUNT(*) FROM agent_devices d JOIN invites i ON i.id = d.invite_id"
                " WHERE i.created_by = ? AND d.status = 'pending' AND d.expires_at > ?",
                (inv["created_by"], now),
            ).fetchone()[0]
            if mine >= MAX_PENDING_PER_INVITER:
                raise RateLimited("too many agent sign-ins are waiting on this person's invites. Try again soon.")
            while True:
                raw = "".join(secrets.choice(_USER_CODE_ALPHABET) for _ in range(8))
                user_code = f"{raw[:4]}-{raw[4:]}"
                taken = c.execute(
                    "SELECT 1 FROM agent_devices WHERE user_code = ? AND expires_at > ?", (user_code, now)
                ).fetchone()
                if not taken:
                    break
            c.execute(
                "INSERT INTO agent_devices (device_hash, user_code, invite_id, agent_name, status,"
                " created_at, expires_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (sha256(device_code), user_code, inv["id"], agent_name, now, now + DEVICE_TTL),
            )
        return {
            "device_code": device_code,
            "user_code": user_code,
            "expires_in": DEVICE_TTL,
            "interval": DEVICE_INTERVAL,
        }

    def _live_device(self, c: sqlite3.Connection, user_code: str | None) -> dict[str, Any]:
        code = self.normalize_user_code(user_code)
        r = (
            c.execute(
                "SELECT * FROM agent_devices WHERE user_code = ? AND expires_at > ? ORDER BY id DESC LIMIT 1",
                (code, self.now()),
            ).fetchone()
            if code
            else None
        )
        if not r:
            raise NotFound(DEVICE_NOT_FOUND)
        return dict(r)

    def peek_device(self, user_id: str, user_code: str | None) -> dict[str, Any]:
        """What the approve page shows. Room name and inviter only while pending,
        the same facts the signed-in invite page shows."""
        self._rate(user_id, "device_view")
        with self.db.read() as c:
            d = self._live_device(c, user_code)
            out: dict[str, Any] = {
                "user_code": d["user_code"],
                "agent": d["agent_name"],
                "status": d["status"],
                "created_at": d["created_at"],
            }
            if d["status"] == "pending":
                inv = self._invite_by_id(c, d["invite_id"], allow_used=True)
                member = c.execute(
                    "SELECT 1 FROM members WHERE room_id = ? AND user_id = ?", (inv["room_id"], user_id)
                ).fetchone()
                if not member and inv["uses"] >= inv["max_uses"]:
                    raise Invalid(INVITE_NOT_VALID)
                out["room_name"] = inv["room_name"]
                out["inviter"] = person_label(inv["inviter"], inv["created_by"])
                out["already_member"] = bool(member)
                out["invite_spent"] = inv["uses"] >= inv["max_uses"]
            return out

    def approve_device(self, user_id: str, user_code: str | None) -> dict[str, Any]:
        """Join the room with the device's invite and let the agent's next poll get a token."""
        self._rate(user_id, "device_view")  # also slows guessing codes on the form
        with self.db.tx() as c:
            d = self._live_device(c, user_code)
            if d["status"] != "pending":
                raise Invalid(DEVICE_USED)
            active = c.execute(
                "SELECT COUNT(*) FROM agent_tokens WHERE user_id = ? AND revoked = 0 AND expires_at > ?",
                (user_id, self.now()),
            ).fetchone()[0]
            if active >= MAX_AGENT_TOKENS:
                raise Invalid(f"you have {MAX_AGENT_TOKENS} agent tokens already; revoke one in Settings first")
            room = self._join(c, user_id, self._invite_by_id(c, d["invite_id"], allow_used=True))
            agent = self._agent(c, user_id, d["agent_name"], True)
            # Approved at the last second: leave the agent two minutes to collect it.
            c.execute(
                "UPDATE agent_devices SET status = 'approved', user_id = ?, room_id = ?, agent_id = ?,"
                " expires_at = MAX(expires_at, ?) WHERE id = ? AND status = 'pending'",
                (user_id, room["id"], agent["id"], self.now() + 120, d["id"]),
            )
            return {"room": room, "agent": agent["name"]}

    def deny_device(self, user_id: str, user_code: str | None) -> None:
        self._rate(user_id, "device_view")
        with self.db.tx() as c:
            d = self._live_device(c, user_code)
            if d["status"] != "pending":
                raise Invalid(DEVICE_USED)
            c.execute(
                "UPDATE agent_devices SET status = 'denied', user_id = ? WHERE id = ? AND status = 'pending'",
                (user_id, d["id"]),
            )

    def device_waiting(self, device_code: str) -> bool:
        """Read-only: is this code still pending and live? For the token long poll."""
        r = self.db.one(
            "SELECT 1 FROM agent_devices WHERE device_hash = ? AND status = 'pending' AND expires_at > ?",
            (sha256(device_code or ""), self.clock()),
        )
        return r is not None

    def poll_device(self, device_code: str | None, *, check_interval: bool = True) -> tuple[str, dict[str, Any] | None]:
        """One token poll. Returns (state, token) where state is one of ok,
        authorization_pending, slow_down, access_denied, expired_token, invalid_grant.
        The token is issued exactly once; later polls get invalid_grant."""
        if not device_code or not isinstance(device_code, str):
            return "invalid_grant", None
        now = self.clock()
        with self.db.tx() as c:
            r = c.execute("SELECT * FROM agent_devices WHERE device_hash = ?", (sha256(device_code),)).fetchone()
            if not r or r["status"] == "issued":
                return "invalid_grant", None
            d = dict(r)
            if d["expires_at"] <= now:
                return "expired_token", None
            if d["status"] == "denied":
                return "access_denied", None
            too_soon = check_interval and now - d["last_poll"] < DEVICE_INTERVAL - 1
            c.execute("UPDATE agent_devices SET last_poll = ? WHERE id = ?", (now, d["id"]))
            if too_soon:
                return "slow_down", None
            if d["status"] == "pending":
                return "authorization_pending", None
            token = "ra_" + secrets.token_urlsafe(32)
            expires_at = int(now) + AGENT_TOKEN_DAYS * 86400
            c.execute(
                "INSERT INTO agent_tokens (token_hash, user_id, agent_id, room_id, created_at, expires_at, revoked)"
                " VALUES (?, ?, ?, ?, ?, ?, 0)",
                (sha256(token), d["user_id"], d["agent_id"], d["room_id"], int(now), expires_at),
            )
            c.execute("UPDATE agent_devices SET status = 'issued' WHERE id = ?", (d["id"],))
            agent = c.execute("SELECT name FROM agents WHERE id = ?", (d["agent_id"],)).fetchone()
        return "ok", {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": AGENT_TOKEN_DAYS * 86400,
            "expires_at": iso(expires_at),
            "room_id": d["room_id"],
            "agent": agent["name"] if agent else d["agent_name"],
        }

    def resolve_agent_token(self, token: str | None) -> dict[str, Any] | None:
        """{id, user_id, room_id, agent_id, agent} for a live API token, else None."""
        if not token or not token.startswith("ra_"):
            return None
        r = self.db.one(
            "SELECT t.id, t.user_id, t.room_id, t.expires_at, a.id AS agent_id, a.name AS agent"
            " FROM agent_tokens t JOIN agents a ON a.id = t.agent_id"
            " WHERE t.token_hash = ? AND t.revoked = 0",
            (sha256(token),),
        )
        if not r or r["expires_at"] <= self.now():
            return None
        return dict(r)

    def list_agent_tokens(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.all(
            "SELECT t.id, t.created_at, t.expires_at, a.name AS agent, r.name AS room_name"
            " FROM agent_tokens t JOIN agents a ON a.id = t.agent_id JOIN rooms r ON r.id = t.room_id"
            " WHERE t.user_id = ? AND t.revoked = 0 AND t.expires_at > ? ORDER BY t.id DESC",
            (user_id, self.now()),
        )
        return [dict(r) for r in rows]

    def revoke_agent_token(self, user_id: str, token_id: int) -> None:
        with self.db.tx() as c:
            cur = c.execute(
                "UPDATE agent_tokens SET revoked = 1 WHERE id = ? AND user_id = ?", (int(token_id), user_id)
            )
            if not cur.rowcount:
                raise NotFound("agent token not found")

    # ---- web sessions ------------------------------------------------

    def create_session(self, user_id: str, workos_sid: str | None = None) -> tuple[str, str]:
        """Returns (session token for the cookie, csrf token). Only the hash is stored.

        workos_sid is the WorkOS session behind this sign-in, kept so logout can end it too."""
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO web_sessions (token_hash, user_id, csrf, expires_at, workos_sid) VALUES (?, ?, ?, ?, ?)",
                (sha256(token), user_id, csrf, self.now() + SESSION_DAYS * 86400, workos_sid),
            )
        return token, csrf

    def resolve_session(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        r = self.db.one(
            "SELECT s.csrf, s.expires_at, u.id, u.email, u.name FROM web_sessions s"
            " JOIN users u ON u.id = s.user_id WHERE s.token_hash = ?",
            (sha256(token),),
        )
        if not r or r["expires_at"] <= self.now():
            return None
        return {"user": {"id": r["id"], "email": r["email"], "name": r["name"]}, "csrf": r["csrf"]}

    def delete_session(self, token: str | None) -> str | None:
        """Delete the session. Returns its WorkOS session id, if it had one."""
        if not token:
            return None
        with self.db.tx() as c:
            r = c.execute(
                "DELETE FROM web_sessions WHERE token_hash = ? RETURNING workos_sid", (sha256(token),)
            ).fetchone()
        return r["workos_sid"] if r else None

    # ---- retention ---------------------------------------------------

    def purge_expired(self) -> int:
        """Delete messages older than each room's retention, and dead sessions."""
        now = self.now()
        removed = 0
        with self.db.tx() as c:
            for r in c.execute("SELECT id, retention_days FROM rooms").fetchall():
                removed += messages.purge_older_than(c, r["id"], now - int(r["retention_days"]) * 86400)
            c.execute("DELETE FROM web_sessions WHERE expires_at <= ?", (now,))
            c.execute("DELETE FROM agent_devices WHERE expires_at <= ?", (now,))
            c.execute("DELETE FROM agent_tokens WHERE expires_at <= ?", (now,))
            c.execute("DELETE FROM listen_tokens WHERE expires_at <= ?", (now,))
        return removed
