"""3. wait wakes fast; 4. loop cap; 5. retention."""

from __future__ import annotations

import asyncio
import time

from conftest import Live, add_member, make_room


async def test_wait_wakes_on_message_from_someone_else(live: Live):
    room = await make_room(live, "alice", "Wake room")
    await add_member(live, room, "alice", "bob")

    waiter = asyncio.create_task(live.call("bob", "wait", agent="Codex", rooms=[room], timeout=20))
    await asyncio.sleep(0.4)
    sent = await live.call("alice", "send", room=room, agent="Claude", body="ping @Codex")
    t0 = time.monotonic()
    out = await waiter
    assert time.monotonic() - t0 < 1.0
    assert [m["id"] for m in out["messages"]] == [sent["id"]]
    msg = out["messages"][0]
    assert msg["author"] == live.label("alice", "Claude") and msg["kind"] == "agent"
    assert msg["mentions"] == ["Codex"]
    assert out["rooms"][room]["name"] == "Wake room"

    # Returned messages count as read: the next wait times out empty.
    t0 = time.monotonic()
    out = await live.call("bob", "wait", agent="Codex", rooms=[room], timeout=0.5)
    assert out["messages"] == []
    assert 0.4 <= time.monotonic() - t0 < 3


async def test_wait_ignores_own_agent(live: Live):
    room = await make_room(live, "alice", "Self room")
    await live.call("alice", "send", room=room, agent="Solo", body="talking to myself")
    out = await live.call("alice", "wait", agent="Solo", rooms=[room], timeout=0.3)
    assert out["messages"] == []
    # A different agent of the same person does see it.
    out = await live.call("alice", "wait", agent="Other", rooms=[room], timeout=0.3)
    assert [m["body"] for m in out["messages"]] == ["talking to myself"]


async def test_read_cursor_and_threads(live: Live):
    room = await make_room(live, "alice", "Read room")
    first = await live.call("alice", "send", room=room, agent="Claude", body="root")
    await live.call("alice", "send", room=room, agent="Claude", body="reply", thread=first["id"])
    out = await live.call("alice", "read", room=room, agent="Reader")
    assert [m["body"] for m in out["messages"]] == ["root", "reply"]
    assert out["messages"][1]["thread"] == first["id"]
    out = await live.call("alice", "read", room=room, agent="Reader")
    assert out["messages"] == []
    out = await live.call("alice", "read", room=room, agent="Reader", since=0, thread=first["id"])
    assert len(out["messages"]) == 2


async def test_loop_cap(live: Live):
    room = await make_room(live, "alice", "Cap room")
    live.app.store.update_room(live.sub("alice"), room, agent_cap=3)
    alice = live.web("alice")

    for i in range(3):
        await live.call("alice", "send", room=room, agent="Claude", body=f"a{i}")
    err = await live.call_error("alice", "send", room=room, agent="Claude", body="a3")
    assert "paused" in err
    listed = {r["id"]: r for r in (await live.call("alice", "rooms_list"))["rooms"]}
    assert listed[room]["paused"] is True
    err = await live.call_error("alice", "send", room=room, agent="Codex", body="still paused?")
    assert "paused" in err

    # Resume resets the streak.
    assert alice.post(f"/r/{room}/resume").status_code == 303
    for i in range(3):
        await live.call("alice", "send", room=room, agent="Claude", body=f"b{i}")
    assert "paused" in await live.call_error("alice", "send", room=room, agent="Claude", body="b3")

    # A human message resets the count, but only resume unpauses.
    assert alice.post(f"/r/{room}/send", {"body": "humans talking"}).status_code == 303
    assert "paused" in await live.call_error("alice", "send", room=room, agent="Claude", body="c0")
    assert alice.post(f"/r/{room}/resume").status_code == 303
    await live.call("alice", "send", room=room, agent="Claude", body="c1")
    await live.call("alice", "send", room=room, agent="Claude", body="c2")
    assert alice.post(f"/r/{room}/send", {"body": "a person again"}).status_code == 303
    for i in range(3):
        await live.call("alice", "send", room=room, agent="Claude", body=f"d{i}")
    assert "paused" in await live.call_error("alice", "send", room=room, agent="Claude", body="d3")

    # Pause by hand also blocks agents.
    assert alice.post(f"/r/{room}/resume").status_code == 303
    assert alice.post(f"/r/{room}/pause").status_code == 303
    assert "paused" in await live.call_error("alice", "send", room=room, agent="Claude", body="e0")


async def test_retention_purge(live: Live):
    room = await make_room(live, "alice", "Retention room")
    live.app.store.update_room(live.sub("alice"), room, retention_days=7)
    old = await live.call("alice", "send", room=room, agent="Claude", body="old news")
    new = await live.call("alice", "send", room=room, agent="Claude", body="fresh")
    eight_days_ago = int(time.time()) - 8 * 86400
    live.sql("UPDATE messages SET created_at = ? WHERE id = ?", (eight_days_ago, old["id"]))

    removed = live.app.store.purge_expired()
    assert removed >= 1
    ids = {r[0] for r in live.sql("SELECT id FROM messages WHERE room_id = ?", (room,))}
    assert old["id"] not in ids and new["id"] in ids
