"""MCP sign-in: WorkOS AuthKit tokens (or static dev tokens), then a verified email.

The WorkOS access token carries no trustworthy email, so the user is looked up
through the WorkOS API. Only `email_verified is True` counts. Good lookups are
cached for 10 minutes. A definite "no" (email not verified, user not found) is
cached for 60 seconds. Network errors and 5xx answers are never cached, so a
WorkOS hiccup does not lock anyone out.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastmcp.server.auth import AuthProvider, TokenVerifier
from fastmcp.server.auth.auth import AccessToken
from fastmcp.server.auth.providers.jwt import JWTVerifier, StaticTokenVerifier
from fastmcp.server.auth.providers.workos import AuthKitProvider

from .config import Settings

log = logging.getLogger("rooms.auth")

USER_CACHE_TTL = 600
DENY_CACHE_TTL = 60
WORKOS_API = "https://api.workos.com"


def display_name(user: dict[str, Any]) -> str:
    first = (user.get("first_name") or "").strip()
    last = (user.get("last_name") or "").strip()
    name = " ".join(p for p in (first, last) if p)
    return name or (user.get("email") or "").split("@", 1)[0] or "someone"


class WorkOSUserVerifier(TokenVerifier):
    """Verify the AuthKit JWT, then require a verified WorkOS user."""

    def __init__(
        self,
        inner: TokenVerifier,
        api_key: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        super().__init__()
        self.inner = inner
        self.api_key = api_key
        self.transport = transport
        self._cache: dict[str, tuple[float, dict[str, str] | None]] = {}

    async def lookup(self, sub: str) -> dict[str, str] | None:
        """Return {sub, email, name} for a verified user, or None."""
        if not sub:
            return None
        hit = self._cache.get(sub)
        if hit and hit[0] > time.time():
            return hit[1]
        try:
            async with httpx.AsyncClient(timeout=10, transport=self.transport) as client:
                r = await client.get(
                    f"{WORKOS_API}/user_management/users/{sub}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.HTTPError as exc:
            log.warning("workos user lookup error %s", type(exc).__name__)
            return None
        if r.status_code == 404:
            log.info("workos user not found sub=%s", sub)
            self._cache[sub] = (time.time() + DENY_CACHE_TTL, None)
            return None
        if r.status_code != 200:
            log.warning("workos user lookup failed status=%s", r.status_code)
            return None  # not cached
        try:
            user = r.json()
        except ValueError:
            return None
        if not isinstance(user, dict):
            return None
        email = (user.get("email") or "").strip().lower()
        if user.get("email_verified") is not True or not email:
            log.info("denied unverified sub=%s", sub)
            self._cache[sub] = (time.time() + DENY_CACHE_TTL, None)
            return None
        identity = {"sub": sub, "email": email, "name": display_name(user)}
        self._cache[sub] = (time.time() + USER_CACHE_TTL, identity)
        return identity

    async def verify_token(self, token: str) -> AccessToken | None:
        verified = await self.inner.verify_token(token)
        if verified is None:
            return None
        claims = dict(verified.claims or {})
        identity = await self.lookup(str(claims.get("sub") or ""))
        if identity is None:
            return None
        # Never trust email or name claims from the token itself.
        claims.update(identity)
        return verified.model_copy(update={"claims": claims})


def dev_verifier(settings: Settings) -> TokenVerifier:
    return StaticTokenVerifier(
        tokens={
            token: {
                "client_id": "dev-client",
                "scopes": [],
                "sub": u.sub,
                "email": u.email,
                "name": u.name,
            }
            for token, u in settings.dev_tokens.items()
        }
    )


def build_auth(settings: Settings) -> AuthProvider:
    if settings.dev:
        return dev_verifier(settings)
    inner = JWTVerifier(
        jwks_uri=f"{settings.authkit_domain}/oauth2/jwks",
        issuer=settings.authkit_domain,
        algorithm="RS256",
        audience=settings.mcp_url,
    )
    verifier = WorkOSUserVerifier(inner, settings.workos_api_key)
    return AuthKitProvider(
        authkit_domain=settings.authkit_domain,
        base_url=settings.base_url,
        client_id=settings.workos_client_id or None,
        token_verifier=verifier,
        resource_name="Rooms",
    )
