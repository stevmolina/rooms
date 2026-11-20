"""Sleep listening: listen tokens, GET /api/listen, the curl loop, presence and Stop."""

from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
import time

import httpx
import pytest

from conftest import Live, add_member, make_room
from test_agent_api import _auth, _signed_in_agent
from rooms.tools import NEXT_STOPPED


def _token(command: str) -> str:
    m = re.search(r"Bearer (rl_[A-Za-z0-9_-]+)", command)
    assert m, command
    return m.group(1)


def _listen(live: Live, token: str, timeout: float = 0.3) -> httpx.Response:
    return httpx.get(f"{live.url}/api/listen", params={"timeout": timeout}, headers=_auth(token), timeout=70)


async def test_listen_sleeps_until_someone_writes(live: Live):
    room = await make_room(live, "dave", "Sleep room")
    await add_member(live, room, "dave", "bob")
    out = await live.call("bob", "listen_token", agent="Codex", rooms=[room])
    assert out["rooms"] == [room] and "end your turn" in out["next"]
    token = _token(out["command"])

    assert _listen(live, token).status_code == 204  # nothing yet

    # A long poll that returns the moment Alice writes.
    async def poll() -> httpx.Response:
        async with httpx.AsyncClient(timeout=70) as client:
            return await client.get(f"{live.url}/api/listen", params={"timeout": 20}, headers=_auth(token))

    task = asyncio.create_task(poll())
    await asyncio.sleep(0.4)
    await live.call("dave", "send", room=room, agent="Claude", body="hello @Codex")
    t0 = time.monotonic()
    r = await task
    assert time.monotonic() - t0 < 1.5
    assert r.status_code == 200
    data = r.json()
    assert data["new"] == {room: 1} and data["rooms"][room] == "Sleep room"
    assert "hello" not in r.text  # who and where, never the text

    # The cursor did not move: the agent reads the message next, then sleeps again.
    assert _listen(live, token).status_code == 200
    got = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=0)
    assert [m["body"] for m in got["messages"]] == ["hello @Codex"]
    assert _listen(live, token).status_code == 204


async def test_listen_starts_from_now_in_unread_rooms(live: Live):
    room = await make_room(live, "dave", "Old history")
    await add_member(live, room, "dave", "bob")
    for i in range(3):
        await live.call("dave", "send", room=room, agent="Claude", body=f"old {i}")
    out = await live.call("bob", "listen_token", agent="Newcomer", rooms=[room])
    assert _listen(live, _token(out["command"])).status_code == 204


async def test_listen_token_scope_and_limits(live: Live):
    room = await make_room(live, "dave", "Scope room")
    other = await make_room(live, "carol", "Not yours")
    assert "not found" in await live.call_error("dave", "listen_token", agent="Claude", rooms=[other])
    out = await live.call("dave", "listen_token", agent="Claude", rooms=[room], hours=0.0003)
    token = _token(out["command"])
    time.sleep(1.2)
    assert _listen(live, token).status_code == 401  # expired
    assert _listen(live, "rl_nope").status_code == 401
    # A listen token works nowhere else.
    assert httpx.get(f"{live.url}/api/rooms", headers=_auth(token)).status_code == 401
    assert httpx.get(f"{live.url}/api/wait", headers=_auth(token)).status_code == 401


async def test_stop_button_ends_sleeping_and_looping_agents(live: Live):
    room = await make_room(live, "dave", "Stop room")
    await add_member(live, room, "dave", "bob")
    out = await live.call("dave", "listen_token", agent="Sleeper", rooms=[room])
    token = _token(out["command"])
    page = live.web("dave").get(f"/r/{room}").text
    assert "listening" in page and "stop-listening" in page

    agent_id = live.app.store.agent(live.sub("dave"), "Sleeper")["id"]
    alice = live.web("dave")
    # An open hold ends the moment Stop is pressed, not at its timeout.
    hold = asyncio.create_task(asyncio.to_thread(_listen, live, token, 20))
    await asyncio.sleep(0.5)
    t0 = time.monotonic()
    assert alice.post(f"/r/{room}/stop-listening", {"agent_id": agent_id}).status_code == 303
    assert (await hold).status_code == 401
    assert time.monotonic() - t0 < 2
    page = live.web("dave").get(f"/r/{room}").text
    assert 'class="dot on"' not in page  # the stopped hold does not count as listening
    out = await live.call("dave", "wait", agent="Sleeper", rooms=[room], timeout=0)  # the agent reads once more
    assert out["next"] == NEXT_STOPPED
    page = live.web("dave").get(f"/r/{room}").text
    assert 'class="dot on"' not in page  # a quick read is not listening
    assert _listen(live, token).status_code == 401
    out = await live.call("dave", "wait", agent="Sleeper", rooms=[room], timeout=0.1)
    assert out["next"] != NEXT_STOPPED  # said once, to the read above

    # Another member cannot stop your agent.
    assert live.web("bob").post(f"/r/{room}/stop-listening", {"agent_id": agent_id}).status_code == 404


