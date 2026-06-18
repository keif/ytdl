from __future__ import annotations

import asyncio

import pytest

from ytdl.events_bus import EventsBus


@pytest.mark.asyncio
async def test_subscriber_receives_published_event() -> None:
    bus = EventsBus(max_per_subscriber=10)
    async with bus.subscribe() as queue:
        bus.publish({"kind": "started", "job_id": "abc"})
        msg = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert msg["kind"] == "started"


@pytest.mark.asyncio
async def test_two_subscribers_both_receive() -> None:
    bus = EventsBus(max_per_subscriber=10)
    async with bus.subscribe() as q1, bus.subscribe() as q2:
        bus.publish({"kind": "progress", "bytes_done": 100})
        m1 = await asyncio.wait_for(q1.get(), timeout=0.5)
        m2 = await asyncio.wait_for(q2.get(), timeout=0.5)
        assert m1 == m2 == {"kind": "progress", "bytes_done": 100}


@pytest.mark.asyncio
async def test_slow_subscriber_drops_oldest() -> None:
    bus = EventsBus(max_per_subscriber=2)
    async with bus.subscribe() as q:
        bus.publish({"n": 1})
        bus.publish({"n": 2})
        bus.publish({"n": 3})  # should drop {"n": 1}
        first = await q.get()
        second = await q.get()
        assert first == {"n": 2}
        assert second == {"n": 3}


@pytest.mark.asyncio
async def test_unsubscribe_on_exit() -> None:
    bus = EventsBus(max_per_subscriber=10)
    async with bus.subscribe():
        assert bus.subscriber_count == 1
    assert bus.subscriber_count == 0


@pytest.mark.asyncio
async def test_publish_threadsafe_from_worker_thread() -> None:
    """Event published from a worker thread via publish_threadsafe must arrive."""
    import threading

    bus = EventsBus(max_per_subscriber=10)
    loop = asyncio.get_running_loop()

    async with bus.subscribe() as q:
        def worker() -> None:
            bus.publish_threadsafe({"from": "worker_thread"}, loop)

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=1.0)

        msg = await asyncio.wait_for(q.get(), timeout=1.0)
        assert msg == {"from": "worker_thread"}
