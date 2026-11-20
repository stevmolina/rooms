"""The room page: who wrote what, reply quotes, the Room panel and the invite flash."""

from __future__ import annotations

from conftest import Live, add_member, make_room


async def test_messages_show_names_roles_and_reply_quotes(live: Live):
    room = await make_room(live, "alice", "Page room", notice="Ask your person first.")
    await add_member(live, room, "alice", "bob")
    first = await live.call("alice", "send", room=room, agent="Claude", body="Hola, what are you working on?")
    await live.call("bob", "send", room=room, agent="Codex", body="The engine PRs.", thread=first["id"])
    page = live.web("alice").get(f"/r/{room}").text

    assert "your agent" in page and "Bob&#x27;s agent" in page
    assert "↳ Claude: Hola, what are you working on?" in page  # quote of the parent, not "#1"
    assert '<strong>Room rules</strong> Ask your person first.' in page
    assert 'id="room-panel"' in page and 'href="#room-panel"' in page
    assert f'data-reply="{first["id"]}"' in page  # replies join the thread root


async def test_invite_link_opens_in_the_room_panel(live: Live):
    room = await make_room(live, "alice", "Invite room")
    alice = live.web("alice")
    page = alice.post(f"/r/{room}/invite", {"uses": 1, "hours": 24}).text
    assert 'class="panel open"' in page and "/join/" in page
    assert page.index('id="room-panel"') < page.index("/join/")  # the link sits inside the panel
