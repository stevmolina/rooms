"""2. Invites: single use, expiry, multi-use, revoke, owner only, MCP and web join."""

from __future__ import annotations

import re

from conftest import Live, add_member, make_room

from rooms.store import sha256


def _token(link: str) -> str:
    return link.rsplit("/join/", 1)[1]


async def test_single_use(live: Live):
    room = await make_room(live, "alice")
    inv = await live.call("alice", "room_invite", room=room, uses=1)
    assert inv["invite"].startswith(f"{live.url}/join/")
    joined = await live.call("bob", "room_join", invite=inv["invite"], agent="Claude")
    assert joined["room"]["id"] == room
    err = await live.call_error("carol", "room_join", invite=inv["invite"])
    assert "invite not valid" in err
    # Only the hash is stored.
    assert live.sql("SELECT COUNT(*) FROM invites WHERE token_hash = ?", (_token(inv["invite"]),)) == [(0,)]
    assert live.sql("SELECT COUNT(*) FROM invites WHERE token_hash = ?", (sha256(_token(inv["invite"])),)) == [(1,)]


async def test_expired(live: Live):
    room = await make_room(live, "alice")
    inv = await live.call("alice", "room_invite", room=room)
    live.sql("UPDATE invites SET expires_at = 1 WHERE token_hash = ?", (sha256(_token(inv["invite"])),))
    err = await live.call_error("bob", "room_join", invite=inv["invite"])
    assert "invite not valid" in err


async def test_multi_use_counts_down(live: Live):
    room = await make_room(live, "alice")
    inv = await live.call("alice", "room_invite", room=room, uses=2)
    await live.call("bob", "room_join", invite=_token(inv["invite"]))  # bare token works too
    await live.call("bob", "room_join", invite=inv["invite"])  # already a member: no use spent
    await live.call("carol", "room_join", invite=inv["invite"])
    err = await live.call_error("dave", "room_join", invite=inv["invite"])
    assert "invite not valid" in err


async def test_revoked(live: Live):
    room = await make_room(live, "alice")
    alice = live.web("alice")
    r = alice.post(f"/r/{room}/invite", {"uses": "5", "hours": "24"})
    assert r.status_code == 200
    link = re.search(r"(http://[^<\s]+/join/[A-Za-z0-9_\-]+)", r.text).group(1)
    invite_id = live.app.store.list_invites(live.sub("alice"), room)[0]["id"]
    r = alice.post(f"/r/{room}/invites/{invite_id}/revoke")
    assert r.status_code == 303
    err = await live.call_error("bob", "room_join", invite=link)
    assert "invite not valid" in err


async def test_non_owner_cannot_invite(live: Live):
    room = await make_room(live, "alice")
    await add_member(live, room, "alice", "bob")
    err = await live.call_error("bob", "room_invite", room=room)
    assert "only the room owner" in err
    r = live.web("bob").post(f"/r/{room}/invite", {"uses": "1", "hours": "24"})
    assert r.status_code == 403


async def test_web_join(live: Live):
    room = await make_room(live, "alice", "Web join room")
    inv = await live.call("alice", "room_invite", room=room)
    path = f"/join/{_token(inv['invite'])}"

    anon = live.web(None)
    # A person in a browser (a navigation) still goes straight to sign-in.
    r = anon.get(path, headers={"Sec-Fetch-Mode": "navigate", "Accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login?next=%2Fjoin%2F")

    carol = live.web("carol")
    page = carol.get(path)
    assert page.status_code == 200
    assert "Web join room" in page.text and f"{live.label('alice')} invited you" in page.text
    assert carol.get(f"/r/{room}").status_code == 404

    r = carol.post(path)
    assert r.status_code == 303 and r.headers["location"] == f"/r/{room}"
    assert carol.get(f"/r/{room}").status_code == 200
    # The link is used up now.
    assert carol.post(path).status_code == 400
    err = await live.call_error("dave", "room_join", invite=inv["invite"])
    assert "invite not valid" in err
