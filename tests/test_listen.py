"""Listen mode: the wait `next` hint, the listen prompt and the room page snippet."""

from __future__ import annotations

from fastmcp import Client
from fastmcp.client.auth import BearerAuth

from conftest import Live, add_member, make_room
import time

from rooms.tools import INSTRUCTIONS, NEXT_EMPTY, NEXT_LISTEN, NEXT_NEW


async def test_wait_says_what_to_do_next(live: Live):
    room = await make_room(live, "alice", "Listen room")
    await add_member(live, room, "alice", "bob")

    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=0.2)
    assert out["messages"] == [] and out["next"] == NEXT_EMPTY
    assert out["rooms"] == {}  # empty waits stay small: no notices repeated

    await live.call("alice", "send", room=room, agent="Claude", body="hi @Codex")
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=5)
    assert len(out["messages"]) == 1 and out["next"] == NEXT_NEW

    live.app.store.update_room(live.sub("alice"), room, agent_cap=1)
    await live.call_error("alice", "send", room=room, agent="Claude", body="over")  # pauses
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=0.2)
    assert "Paused: Listen room" in out["next"] and "stop listening" in out["next"]


async def test_listen_prompt(live: Live):
    room = await make_room(live, "alice", "Prompt room")
    async with Client(f"{live.url}/mcp", auth=BearerAuth(live.token("alice"))) as client:
        prompts = {p.name for p in await client.list_prompts()}
        assert "listen" in prompts
        result = await client.get_prompt("listen", {"room": room, "agent": "Codex"})
        text = result.messages[0].content.text
        assert f'rooms=["{room}"]' in text and 'agent="Codex"' in text
        # The person pastes this as their own words: no text another member chose.
        assert "Prompt room" not in text
        assert "until I say stop" in text and "background helper" in text

    # Non-members get an error, not the room name.
    async with Client(f"{live.url}/mcp", auth=BearerAuth(live.token("bob"))) as client:
        try:
            await client.get_prompt("listen", {"room": room})
        except Exception as exc:  # noqa: BLE001
            assert "Prompt room" not in str(exc)
        else:
            raise AssertionError("expected an error")


def test_instructions_make_listening_the_default():
    assert "Listening is the default" in INSTRUCTIONS and "`next`" in INSTRUCTIONS
    assert "sub-agent" in INSTRUCTIONS


async def test_taking_part_says_keep_listening(live: Live):
    out = await live.call("alice", "room_create", name="Default room")
    room = out["room"]["id"]
    assert out["next"] == NEXT_LISTEN
    inv = await live.call("alice", "room_invite", room=room)
    out = await live.call("bob", "room_join", invite=inv["invite"], agent="Codex")
    assert out["next"] == NEXT_LISTEN
    out = await live.call("bob", "send", room=room, agent="Codex", body="hello")
    assert out["next"] == NEXT_LISTEN
    out = await live.call("bob", "read", room=room, agent="Codex")
    assert out["next"] == NEXT_LISTEN


async def test_send_does_not_skip_a_message_that_landed_first(live: Live):
    """Codex read message 1, Claude posted 2, then Codex posted 3. Codex must still get 2."""
    room = await make_room(live, "alice", "Race room")
    await add_member(live, room, "alice", "bob")
    await live.call("alice", "send", room=room, agent="Claude", body="one")
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=2)
    assert [m["body"] for m in out["messages"]] == ["one"]
    await live.call("alice", "send", room=room, agent="Claude", body="two")  # Codex has not seen it
    out = await live.call("bob", "send", room=room, agent="Codex", body="three")
    assert "1 message(s) from others arrived before yours" in out["next"]
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=2)
    assert [m["body"] for m in out["messages"]] == ["two"]
    # Now nothing is pending, so a send marks the room read again.
    out = await live.call("bob", "send", room=room, agent="Codex", body="four")
    assert out["next"] == NEXT_LISTEN
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=0.2)
    assert out["messages"] == []


async def test_listening_stops_after_a_quiet_stretch(live: Live):
    room = await make_room(live, "alice", "Quiet room")
    live.app.listening.quiet_after = 0.5
    try:
        out = await live.call("alice", "wait", agent="Sleepy", rooms=[room], timeout=0.3)
        assert out["next"] == NEXT_EMPTY  # first wait starts the stretch
        time.sleep(0.4)
        out = await live.call("alice", "wait", agent="Sleepy", rooms=[room], timeout=0.3)
        assert "Stop listening now" in out["next"]
        # Taking part again starts a new stretch.
        await live.call("alice", "send", room=room, agent="Sleepy", body="back")
        out = await live.call("alice", "wait", agent="Sleepy", rooms=[room], timeout=0.1)
        assert out["next"] == NEXT_EMPTY
    finally:
        live.app.listening.quiet_after = 30 * 60


async def test_room_page_shows_listen_prompt(live: Live):
    room = await make_room(live, "alice", "Page room")
    await live.call("alice", "send", room=room, agent="Codex", body="hello")
    page = live.web("alice").get(f"/r/{room}").text
    assert 'id="listen-text"' in page and "Add an agent" in page
    assert f"rooms=[&quot;{room}&quot;]" in page
    first = live.app.store.my_agents(live.sub("alice"))[0]["name"]
    assert f"agent=&quot;{first}&quot;" in page  # defaults to one of the person's own agents


async def test_new_agent_posting_skips_old_history(live: Live):
    room = await make_room(live, "alice", "History room")
    await add_member(live, room, "alice", "bob")
    for i in range(5):
        await live.call("alice", "send", room=room, agent="Claude", body=f"old {i}")
    live.sql("UPDATE messages SET created_at = created_at - 600 WHERE room_id = ?", (room,))
    out = await live.call("bob", "send", room=room, agent="Fresh", body="hi all")
    assert out["next"] == NEXT_LISTEN
    out = await live.call("bob", "wait", agent="Fresh", rooms=[room], timeout=0.2)
    assert out["messages"] == []


async def test_reply_in_thread_does_not_redeliver_the_thread(live: Live):
    room = await make_room(live, "alice", "Thread room")
    await add_member(live, room, "alice", "bob")
    root = await live.call("alice", "send", room=room, agent="Claude", body="root")
    await live.call("alice", "send", room=room, agent="Claude", body="reply", thread=root["id"])
    out = await live.call("bob", "read", room=room, agent="Codex", thread=root["id"])
    assert len(out["messages"]) == 2
    out = await live.call("bob", "send", room=room, agent="Codex", body="me too", thread=root["id"])
    assert out["next"] == NEXT_LISTEN
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=0.2)
    assert out["messages"] == []


async def test_quiet_clock_is_per_room(live: Live):
    busy = await make_room(live, "alice", "Busy room")
    calm = await make_room(live, "alice", "Calm room")
    live.app.listening.quiet_after = 0.5
    try:
        await live.call("alice", "wait", agent="Two", rooms=[calm], timeout=0.1)
        time.sleep(0.6)
        await live.call("alice", "send", room=busy, agent="Two", body="busy elsewhere")
        out = await live.call("alice", "wait", agent="Two", rooms=[calm], timeout=0.1)
        assert "Stop listening now" in out["next"]  # activity in another room does not count
    finally:
        live.app.listening.quiet_after = 30 * 60
