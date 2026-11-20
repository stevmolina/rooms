"""In-process wake-ups for long polls.

A waiter registers one event under each room it watches. A new message wakes
only the waiters of that room; each then re-runs its own query (a plain read,
which also re-checks membership), so a wake-up never leaks anything: it only
means "look again". One server process, so this is enough.

Each user may hold at most MAX_WAITS_PER_USER open waits at once, across MCP
`wait`, the web page's poll and the wake API.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from typing import Any

from .store import RoomsError, Store

MAX_WAIT = 55.0
MAX_WAITS_PER_USER = 5
MAX_LISTENS_PER_USER = 10  # sleeping listen holds, counted apart from the 5 waits


class TooManyWaits(RoomsError):
    pass


class Notifier:
    def __init__(self, max_waits_per_user: int = MAX_WAITS_PER_USER) -> None:
        self.max_waits_per_user = max_waits_per_user
        self._waiters: dict[str, set[asyncio.Event]] = defaultdict(set)
        self._open: dict[str, int] = defaultdict(int)
        self.wakeups = 0  # waiter events set, for tests and metrics

    def bump(self, room_id: str) -> None:
        """A message was posted in `room_id`. Wake only that room's waiters."""
        for event in self._waiters.pop(room_id, ()):
            if not event.is_set():
                event.set()
                self.wakeups += 1

    @contextmanager
    def slot(self, user_id: str, pool: str = "wait") -> Iterator[None]:
        """Hold one of the user's open-wait slots, or raise TooManyWaits. Sleeping listen
        holds use their own pool, so sleeping agents never block a read or the web page."""
        key = user_id if pool == "wait" else f"{user_id}#{pool}"
        limit = self.max_waits_per_user if pool == "wait" else MAX_LISTENS_PER_USER
        if self._open[key] >= limit:
            what = "open waits" if pool == "wait" else "sleeping listeners"
            raise TooManyWaits(f"too many {what}: at most {limit} at once per person")
        self._open[key] += 1
        try:
            yield
        finally:
            self._open[key] -= 1
            if self._open[key] <= 0:
                del self._open[key]

    def _register(self, rooms: Iterable[str], event: asyncio.Event) -> list[str]:
        rooms = list(rooms)
        for room_id in rooms:
            self._waiters[room_id].add(event)
        return rooms

    def _unregister(self, rooms: list[str], event: asyncio.Event) -> None:
        for room_id in rooms:
            waiters = self._waiters.get(room_id)
            if waiters is not None:
                waiters.discard(event)
                if not waiters:
                    del self._waiters[room_id]

    async def wait_for(
        self,
        user_id: str,
        rooms: Callable[[], Iterable[str]],
        check: Callable[[], list[Any]],
        timeout: float,
        pool: str = "wait",
    ) -> list[Any]:
        """Run `check` now and whenever one of `rooms()` gets a message, until it
        returns something or time runs out. Holds one of the user's wait slots."""
        deadline = time.monotonic() + max(0.0, min(float(timeout), MAX_WAIT))
        with self.slot(user_id, pool):
            while True:
                event = asyncio.Event()
                watched = self._register(rooms(), event)  # before checking: no message is missed
                try:
                    found = check()
                    if found:
                        return found
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return []
                    try:
                        await asyncio.wait_for(event.wait(), timeout=remaining)
                    except TimeoutError:
                        return []
                finally:
                    self._unregister(watched, event)

    async def wait(
        self,
        store: Store,
        user_id: str,
        agent_id: int,
        rooms: list[str] | None = None,
        timeout: float = 50,
    ) -> list[dict[str, Any]]:
        """New messages from others for this agent, or [] at timeout."""
        return await self.wait_for(
            user_id,
            lambda: store.member_rooms(user_id, rooms),
            lambda: store.take_for_agent(user_id, agent_id, rooms),
            timeout,
        )
