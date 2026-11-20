"""7. Wake API; WorkOS verifier unit test."""

from __future__ import annotations

import re
import time

import httpx
import pytest
from fastmcp.server.auth import TokenVerifier
from fastmcp.server.auth.auth import AccessToken

from conftest import Live, add_member, make_room

from rooms.auth import WorkOSUserVerifier


def _wake_token(live: Live, who: str, agent: str) -> str:
    r = live.web(who).post("/settings/wake", {"agent": agent})
    assert r.status_code == 200
    return re.search(r"ROOMS_WAKE_TOKEN=(rw_[A-Za-z0-9_\-]+)", r.text).group(1)


def _api_wait(live: Live, token: str, **params) -> httpx.Response:
    return httpx.get(
        f"{live.url}/api/wait", params=params, headers={"Authorization": f"Bearer {token}"}, timeout=70
    )


async def test_wake_api(live: Live):
    allowed = await make_room(live, "alice", "Auto room")
    other = await make_room(live, "alice", "Manual room")
    await add_member(live, allowed, "alice", "bob")
    await add_member(live, other, "alice", "bob")
    token = _wake_token(live, "bob", "Waker")
    bob = live.web("bob")
    agent_id = live.app.store.agent(live.sub("bob"), "Waker")["id"]

    assert _api_wait(live, "rw_wrong", timeout=0).status_code == 401
    assert _api_wait(live, live.token("bob"), timeout=0).status_code == 401  # an MCP token is not a wake token
    assert httpx.get(f"{live.url}/api/wait").status_code == 401

    m1 = await live.call("alice", "send", room=allowed, agent="Claude", body="anyone?")
    await live.call("alice", "send", room=other, agent="Claude", body="not for auto-reply")
    # No auto-reply yet: nothing.
    assert _api_wait(live, token, timeout=0.3).json()["messages"] == []

    assert bob.post(f"/r/{allowed}/autoreply", {"agent_id": str(agent_id), "hours": "1"}).status_code == 303
    got = _api_wait(live, token, timeout=2).json()["messages"]
    assert [m["id"] for m in got] == [m1["id"]]
    assert got[0]["room"] == allowed

    # `after` is respected.
    assert _api_wait(live, token, timeout=0.3, after=m1["id"]).json()["messages"] == []

    # The cursor did not move: reading as the agent still shows the message.
    out = await live.call("bob", "read", room=allowed, agent="Waker")
    assert m1["id"] in [m["id"] for m in out["messages"]]
    # After reading, the cursor is past it.
    assert _api_wait(live, token, timeout=0.3).json()["messages"] == []

    # Expired permission: nothing.
    m2 = await live.call("alice", "send", room=allowed, agent="Claude", body="second")
    assert [m["id"] for m in _api_wait(live, token, timeout=1).json()["messages"]] == [m2["id"]]
    live.sql(
        "UPDATE room_agents SET auto_reply_until = ? WHERE room_id = ? AND agent_id = ?",
        (int(time.time()) - 5, allowed, agent_id),
    )
    assert _api_wait(live, token, timeout=0.3).json()["messages"] == []

    # Wake tokens do not work on /mcp.
    r = httpx.post(
        f"{live.url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
    )
    assert r.status_code == 401

    # Revoked: 401.
    tid = live.app.store.list_wake_tokens(live.sub("bob"))[0]["id"]
    assert bob.post(f"/settings/wake/{tid}/revoke").status_code == 303
    assert _api_wait(live, token, timeout=0).status_code == 401


async def test_wake_api_wakes_fast(live: Live):
    import asyncio

    room = await make_room(live, "alice", "Fast wake")
    await add_member(live, room, "alice", "bob")
    token = _wake_token(live, "bob", "Speedy")
    agent_id = live.app.store.agent(live.sub("bob"), "Speedy")["id"]
    live.app.store.set_auto_reply(live.sub("bob"), room, agent_id, 8)

    async with httpx.AsyncClient(timeout=30) as client:
        task = asyncio.create_task(
            client.get(
                f"{live.url}/api/wait",
                params={"timeout": 20},
                headers={"Authorization": f"Bearer {token}"},
            )
        )
        await asyncio.sleep(0.3)
        await live.call("alice", "send", room=room, agent="Claude", body="wake up")
        t0 = time.monotonic()
        r = await task
        assert time.monotonic() - t0 < 1.0
        assert [m["body"] for m in r.json()["messages"]] == ["wake up"]


# ---- WorkOS verifier -------------------------------------------------