async def test_others_see_a_listening_agent(live: Live):
    room = await make_room(live, "dave", "Presence room")
    await add_member(live, room, "dave", "bob")
    await live.call("bob", "send", room=room, agent="Codex", body="here")
    out = await live.call("bob", "listen_token", agent="Codex", rooms=[room])
    task = asyncio.create_task(asyncio.to_thread(_listen, live, _token(out["command"]), 3))
    await asyncio.sleep(0.5)
    page = live.web("dave").get(f"/r/{room}").text
    agents = page.split("<h3>Agents</h3>", 1)[-1]
    assert "Codex · Bob" in page and 'class="dot on"' in agents
    await task


async def test_http_api_agents_get_a_sleeping_command(live: Live):
    room = await make_room(live, "dave", "Api sleep")
    token = await _signed_in_agent(live, "dave", "bob", room, agent="Curl")
    r = httpx.post(f"{live.url}/api/rooms/{room}/listen", headers=_auth(token), json={})
    assert r.status_code == 200, r.text
    data = r.json()
    assert f"/api/rooms/{room}/wait?timeout=0" in data["command"]
    assert _listen(live, _token(data["command"])).status_code == 204


@pytest.mark.skipif(not shutil.which("curl") or not shutil.which("sh"), reason="needs sh and curl")
async def test_the_real_command_exits_when_someone_writes(live: Live):
    room = await make_room(live, "dave", "Shell room")
    await add_member(live, room, "dave", "bob")
    out = await live.call("bob", "listen_token", agent="Shell", rooms=[room])
    command = out["command"].replace("timeout=50", "timeout=2")
    proc = subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE, text=True)
    await asyncio.sleep(3)  # at least one empty round
    assert proc.poll() is None
    await live.call("dave", "send", room=room, agent="Claude", body="wake up")
    stdout, _ = await asyncio.to_thread(proc.communicate, timeout=10)
    assert proc.returncode == 0
    assert stdout.startswith("Rooms: new messages.") and 'agent="Shell"' in stdout

    # Stopped tokens end the loop with the stop line.
    agent_id = live.app.store.agent(live.sub("bob"), "Shell")["id"]
    await live.call("bob", "wait", agent="Shell", rooms=[room], timeout=0)  # read it first
    proc = subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE, text=True)
    await asyncio.sleep(0.5)
    live.app.store.stop_listening(live.sub("bob"), agent_id)
    stdout, _ = await asyncio.to_thread(proc.communicate, timeout=10)
    assert "expired or your person stopped it" in stdout


async def test_new_tokens_replace_old_ones(live: Live):
    """A chatty agent asks for a new command after every reply: it never hits the cap."""
    room = await make_room(live, "erin", "Chatty room")
    first = _token((await live.call("erin", "listen_token", agent="Chatty", rooms=[room]))["command"])
    for _ in range(25):
        out = await live.call("erin", "listen_token", agent="Chatty", rooms=[room])
    assert _listen(live, first).status_code == 410  # replaced: the old loop ends quietly
    assert _listen(live, _token(out["command"])).status_code == 204


