"""The room page: who wrote what, reply quotes, the Room panel, the invite flash, the timeline."""

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


async def test_room_page_includes_the_timeline_rail(live: Live):
    room = await make_room(live, "carol", "Rail room")
    page = live.web("carol").get(f"/r/{room}").text
    assert 'id="timeline"' in page and 'aria-label="Conversation timeline"' in page
    assert 'id="timeline-new"' in page and 'class="rail-new"' in page
    assert ".rail-win{" in page and "@media (max-width:1180px)" in page
    assert "data-unread-from=" not in page  # first visit, even with no messages


async def test_timeline_marks_unread_only_after_a_prior_visit(live: Live):
    room = await make_room(live, "carol", "Unread rail")
    await add_member(live, room, "carol", "dave")
    first = live.web("carol").post(f"/r/{room}/send", {"body": "Starting here."})
    assert first.status_code == 303
    # Carol has now read the room (sending advances her cursor). Dave posts two more.
    dave = live.web("dave")
    assert dave.post(f"/r/{room}/send", {"body": "First new note."}).status_code == 303
    assert dave.post(f"/r/{room}/send", {"body": "Second new note."}).status_code == 303
    ids = [r[0] for r in live.sql("SELECT id FROM messages WHERE room_id = ? ORDER BY id", (room,))]
    assert len(ids) == 3
    page = live.web("carol").get(f"/r/{room}").text
    assert f'data-unread-from="{ids[1]}"' in page
    assert 'data-unread-count="2"' in page
    # Opening the page marks those as read, so a reload has no unread marker.
    again = live.web("carol").get(f"/r/{room}").text
    assert "data-unread-from=" not in again


async def test_timeline_skips_unread_on_a_first_visit_to_a_busy_room(live: Live):
    room = await make_room(live, "carol", "Busy first visit")
    await add_member(live, room, "carol", "dave")
    assert live.web("dave").post(f"/r/{room}/send", {"body": "Already here."}).status_code == 303
    page = live.web("carol").get(f"/r/{room}").text
    assert "Already here." in page
    assert "data-unread-from=" not in page
