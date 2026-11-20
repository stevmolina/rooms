"""Settings from the environment. Read once, when the server is built."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def _bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DevUser:
    sub: str
    email: str
    name: str


@dataclass(frozen=True)
class Settings:
    auth_mode: str = "workos"
    base_url: str = "http://localhost:8080"
    authkit_domain: str = ""
    workos_api_key: str = ""
    workos_client_id: str = ""
    db_path: str = "./data/rooms.db"
    dev_tokens: dict[str, DevUser] = field(default_factory=dict)
    cookie_secure: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    @property
    def mcp_url(self) -> str:
        return self.base_url + "/mcp"

    @property
    def origin(self) -> str:
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def dev(self) -> bool:
        return self.auth_mode == "dev"

    @classmethod
    def from_env(cls) -> Settings:
        mode = os.getenv("AUTH_MODE", "workos").strip() or "workos"
        if mode not in {"workos", "dev"}:
            raise SystemExit(f"unknown AUTH_MODE {mode!r}; use 'workos' or 'dev'")
        s = cls(
            auth_mode=mode,
            base_url=os.getenv("MCP_BASE_URL", "http://localhost:8080").strip().rstrip("/"),
            authkit_domain=os.getenv("AUTHKIT_DOMAIN", "").strip().rstrip("/"),
            workos_api_key=os.getenv("WORKOS_API_KEY", "").strip(),
            workos_client_id=os.getenv("WORKOS_CLIENT_ID", "").strip(),
            db_path=os.getenv("ROOMS_DB", "./data/rooms.db").strip() or "./data/rooms.db",
            dev_tokens=parse_dev_tokens(os.getenv("DEV_TOKENS", "")) if mode == "dev" else {},
            cookie_secure=_bool(os.getenv("COOKIE_SECURE"), True),
            host=os.getenv("HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=int(os.getenv("PORT", "8080")),
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )
        s.validate()
        return s

    def validate(self) -> None:
        if self.auth_mode == "workos":
            missing = [
                name
                for name, value in (
                    ("MCP_BASE_URL", os.getenv("MCP_BASE_URL", "").strip()),
                    ("AUTHKIT_DOMAIN", self.authkit_domain),
                    ("WORKOS_API_KEY", self.workos_api_key),
                    ("WORKOS_CLIENT_ID", self.workos_client_id),
                )
                if not value
            ]
            if missing:
                raise SystemExit("missing required env vars: " + ", ".join(missing))
        elif not self.dev_tokens:
            raise SystemExit("AUTH_MODE=dev needs DEV_TOKENS=token:sub:email:Name,...")


def parse_dev_tokens(raw: str) -> dict[str, DevUser]:
    """Parse "token:sub:email:Name,token2:sub2:email2:Name 2"."""
    out: dict[str, DevUser] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":", 3)
        if len(parts) != 4 or not all(p.strip() for p in parts):
            raise SystemExit(f"bad DEV_TOKENS entry {item.split(':', 1)[0]!r}: want token:sub:email:Name")
        token, sub, email, name = (p.strip() for p in parts)
        out[token] = DevUser(sub=sub, email=email.lower(), name=name)
    return out