async def test_the_shell_loop_ends_quietly_when_replaced(live: Live):
    room = await make_room(live, "erin", "Replace shell")
    out = await live.call("erin", "listen_token", agent="Swap", rooms=[room])
    command = out["command"].replace("timeout=50", "timeout=2")
    proc = subprocess.Popen(["sh", "-c", command], stdout=subprocess.PIPE, text=True)
    await asyncio.sleep(0.5)
    await live.call("erin", "listen_token", agent="Swap", rooms=[room])
    stdout, _ = await asyncio.to_thread(proc.communicate, timeout=10)
    assert "replaced this one. Nothing to do." in stdout


async def test_stop_is_per_room_and_does_not_linger(live: Live):
    a = await make_room(live, "erin", "Stop A")
    b = await make_room(live, "erin", "Stop B")
    agent_id = live.app.store.agent(live.sub("erin"), "Two rooms")["id"]
    await live.call("erin", "listen_token", agent="Two rooms", rooms=[a, b])
    assert live.web("erin").post(f"/r/{a}/stop-listening", {"agent_id": agent_id}).status_code == 303
    out = await live.call("erin", "wait", agent="Two rooms", rooms=[b], timeout=0)
    assert out["next"] != NEXT_STOPPED  # Stop in A says nothing about B
    # A later session starts clean: a new token clears an unread Stop.
    await live.call("erin", "listen_token", agent="Two rooms", rooms=[a])
    out = await live.call("erin", "wait", agent="Two rooms", rooms=[a], timeout=0)
    assert out["next"] != NEXT_STOPPED


async def test_presence_is_per_room(live: Live):
    x = await make_room(live, "frank", "Room X")
    y = await make_room(live, "frank", "Room Y")
    await live.call("frank", "send", room=x, agent="Busy", body="hi in X")
    task = asyncio.create_task(live.call("frank", "wait", agent="Busy", rooms=[y], timeout=6))
    await asyncio.sleep(0.5)
    page_x = live.web("frank").get(f"/r/{x}").text
    assert 'class="dot on"' not in page_x
    await task


async def test_default_rooms_are_the_ones_the_agent_takes_part_in(live: Live):
    mine = await make_room(live, "frank", "Taken part")
    await make_room(live, "frank", "Never touched")
    assert "pass rooms" in await live.call_error("frank", "listen_token", agent="Fresh one")
    await live.call("frank", "send", room=mine, agent="Fresh one", body="hello")
    out = await live.call("frank", "listen_token", agent="Fresh one")
    assert out["rooms"] == [mine]


async def test_leaving_a_room_ends_its_listen_token(live: Live):
    room = await make_room(live, "frank", "Leave room")
    await add_member(live, room, "frank", "erin")
    token = _token((await live.call("erin", "listen_token", agent="Leaver", rooms=[room]))["command"])
    assert live.web("erin").post(f"/r/{room}/leave").status_code == 303
    assert _listen(live, token).status_code == 401


async def test_a_sleeping_agent_is_told_to_sleep_again(live: Live):
    from rooms.tools import NEXT_SLEEP_NEW, NEXT_SLEEP_ON

    room = await make_room(live, "frank", "Back to sleep")
    await add_member(live, room, "frank", "erin")
    await live.call("erin", "listen_token", agent="Napper", rooms=[room])
    await live.call("frank", "send", room=room, agent="Claude", body="psst")
    out = await live.call("erin", "wait", agent="Napper", rooms=[room], timeout=0)
    assert out["next"] == NEXT_SLEEP_NEW
    out = await live.call("erin", "send", room=room, agent="Napper", body="hi")
    assert out["next"] == NEXT_SLEEP_ON


async def test_sleepers_do_not_block_reads(live: Live):
    """Sleeping holds have their own pool: six sleepers, and a read still works."""
    rooms = [await make_room(live, "carol", f"Pool {i}") for i in range(6)]
    tokens = [
        _token((await live.call("carol", "listen_token", agent=f"S{i}", rooms=[r]))["command"])
        for i, r in enumerate(rooms)
    ]
    holds = [asyncio.create_task(asyncio.to_thread(_listen, live, t, 4)) for t in tokens]
    await asyncio.sleep(0.6)
    out = await live.call("carol", "wait", agent="Reader", rooms=[rooms[0]], timeout=0)
    assert "messages" in out
    codes = [(await h).status_code for h in holds]
    assert codes == [204] * 6
