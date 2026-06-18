from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from ytdl.api import build_app
from ytdl.api.routes_events import event_stream
from ytdl.config import Config


def _config(tmp_path: Path) -> Config:
    return Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
    )


async def _next_event(gen) -> dict:
    """Pull the next non-keepalive SSE event off the byte stream as a dict."""
    chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    text = chunk.decode()
    # Each SSE message ends with a blank line; split into lines and find the data line.
    for line in text.splitlines():
        if line.startswith("data:"):
            return json.loads(line.removeprefix("data:").strip())
    raise AssertionError(f"chunk had no data line: {text!r}")


@pytest.mark.asyncio
async def test_sse_sends_snapshot_then_live_events(tmp_path: Path) -> None:
    # We test the SSE generator directly because httpx.ASGITransport buffers
    # the entire response before returning, which deadlocks against a
    # never-ending stream. The route handler is a thin wrapper around
    # `event_stream`, so exercising the generator covers the protocol logic.
    app = build_app(_config(tmp_path))
    bus = app.state.bus
    cfg = app.state.config

    gen = event_stream(bus, cfg.db_path, keepalive_seconds=0.05)

    # snapshot arrives first
    snapshot_data = await _next_event(gen)
    assert snapshot_data["event"] == "snapshot"
    assert snapshot_data["jobs"] == []

    # publish a live event — subscriber is already attached, so it lands in the queue
    bus.publish({"event": "started", "job_id": "x"})
    live_data = await _next_event(gen)
    assert live_data["event"] == "started"
    assert live_data["job_id"] == "x"

    await gen.aclose()


@pytest.mark.asyncio
async def test_sse_keepalive_emitted_when_idle(tmp_path: Path) -> None:
    app = build_app(_config(tmp_path))
    gen = event_stream(
        app.state.bus, app.state.config.db_path, keepalive_seconds=0.05
    )
    # Drain the snapshot
    await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    # Next chunk with no publishes should be a keep-alive comment
    chunk = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
    assert chunk.startswith(b":")
    await gen.aclose()


@pytest.mark.asyncio
async def test_sse_replays_events_since_last_event_id(tmp_path: Path) -> None:
    from ytdl.db import connect
    from ytdl.queue import record_event

    app = build_app(_config(tmp_path))
    conn = connect(app.state.config.db_path)
    try:
        id1 = record_event(conn, "job-a", "started", {})
        id2 = record_event(conn, "job-a", "finished", {"output_path": "/x"})
    finally:
        conn.close()

    gen = event_stream(
        app.state.bus,
        app.state.config.db_path,
        last_event_id=str(id1 - 1),
        keepalive_seconds=0.05,
    )

    # snapshot first
    snapshot = await _next_event(gen)
    assert snapshot["event"] == "snapshot"
    # then the two replayed events
    first = await _next_event(gen)
    assert first["event"] == "started"
    assert first["job_id"] == "job-a"
    second = await _next_event(gen)
    assert second["event"] == "finished"
    assert second["output_path"] == "/x"
    await gen.aclose()
    # silence unused-id warnings
    assert id2 > id1


def test_format_sse_emits_id_line_when_event_id_in_payload() -> None:
    """Live bus messages may carry _event_id inline; the formatter must
    promote it to the SSE 'id:' line so EventSource advances Last-Event-ID."""
    from ytdl.api.routes_events import _format_sse

    out = _format_sse({"event": "finished", "job_id": "abc", "_event_id": 42})
    text = out.decode()
    assert "id: 42" in text
    # _event_id must NOT leak into the JSON data payload sent to clients.
    assert "_event_id" not in text
    assert '"event": "finished"' in text


def test_format_sse_no_id_line_when_event_id_absent() -> None:
    """Non-persisted events (progress, expanded) carry no event id; the
    formatter must not invent one."""
    from ytdl.api.routes_events import _format_sse

    out = _format_sse(
        {"event": "progress", "job_id": "abc", "downloaded_bytes": 100}
    )
    text = out.decode()
    assert "id:" not in text


def test_format_sse_does_not_mutate_input_dict() -> None:
    """Other bus subscribers may be processing the same dict — the formatter
    must work on a copy when stripping _event_id."""
    from ytdl.api.routes_events import _format_sse

    payload = {"event": "finished", "job_id": "abc", "_event_id": 7}
    original = dict(payload)
    _format_sse(payload)
    assert payload == original, "input dict must not be mutated by _format_sse"
