"""Agents keep talking: the loop cap posts a note instead of pausing, an hourly budget
is the backstop, and the owner's agent can resume and change settings over MCP."""

from __future__ import annotations

import re
import time

from conftest import Live, add_member, make_room


async def test_cap_still_pauses_with_setting_off(live: Live):
    room = await make_room(live, "bob", "Off room")
    out = await live.call("bob", "room_settings", room=room, agent_cap=3)
    assert out["room"]["keep_talking"] is False
    for i in range(3):
        await live.call("bob", "send", room=room, agent="Claude", body=f"m{i}")
    err = await live.call_error("bob", "send", room=room, agent="Claude", body="m3")
    assert "paused" in err and "tell your person" in err
    listed = {r["id"]: r for r in (await live.call("bob", "rooms_list"))["rooms"]}
    assert listed[room]["paused"] is True


async def test_keep_talking_skips_pause_and_notes_once(live: Live):
    room = await make_room(live, "bob", "Talk room")
    await add_member(live, room, "bob", "carol")
    out = await live.call("bob", "room_settings", room=room, agent_cap=3, keep_talking=True)
    assert out["room"]["keep_talking"] is True and out["room"]["agent_cap"] == 3

    # Two agents of two people, 25 messages in a row: no pause.
    for i in range(25):
        who, agent = ("bob", "Claude") if i % 2 == 0 else ("carol", "Codex")
        await live.call(who, "send", room=room, agent=agent, body=f"turn {i}")
    listed = {r["id"]: r for r in (await live.call("bob", "rooms_list"))["rooms"]}
    assert listed[room]["paused"] is False

    rows = live.sql(
        "SELECT body, author_agent_id FROM messages WHERE room_id = ? AND system = 1 ORDER BY id", (room,)
    )
    # One note each time a stretch of 3 overflows: before messages 4, 7, ... 25.
    assert len(rows) == 8
    assert rows[0][0].startswith("3 agent messages in a row; agents keep talking is on")
    assert rows[0][1] is None

    # Each crossing posts exactly one note: the first crossing (4th message) has one before it.
    bodies = [
        r[0]
        for r in live.sql("SELECT body FROM messages WHERE room_id = ? ORDER BY id LIMIT 5", (room,))
    ]
    assert bodies[:3] == ["turn 0", "turn 1", "turn 2"]
    assert bodies[3].startswith("3 agent messages in a row") and bodies[4] == "turn 3"

    # Agents see the note as from "Rooms", kind system.
    out = await live.call("bob", "read", room=room, agent="Reader", since=0)
    notes = [m for m in out["messages"] if m["kind"] == "system"]
    assert notes and all(m["author"] == "Rooms" for m in notes)

    # Turning it off brings the pause back.
    await live.call("bob", "room_settings", room=room, keep_talking=False)
    # The 25th message started a new stretch of 1, so two more fit before the cap.
    for i in range(2):
        await live.call("bob", "send", room=room, agent="Claude", body=f"x{i}")
    err = await live.call_error("bob", "send", room=room, agent="Claude", body="over")
    assert "paused" in err


async def test_hourly_budget_refuses_without_pausing(live: Live):
    room = await make_room(live, "bob", "Budget room")
    await live.call("bob", "room_settings", room=room, agent_cap=2, keep_talking=True, hourly_budget=5)
    for i in range(5):
        await live.call("bob", "send", room=room, agent="Claude", body=f"b{i}")
    err = await live.call_error("bob", "send", room=room, agent="Claude", body="one too many")
    assert "hourly budget" in err and "not paused" in err and "Next: Wait about" in err
    listed = {r["id"]: r for r in (await live.call("bob", "rooms_list"))["rooms"]}
    assert listed[room]["paused"] is False

    # A person can still post.
    alice = live.web("bob")
    assert alice.post(f"/r/{room}/send", {"body": "I am here"}).status_code == 303

    # Messages older than an hour no longer count.
    hour_ago = int(time.time()) - 3601
    live.sql(
        "UPDATE messages SET created_at = ? WHERE room_id = ? AND author_agent_id IS NOT NULL",
        (hour_ago, room),
    )
    await live.call("bob", "send", room=room, agent="Claude", body="back again")


async def test_budget_only_applies_with_keep_talking(live: Live):
    room = await make_room(live, "bob", "Budget off room")
    await live.call("bob", "room_settings", room=room, agent_cap=50, hourly_budget=2)
    for i in range(4):
        await live.call("bob", "send", room=room, agent="Claude", body=f"c{i}")


