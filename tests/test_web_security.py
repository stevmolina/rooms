"""6. Web security; 8. MCP auth."""

from __future__ import annotations

import re

import httpx

from conftest import Live, make_room

from rooms.store import safe_next


def test_csrf_and_origin(live: Live):
    alice = live.web("alice")
    assert alice.post("/rooms", {"name": "No csrf"}, csrf=False).status_code == 403
    assert alice.post("/rooms", {"name": "Bad csrf"}, csrf="nope").status_code == 403
    token = alice.csrf()
    r = alice.post("/rooms", {"name": "Evil origin"}, csrf=token, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    r = alice.post("/rooms", {"name": "Good"}, csrf=token, headers={"Origin": live.url})
    assert r.status_code == 303 and r.headers["location"].startswith("/r/r_")
    # A session is required: carol's csrf without carol's cookie does nothing.
    anon = live.web(None)
    assert anon.post("/rooms", {"name": "x"}, csrf=live.web("carol").csrf()).status_code == 403


async def test_message_body_is_escaped(live: Live):
    room = await make_room(live, "alice", "<b>Bold</b> room")
    alice = live.web("alice")
    assert alice.post(f"/r/{room}/send", {"body": "<script>alert(1)</script>"}).status_code == 303
    err = await live.call_error("alice", "send", room=room, agent='"><img src=x>', body="hi")
    assert "agent name" in err
    page = alice.get(f"/r/{room}")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text
    assert "<b>Bold</b>" not in page.text and "&lt;b&gt;Bold&lt;/b&gt;" in page.text

    csp = page.headers["content-security-policy"]
    nonce = re.search(r"'nonce-([^']+)'", csp).group(1)
    assert f'<script nonce="{nonce}">' in page.text
    assert "frame-ancestors 'none'" in csp and "default-src 'self'" in csp
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["referrer-policy"] == "same-origin"
    # The live update script never uses innerHTML.
    assert "innerHTML" not in page.text


def test_session_cookie_flags(live: Live):
    client = httpx.Client(base_url=live.url, follow_redirects=False)
    r = client.get("/dev/login", params={"token": live.token("alice")})
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("rooms_session=")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie


def test_next_redirect_is_relative_only(live: Live):
    client = httpx.Client(base_url=live.url, follow_redirects=False)
    for bad in ["https://evil.com", "//evil.com", "/\\evil.com", "http:/evil.com", "javascript:alert(1)"]:
        r = client.get("/dev/login", params={"token": live.token("bob"), "next": bad})
        assert r.status_code == 303 and r.headers["location"] == "/rooms", bad
    r = client.get("/dev/login", params={"token": live.token("bob"), "next": "/join/abc"})
    assert r.headers["location"] == "/join/abc"
    anon = httpx.Client(base_url=live.url, follow_redirects=False)
    r = anon.get("/login", params={"next": "//evil.com"})
    assert 'name="next" value="/rooms"' in r.text


def test_safe_next_unit():
    assert safe_next("/r/r_abc") == "/r/r_abc"
    for bad in [None, "", "evil.com", "//evil.com", "/\\evil.com", "https://x", "/a\nb"]:
        assert safe_next(bad) == "/rooms"


def test_mcp_requires_token(live: Live):
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    headers = {"Accept": "application/json, text/event-stream"}
    r = httpx.post(f"{live.url}/mcp", json=body, headers=headers)
    assert r.status_code == 401
    r = httpx.post(f"{live.url}/mcp", json=body, headers={**headers, "Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_healthz_and_pages(live: Live):
    assert httpx.get(f"{live.url}/healthz").json() == {"ok": True}
    r = httpx.get(f"{live.url}/")
    assert r.status_code == 200 and "Sign in" in r.text and "All your agents" in r.text
    assert 'href="/login?next=%2Frooms"' in r.text and "Open rooms" not in r.text
    anon = httpx.Client(base_url=live.url, follow_redirects=False)
    r = anon.get("/rooms")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Frooms"
    alice = live.web("alice")
    r = alice.get("/")
    assert r.status_code == 200 and "All your agents" in r.text
    assert 'href="/rooms">Open rooms' in r.text and "Your rooms" not in r.text
    r = alice.get("/rooms")
    assert "Your rooms" in r.text and "claude mcp add" in r.text and 'class="brand" href="/"' in r.text
