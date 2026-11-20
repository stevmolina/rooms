"""WorkOS mode wiring, in-process with a mocked WorkOS API (no network)."""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from rooms.config import Settings
from rooms.server import build_server

BASE = "https://rooms.test"


@pytest.fixture
def workos(tmp_path):
    settings = Settings(
        auth_mode="workos",
        base_url=BASE,
        authkit_domain="https://example.authkit.app",
        workos_api_key="sk_test_123",
        workos_client_id="client_123",
        db_path=str(tmp_path / "rooms.db"),
        cookie_secure=True,
    )
    mcp = build_server(settings)
    replies: list[httpx.Response] = []
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return replies.pop(0)

    mcp.rooms_app.workos_transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=mcp.http_app()), base_url=BASE)
    return client, replies, sent, mcp.rooms_app


async def _start_login(client: httpx.AsyncClient, next_url: str) -> str:
    r = await client.get("/login", params={"next": next_url})
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://api.workos.com/user_management/authorize"
    q = parse_qs(loc.query)
    assert q["client_id"] == ["client_123"]
    assert q["redirect_uri"] == [f"{BASE}/auth/callback"]
    assert q["provider"] == ["authkit"] and q["response_type"] == ["code"]
    assert "Secure" in r.headers["set-cookie"] and "HttpOnly" in r.headers["set-cookie"]
    return q["state"][0]


async def test_no_dev_login_and_mcp_401(workos):
    client, *_ = workos
    assert (await client.get("/dev/login", params={"token": "x"})).status_code == 404
    r = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")


async def test_callback_checks_state_and_verified_email(workos):
    client, replies, sent, app = workos

    state = await _start_login(client, "/join/abc")
    r = await client.get("/auth/callback", params={"code": "c1", "state": "wrong"})
    assert r.status_code == 400
    assert sent == []

    replies.append(
        httpx.Response(200, json={"user": {"id": "user_x", "email": "x@example.com", "email_verified": False}})
    )
    r = await client.get("/auth/callback", params={"code": "c1", "state": state})
    assert r.status_code == 403
    assert sent[-1].url.path == "/user_management/authenticate"

    state = await _start_login(client, "//evil.com")
    replies.append(
        httpx.Response(
            200,
            json={"user": {"id": "user_x", "email": "X@Example.com", "email_verified": True, "first_name": "Xena"}},
        )
    )
    r = await client.get("/auth/callback", params={"code": "c2", "state": state})
    assert r.status_code == 303 and r.headers["location"] == "/rooms"
    cookie = r.headers["set-cookie"]
    assert cookie.startswith("__Host-rooms_session=")
    assert "Secure" in cookie and "Path=/" in cookie and "Domain" not in cookie
    assert r.headers["strict-transport-security"] == "max-age=31536000"
    assert app.store.get_user("user_x") == {"id": "user_x", "email": "x@example.com", "name": "Xena"}
    home = await client.get("/rooms")
    assert "Your rooms" in home.text and "Xena" in home.text


async def test_callback_bad_workos_answers_are_400(workos):
    client, replies, sent, _ = workos
    for reply in (
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json=["not", "a", "dict"]),
        httpx.Response(200, json={"user": "nope"}),
        httpx.Response(500, content=b"oops"),
    ):
        state = await _start_login(client, "/")
        replies.append(reply)
        r = await client.get("/auth/callback", params={"code": "c", "state": state})
        assert r.status_code == 400, reply
        assert "Sign-in failed" in r.text


async def test_agent_api_in_workos_mode(workos):
    client, replies, sent, _ = workos
    # Junk, MCP-looking and agent-looking tokens: 401, and no call to WorkOS.
    for bad in ("ra_wrong", "eyJhbGciOiJSUzI1NiJ9.e30.x", "rw_wrong"):
        r = await client.get("/api/rooms", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401
    r = await client.post("/api/agent/start", json={"invite": "nope", "agent": "Claude"})
    assert r.status_code == 400 and r.json()["error"] == "invite not valid"
    # The approve page goes through the normal WorkOS sign-in and keeps the code.
    r = await client.get("/device", params={"code": "BCDF-GHJK"})
    assert r.status_code == 303 and r.headers["location"] == "/login?next=%2Fdevice%3Fcode%3DBCDF-GHJK"
    assert sent == []


def _jwt(claims: dict) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


async def test_logout_ends_the_workos_session_so_another_account_can_sign_in(workos):
    client, replies, sent, app = workos

    async def sign_in(user_id: str, email: str, sid: str) -> None:
        state = await _start_login(client, "/")
        replies.append(
            httpx.Response(
                200,
                json={
                    "user": {"id": user_id, "email": email, "email_verified": True, "first_name": user_id},
                    "access_token": _jwt({"sub": user_id, "sid": sid}),
                },
            )
        )
        r = await client.get("/auth/callback", params={"code": "c", "state": state})
        assert r.status_code == 303

    await sign_in("user_a", "a@example.com", "session_A")
    home = await client.get("/rooms")
    csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
    r = await client.post("/logout", data={"csrf": csrf}, headers={"origin": BASE})
    # A page that moves on to WorkOS by itself (a redirect would be blocked by CSP form-action).
    assert r.status_code == 200 and "Signing you out" in r.text and "Sign out" not in r.text
    refresh = r.headers["refresh"]
    assert refresh.startswith("0;url=")
    loc = urlsplit(refresh.removeprefix("0;url="))
    assert f"{loc.scheme}://{loc.netloc}{loc.path}" == "https://api.workos.com/user_management/sessions/logout"
    assert parse_qs(loc.query) == {"session_id": ["session_A"], "return_to": [f"{BASE}/"]}

    # A second account can now sign in on the same browser.
    await sign_in("user_b", "b@example.com", "session_B")
    home = await client.get("/rooms")
    assert "user_b" in home.text and "user_a" not in home.text


async def test_logout_without_a_workos_session_goes_home(workos):
    client, replies, _, _ = workos
    state = await _start_login(client, "/")
    replies.append(
        httpx.Response(200, json={"user": {"id": "user_c", "email": "c@example.com", "email_verified": True}})
    )
    await client.get("/auth/callback", params={"code": "c", "state": state})
    home = await client.get("/rooms")
    csrf = re.search(r'name="csrf" value="([^"]+)"', home.text).group(1)
    r = await client.post("/logout", data={"csrf": csrf}, headers={"origin": BASE})
    assert r.status_code == 303 and r.headers["location"] == "/"