async def test_resume_and_settings_tools_are_owner_only(live: Live):
    room = await make_room(live, "bob", "Owner room")
    await add_member(live, room, "bob", "carol")
    await live.call("bob", "room_settings", room=room, agent_cap=1)
    await live.call("carol", "send", room=room, agent="Codex", body="hi")
    assert "paused" in await live.call_error("carol", "send", room=room, agent="Codex", body="again")

    err = await live.call_error("carol", "room_resume", room=room)
    assert "only the room owner" in err
    err = await live.call_error("carol", "room_settings", room=room, keep_talking=True)
    assert "only the room owner" in err
    # A non-member gets not found.
    assert "not found" in await live.call_error("dave", "room_resume", room=room)
    assert "not found" in await live.call_error("dave", "room_settings", room=room, agent_cap=5)

    out = await live.call("bob", "room_resume", room=room)
    assert out["room"]["paused"] is False
    await live.call("carol", "send", room=room, agent="Codex", body="free again")

    out = await live.call(
        "bob", "room_settings", room=room, agent_cap=500, keep_talking=True, hourly_budget=300
    )
    r = out["room"]
    assert (r["agent_cap"], r["keep_talking"], r["agent_hourly_budget"]) == (500, True, 300)


def test_settings_form_toggles_keep_talking(live: Live):
    import asyncio

    room = asyncio.run(make_room(live, "bob", "Form room"))
    alice = live.web("bob")
    page = alice.get(f"/r/{room}").text
    assert 'name="keep_talking"' in page and 'name="agent_hourly_budget"' in page
    assert not re.search(r'name="keep_talking"[^>]*checked', page)

    form = {"notice": "", "agent_cap": "500", "retention_days": "90", "keep_talking_field": "1",
            "keep_talking": "1", "agent_hourly_budget": "200"}
    assert alice.post(f"/r/{room}/settings", form).status_code == 303
    page = alice.get(f"/r/{room}").text
    assert re.search(r'name="keep_talking"[^>]*checked', page)
    assert "Agents keep talking" in page
    summary = live.app.store.room_summary(live.app.store.room(live.sub("bob"), room))
    assert summary["keep_talking"] is True and summary["agent_hourly_budget"] == 200

    # An unchecked box sends nothing; the hidden field turns it off.
    form.pop("keep_talking")
    assert alice.post(f"/r/{room}/settings", form).status_code == 303
    summary = live.app.store.room_summary(live.app.store.room(live.sub("bob"), room))
    assert summary["keep_talking"] is False

    bob = live.web("carol")
    asyncio.run(add_member(live, room, "bob", "carol"))
    r = bob.post(f"/r/{room}/settings", {**form, "keep_talking": "1"})
    assert r.status_code in (403, 400)
    summary = live.app.store.room_summary(live.app.store.room(live.sub("bob"), room))
    assert summary["keep_talking"] is False


async def test_note_does_not_wake_agents_or_count_as_unseen(live: Live):
    room = await make_room(live, "bob", "Quiet note room")
    await live.call("bob", "room_settings", room=room, agent_cap=2, keep_talking=True)
    await live.call("bob", "send", room=room, agent="Claude", body="one")
    await live.call("bob", "send", room=room, agent="Claude", body="two")
    out = await live.call("bob", "send", room=room, agent="Claude", body="three")  # crosses the cap
    assert "arrived before yours" not in out["next"]
    assert live.sql("SELECT COUNT(*) FROM messages WHERE room_id = ? AND system = 1", (room,))[0][0] == 1
    # Another agent waiting sees the three agent messages, not the note.
    out = await live.call("bob", "wait", agent="Other", rooms=[room], timeout=0)
    assert [m["body"] for m in out["messages"]] == ["one", "two", "three"]


async def test_budget_wait_hint_after_lowering_budget(live: Live):
    room = await make_room(live, "bob", "Lowered budget room")
    await live.call("bob", "room_settings", room=room, agent_cap=500, keep_talking=True, hourly_budget=10)
    for i in range(6):
        await live.call("bob", "send", room=room, agent="Claude", body=f"l{i}")
    now = int(time.time())
    # Spread them out: the oldest 50 minutes ago, then 40, 30, 20, 10 and 0.
    ids = [r[0] for r in live.sql("SELECT id FROM messages WHERE room_id = ? ORDER BY id", (room,))]
    for n, mid in enumerate(ids):
        live.sql("UPDATE messages SET created_at = ? WHERE id = ?", (now - (50 - 10 * n) * 60, mid))
    await live.call("bob", "room_settings", room=room, hourly_budget=3)
    err = await live.call_error("bob", "send", room=room, agent="Claude", body="too soon")
    # 6 sent, budget 3: four must age out, the 4th oldest (20 min ago) frees a slot in ~40 min.
    m = re.search(r"in about (\d+) min", err)
    assert m and 39 <= int(m.group(1)) <= 41, err
