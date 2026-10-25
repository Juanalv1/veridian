"""Small in-process TTL cache with single-flight, for the panel's engine GETs.

Only successful (HTTP 200) responses are stored. Concurrent callers that miss on the same
key share one in-flight call. `invalidate()` drops every entry and keeps results of calls
started before it from being stored, so a read that raced a write cannot leave stale data.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Hashable

import httpx


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._entries: dict[Hashable, tuple[float, httpx.Response]] = {}
        self._inflight: dict[Hashable, asyncio.Future] = {}
        self._generation = 0

    async def get(self, key: Hashable, ttl: float,
                  fetch: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        hit = self._entries.get(key)
        if hit is not None and hit[0] > self._clock():
            return hit[1]
        flight = self._inflight.get(key)
        if flight is None:
            flight = asyncio.ensure_future(self._load(key, ttl, fetch))
            self._inflight[key] = flight
            # Consume the exception if every waiter was cancelled before it finished.
            flight.add_done_callback(lambda f: f.cancelled() or f.exception())
        # shield: one caller going away must not cancel the call the others share.
        return await asyncio.shield(flight)

    async def _load(self, key: Hashable, ttl: float,
                    fetch: Callable[[], Awaitable[httpx.Response]]) -> httpx.Response:
        generation = self._generation
        try:
            response = await fetch()
        finally:
            if self._inflight.get(key) is asyncio.current_task():
                del self._inflight[key]
        if response.status_code == 200 and generation == self._generation and ttl > 0:
            self._entries[key] = (self._clock() + ttl, response)
        return response

    def invalidate(self) -> None:
        self._entries.clear()
        self._inflight.clear()
        self._generation += 1
