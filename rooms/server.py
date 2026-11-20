"""Rooms server: MCP at /mcp plus the web page, in one process.

    python -m rooms.server
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastmcp import FastMCP
from starlette.middleware import Middleware

from .auth import build_auth
from .config import Settings
from .db import Database
from .notify import Notifier
from .store import Store
from .tools import INSTRUCTIONS, Listening, register_tools
from .web import register_web

log = logging.getLogger("rooms")
access_log = logging.getLogger("rooms.access")

DEV_HOSTS = {"localhost", "127.0.0.1", "::1"}
_SECRET_PATHS = re.compile(r"^/join/[^/]+")

PURGE_EVERY = 3600


@dataclass
class App:
    settings: Settings
    store: Store
    notifier: Notifier = field(default_factory=Notifier)
    listening: Listening = field(default_factory=Listening)
    workos_transport: httpx.AsyncBaseTransport | None = None


async def _purge_loop(store: Store) -> None:
    while True:
        try:
            removed = store.purge_expired()
            if removed:
                log.info("retention purge removed %s messages", removed)
        except Exception:  # keep the loop alive; log and retry next hour
            log.exception("retention purge failed")
        await asyncio.sleep(PURGE_EVERY)


def redact_path(path: str) -> str:
    """For logs: no query string (OAuth code/state), no invite tokens."""
    path = path.split("?", 1)[0]
    return _SECRET_PATHS.sub("/join/…", path)


class AccessLog:
    """One line per HTTP request, with secrets redacted. Replaces uvicorn's
    access log, which prints full paths and query strings."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start = time.monotonic()
        status = {"code": 0}

        async def send_wrapper(message: dict) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_log.info(
                "%s %s %s %dms",
                scope.get("method", "?"),
                redact_path(scope.get("path", "")),
                status["code"] or "-",
                (time.monotonic() - start) * 1000,
            )


def check_dev_host(settings: Settings) -> None:
    if settings.dev and (urlsplit(settings.base_url).hostname or "") not in DEV_HOSTS:
        raise SystemExit("AUTH_MODE=dev only runs with MCP_BASE_URL on localhost or 127.0.0.1")


def build_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or Settings.from_env()
    check_dev_host(settings)
    app = App(settings=settings, store=Store(Database(settings.db_path)))

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[dict]:
        task = asyncio.create_task(_purge_loop(app.store))
        try:
            yield {}
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    mcp = FastMCP(
        name="Rooms",
        instructions=INSTRUCTIONS,
        auth=build_auth(settings),
        lifespan=lifespan,
        mask_error_details=True,
    )
    register_tools(mcp, app)
    register_web(mcp, app)
    mcp.rooms_app = app  # type: ignore[attr-defined]  # tests reach the store through this
    return mcp


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    build_server(settings).run(
        transport="http",
        host=settings.host,
        port=settings.port,
        path="/mcp",
        middleware=[Middleware(AccessLog)],
        uvicorn_config={"access_log": False},
    )


if __name__ == "__main__":
    main()
