"""Run the real server over HTTP in a background thread (AUTH_MODE=dev)."""

from __future__ import annotations

import re
import socket
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import logging

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from rooms.config import DevUser, Settings
from rooms.server import App, build_server
from rooms.store import user_tag

USERS = {
    "alice": ("tok-alice", DevUser("user_alice", "alice@example.com", "Alice")),
    "bob": ("tok-bob", DevUser("user_bob", "bob@example.com", "Bob")),
    "carol": ("tok-carol", DevUser("user_carol", "carol@example.com", "Carol")),
    "dave": ("tok-dave", DevUser("user_dave", "dave@example.com", "Dave")),
    "erin": ("tok-erin", DevUser("user_erin", "erin@example.com", "Erin")),
    "frank": ("tok-frank", DevUser("user_frank", "frank@example.com", "Frank")),
}


# The MCP library logs client disconnects as errors; they are expected here.
logging.getLogger("mcp.server.streamable_http").setLevel(logging.CRITICAL)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Live:
    url: str
    app: App
    db_path: str

    def label(self, who: str, agent: str | None = None) -> str:
        """The author label clients see, e.g. "Claude · Alice #1a2b"."""
        dev = USERS[who][1]
        person = f"{dev.name} #{user_tag(dev.sub)}"
        return f"{agent} · {person}" if agent else person

    def token(self, who: str) -> str:
        return USERS[who][0]

    def sub(self, who: str) -> str:
        return USERS[who][1].sub

    def sql(self, query: str, params: tuple = ()) -> list[Any]:
        conn = sqlite3.connect(self.db_path)
        try:
            rows = conn.execute(query, params).fetchall()
            conn.commit()
            return rows
        finally:
            conn.close()

    async def call(self, who: str, tool: str, **args: Any) -> Any:
        async with Client(f"{self.url}/mcp", auth=BearerAuth(self.token(who))) as client:
            result = await client.call_tool(tool, args)
            return result.structured_content

    async def call_error(self, who: str, tool: str, **args: Any) -> str:
        async with Client(f"{self.url}/mcp", auth=BearerAuth(self.token(who))) as client:
            result = await client.call_tool(tool, args, raise_on_error=False)
            assert result.is_error, f"{tool} should have failed"
            return result.content[0].text

    def web(self, who: str | None) -> Web:
        client = httpx.Client(base_url=self.url, follow_redirects=False, timeout=30)
        if who:
            r = client.get("/dev/login", params={"token": self.token(who)})
            assert r.status_code == 303, r.text
        return Web(client, self.url)


class Web:
    def __init__(self, client: httpx.Client, url: str):
        self.client = client
        self.url = url

    def get(self, path: str, **kw: Any) -> httpx.Response:
        return self.client.get(path, **kw)

    def csrf(self) -> str:
        page = self.client.get("/settings").text
        m = re.search(r'name="csrf" value="([^"]+)"', page)
        assert m, "no csrf token on page"
        return m.group(1)

    def post(self, path: str, data: dict | None = None, *, csrf: bool | str = True, **kw: Any) -> httpx.Response:
        data = dict(data or {})
        if csrf is True:
            data["csrf"] = self.csrf()
        elif isinstance(csrf, str):
            data["csrf"] = csrf
        return self.client.post(path, data=data, **kw)


@pytest.fixture(scope="session")
def live(tmp_path_factory: pytest.TempPathFactory) -> Live:
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    db_path = str(Path(tmp_path_factory.mktemp("db")) / "rooms.db")
    settings = Settings(
        auth_mode="dev",
        base_url=url,
        db_path=db_path,
        dev_tokens={tok: user for tok, user in USERS.values()},
        cookie_secure=False,
        log_level="WARNING",
    )
    mcp = build_server(settings)
    config = uvicorn.Config(
        mcp.http_app(), host="127.0.0.1", port=port, log_level="warning", timeout_graceful_shutdown=1
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("server did not start")
        time.sleep(0.05)
    # Rate limits get their own test; keep them out of the way elsewhere.
    mcp.rooms_app.store.limits = {k: (100_000, w) for k, (_, w) in mcp.rooms_app.store.limits.items()}
    yield Live(url=url, app=mcp.rooms_app, db_path=db_path)
    server.should_exit = True
    thread.join(timeout=5)


async def make_room(live: Live, owner: str, name: str = "Test room", notice: str = "") -> str:
    out = await live.call(owner, "room_create", name=name, notice=notice)
    return out["room"]["id"]


async def add_member(live: Live, room_id: str, owner: str, who: str) -> None:
    inv = await live.call(owner, "room_invite", room=room_id)
    await live.call(who, "room_join", invite=inv["invite"])