class FakeInner(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        if token != "good-jwt":
            return None
        return AccessToken(
            token=token,
            client_id="c",
            scopes=[],
            claims={"sub": "user_01", "email": "forged@evil.com"},
        )


def _verifier(responses: list[httpx.Response]) -> tuple[WorkOSUserVerifier, list[httpx.Request]]:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responses[min(len(calls) - 1, len(responses) - 1)]

    return WorkOSUserVerifier(FakeInner(), "sk_test", transport=httpx.MockTransport(handler)), calls


@pytest.mark.asyncio
async def test_verifier_rejects_unverified_email():
    v, calls = _verifier([httpx.Response(200, json={"id": "user_01", "email": "a@b.com", "email_verified": False})])
    assert await v.verify_token("good-jwt") is None
    assert calls[0].url.path == "/user_management/users/user_01"
    assert calls[0].headers["authorization"] == "Bearer sk_test"


@pytest.mark.asyncio
async def test_verifier_does_not_cache_failures_but_caches_success():
    ok = httpx.Response(
        200, json={"id": "user_01", "email": "Ana@Example.com", "email_verified": True, "first_name": "Ana"}
    )
    v, calls = _verifier([httpx.Response(500), ok])
    assert await v.verify_token("good-jwt") is None
    token = await v.verify_token("good-jwt")  # retried, not cached
    assert len(calls) == 2
    assert token is not None
    assert token.claims["email"] == "ana@example.com"  # from the API, never the token claim
    assert token.claims["name"] == "Ana"
    await v.verify_token("good-jwt")
    assert len(calls) == 2  # success cached
    assert await v.verify_token("bad-jwt") is None


@pytest.mark.asyncio
async def test_verifier_network_error_rejected():
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    v = WorkOSUserVerifier(FakeInner(), "sk_test", transport=httpx.MockTransport(boom))
    assert await v.verify_token("good-jwt") is None


@pytest.mark.asyncio
async def test_verifier_caches_definite_no_for_60s(monkeypatch):
    import rooms.auth as auth_mod

    v, calls = _verifier([httpx.Response(200, json={"id": "user_01", "email": "a@b.com", "email_verified": False})])
    assert await v.verify_token("good-jwt") is None
    assert await v.verify_token("good-jwt") is None
    assert len(calls) == 1  # unverified answer cached
    real = time.time
    monkeypatch.setattr(auth_mod.time, "time", lambda: real() + 61)
    assert await v.verify_token("good-jwt") is None
    assert len(calls) == 2  # expired after 60 s

    v, calls = _verifier([httpx.Response(404), httpx.Response(404)])
    monkeypatch.setattr(auth_mod.time, "time", real)
    assert await v.verify_token("good-jwt") is None
    assert await v.verify_token("good-jwt") is None
    assert len(calls) == 1  # user not found cached

    v, calls = _verifier([httpx.Response(503), httpx.Response(502)])
    assert await v.verify_token("good-jwt") is None
    assert await v.verify_token("good-jwt") is None
    assert len(calls) == 2  # 5xx never cached


# ---- JWT checks with a locally generated RS256 key ----------------------

ISS = "https://example.authkit.app"
AUD = "https://rooms.test/mcp"


def _jwt_setup():
    from fastmcp.server.auth.providers.jwt import JWTVerifier, RSAKeyPair

    keys = RSAKeyPair.generate()
    inner = JWTVerifier(public_key=keys.public_key, issuer=ISS, audience=AUD, algorithm="RS256")
    ok = httpx.Response(200, json={"id": "user_01", "email": "ana@example.com", "email_verified": True})
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return ok

    verifier = WorkOSUserVerifier(inner, "sk_test", transport=httpx.MockTransport(handler))
    return keys, inner, verifier, calls


def _hs256(claims: dict, secret: bytes) -> str:
    import base64
    import hashlib
    import hmac
    import json

    def b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


@pytest.mark.asyncio
async def test_jwt_issuer_audience_and_algorithm():
    keys, inner, verifier, calls = _jwt_setup()
    good = keys.create_token(subject="user_01", issuer=ISS, audience=AUD)
    assert await inner.verify_token(good) is not None
    token = await verifier.verify_token(good)
    assert token is not None and token.claims["email"] == "ana@example.com"
    assert len(calls) == 1

    wrong_aud = keys.create_token(subject="user_01", issuer=ISS, audience="https://other.test/mcp")
    wrong_iss = keys.create_token(subject="user_01", issuer="https://evil.authkit.app", audience=AUD)
    now = int(time.time())
    hs256 = _hs256(
        {"sub": "user_01", "iss": ISS, "aud": AUD, "iat": now, "exp": now + 600},
        keys.public_key.encode(),  # classic RS/HS confusion: the public key as HMAC secret
    )
    for bad in (wrong_aud, wrong_iss, hs256):
        assert await inner.verify_token(bad) is None
        assert await verifier.verify_token(bad) is None
    assert len(calls) == 1  # bad tokens never reach the WorkOS lookup
