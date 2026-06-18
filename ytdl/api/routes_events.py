"""SSE multiplexed stream.

Spec rules:
  - send a `snapshot` message on connect summarizing non-terminal jobs
  - then stream live events from the in-process EventsBus
  - on reconnect, replay missed transitions from the events table via Last-Event-ID
  - per-connection backpressure: bus already drops oldest on full queue
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ytdl.db import connect
from ytdl.events_bus import EventsBus
from ytdl.models import TERMINAL_STATUSES
from ytdl.queue import list_events_since, list_jobs

router = APIRouter(tags=["events"])


def _format_sse(data: dict, event_id: int | None = None) -> bytes:
    # Live events from the bus may carry their events-table row id inline as
    # "_event_id". Promote it to the SSE id: line so EventSource advances
    # Last-Event-ID during normal streaming, and strip it from the JSON
    # payload (it's an internal field, not for clients). We never mutate the
    # input dict — other subscribers may be processing the same message.
    if event_id is None and "_event_id" in data:
        event_id = data["_event_id"]
        data = {k: v for k, v in data.items() if k != "_event_id"}
    out = []
    if event_id is not None:
        out.append(f"id: {event_id}")
    out.append(f"data: {json.dumps(data)}")
    out.append("")
    out.append("")
    return ("\n".join(out)).encode()


async def event_stream(
    bus: EventsBus,
    db_path: Path,
    *,
    last_event_id: str | None = None,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
    keepalive_seconds: float = 15.0,
) -> AsyncIterator[bytes]:
    """Async generator producing the SSE byte stream for `/events`.

    Extracted from the route handler so the streaming behavior can be unit
    tested without an HTTP transport (httpx.ASGITransport buffers the entire
    response, which deadlocks on never-ending SSE streams).
    """
    # Subscribe to the live bus BEFORE emitting the snapshot. This closes
    # the race where an event published between snapshot and subscribe
    # would be dropped: any event arriving now will be buffered in the
    # queue and delivered after the snapshot/replay.
    async with bus.subscribe() as queue:
        # 1. Snapshot of non-terminal jobs
        conn = connect(db_path)
        try:
            jobs = list_jobs(conn, limit=500)
            non_terminal = [
                {"id": j.id, "url": j.url, "status": j.status.value, "title": j.title}
                for j in jobs
                if j.status not in TERMINAL_STATUSES
            ]
        finally:
            conn.close()
        yield _format_sse({"event": "snapshot", "jobs": non_terminal})

        # 2. Replay events since Last-Event-ID, if provided
        if last_event_id and last_event_id.isdigit():
            conn = connect(db_path)
            try:
                replays = list_events_since(conn, int(last_event_id))
            finally:
                conn.close()
            for ev in replays:
                yield _format_sse(
                    {"event": ev.kind, "job_id": ev.job_id, **ev.payload},
                    event_id=ev.id,
                )

        # 3. Live stream
        while True:
            if is_disconnected is not None and await is_disconnected():
                return
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except TimeoutError:
                yield b": keep-alive\n\n"
                continue
            yield _format_sse(msg)


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    bus = request.app.state.bus
    cfg = request.app.state.config
    last_id_hdr = request.headers.get("last-event-id")
    gen = event_stream(
        bus,
        cfg.db_path,
        last_event_id=last_id_hdr,
        is_disconnected=request.is_disconnected,
    )
    return StreamingResponse(gen, media_type="text/event-stream")
