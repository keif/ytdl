"""In-process pub/sub for SSE subscribers.

One bus per FastAPI app instance. Publishers push dicts; subscribers pull from
a bounded asyncio.Queue. When a subscriber is too slow and its queue fills,
the oldest message is dropped — the per-connection backpressure rule from the
spec ("drop messages older than buffer cap rather than block").
"""
from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any


class EventsBus:
    def __init__(self, max_per_subscriber: int = 256) -> None:
        self._max = max_per_subscriber
        self._subs: list[asyncio.Queue[dict[str, Any]]] = []

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subs):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._max)
        self._subs.append(q)
        try:
            yield q
        finally:
            self._subs.remove(q)
