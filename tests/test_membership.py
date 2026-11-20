"""1. Membership isolation, over MCP and the web page."""

from __future__ import annotations

import asyncio

from conftest import Live, add_member, make_room

MISSING = "r_doesnotexist"


async def test_non_member_mcp_errors_match_missing_room(live: Live):
    room = await make_room(live, "alice", "Secret plans")
    await live.call("alice", "send", room=room, agent="Claude", body="hello")

    for tool, args in [
        ("read", {"agent": "Claude"}),
        ("send", {"agent": "Claude", "body": "let me in"}),
        ("room_invite", {}),
    ]:
        err_real = await live.call_error("carol", tool, room=room, **args)
        err_missing = await live.call_error("carol", tool, room=MISSING, **args)
        assert err_real == err_missing
        assert "room not found" in err_real

    err_real = await live.call_error("carol", "wait", agent="Claude", rooms=[room], timeout=0.2)
    err_missing = await live.call_error("carol", "wait", agent="Claude", rooms=[MISSING], timeout=0.2)
    assert err_real == err_missing

    listed = await live.call("carol", "rooms_list")
    assert room not in {r["id"] for r in listed["rooms"]}


async def test_non_member_wait_never_receives(live: Live):
    room = await make_room(live, "alice", "Quiet room")
    waiter = asyncio.create_task(live.call("carol", "wait", agent="Snoop", timeout=1.5))
    await asyncio.sleep(0.3)
    await live.call("alice", "send", room=room, agent="Claude", body="carol must not see this")
    out = await waiter
    assert all(m["room"] != room for m in out["messages"])


async def test_non_member_web_matches_missing_room(live: Live):
    room = await make_room(live, "alice", "Web secret")
    carol = live.web("carol")

    real = carol.get(f"/r/{room}")
    missing = carol.get(f"/r/{MISSING}")
    assert real.status_code == missing.status_code == 404
    assert real.text == missing.text

    real = carol.get(f"/r/{room}/poll", params={"after": 0})
    missing = carol.get(f"/r/{MISSING}/poll", params={"after": 0})
    assert real.status_code == missing.status_code == 404
    assert real.json() == missing.json()

    forms = {
        "send": {"body": "hi"},
        "pause": {},
        "resume": {},
        "settings": {"notice": "pwned"},
        "invite": {"uses": "1", "hours": "24"},
        "autoreply": {"agent_id": "1", "hours": "1"},
        "leave": {},
    }
    for action, data in forms.items():
        real = carol.post(f"/r/{room}/{action}", data)
        missing = carol.post(f"/r/{MISSING}/{action}", data)
        assert real.status_code == missing.status_code == 404, action
        assert real.text == missing.text, action

    # Nothing changed for alice.
    out = await live.call("alice", "read", room=room, agent="Claude", since=0)
    assert out["room"]["notice"] == ""
    assert out["room"]["paused"] is False


async def test_agents_belong_to_their_user(live: Live):
    room = await make_room(live, "alice", "Two Claudes")
    await add_member(live, room, "alice", "bob")

    a = await live.call("alice", "send", room=room, agent="twin", body="from alice's twin")
    waiter = asyncio.create_task(live.call("bob", "wait", agent="twin", rooms=[room], timeout=5))
    await asyncio.sleep(0.2)
    b = await live.call("bob", "send", room=room, agent="twin", body="from bob's twin")
    got = await waiter

    assert a["message"]["author"] == live.label("alice", "twin")
    assert b["message"]["author"] == live.label("bob", "twin")
    # Bob's "twin" is a different agent, so alice's twin counts as "someone else".
    assert [m["id"] for m in got["messages"]] == [a["id"]]

    alice_agent = live.app.store.agent(live.sub("alice"), "twin")
    bob_agent = live.app.store.agent(live.sub("bob"), "twin")
    assert alice_agent["id"] != bob_agent["id"]
    rows = live.sql("SELECT author_user_id, author_agent_id FROM messages WHERE id = ?", (b["id"],))
    assert rows == [(live.sub("bob"), bob_agent["id"])]
