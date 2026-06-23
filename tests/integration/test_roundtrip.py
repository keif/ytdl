"""Integration test: POST /jobs -> worker pipeline -> finished event on the bus.

The plan's verbatim sketch streams `/events` via httpx.ASGITransport. That
transport buffers the entire response before returning, which deadlocks
against a never-ending SSE stream — the same constraint documented in
the SSE route tests. We exercise the same integration path here
(API endpoint -> queue -> supervisor -> bus) by subscribing to the bus
directly. SSE protocol/framing is covered by the unit tests on
`event_stream`.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ytdl.api import build_app
from ytdl.config import Config
from ytdl.downloader import DownloadResult


def _config(tmp_path: Path) -> Config:
    return Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=1,
        cookies_browser=None,
        default_format="best",
    )


@pytest.mark.asyncio
async def test_post_then_bus_then_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "out").mkdir()

    def fake_download(job, ctx) -> DownloadResult:
        # simulate two progress emissions then a final filepath
        ctx.on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 100,
                "total_bytes": 200,
                "speed": 50,
                "eta": 2,
            }
        )
        ctx.on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 200,
                "total_bytes": 200,
                "speed": 50,
                "eta": 0,
            }
        )
        path = Path(job.output_dir) / f"Title [{job.id}].mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fake")
        return DownloadResult(
            output_path=str(path),
            title="Title",
            video_id="abc",
            uploader="Uploader",
            duration_s=10,
            filesize_bytes=200,
        )

    # Patch the supervisor's default adapter import target. Also short-circuit
    # the playlist probe so we never reach yt_dlp.
    import ytdl.workers as wm

    monkeypatch.setattr(
        wm, "_default_download_adapter", lambda job, ctx: fake_download(job, ctx)
    )
    monkeypatch.setattr(
        wm,
        "_default_probe_adapter",
        lambda url, *, cookies_browser=None, socket_timeout=30: {"_type": "video"},
    )

    app = build_app(_config(tmp_path))

    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            events_seen: list[dict] = []
            done = asyncio.Event()

            async def collect() -> None:
                async with app.state.bus.subscribe() as q:
                    while True:
                        msg = await asyncio.wait_for(q.get(), timeout=5.0)
                        events_seen.append(msg)
                        if msg.get("event") in ("finished", "failed", "canceled"):
                            done.set()
                            return

            collect_task = asyncio.create_task(collect())
            # Yield so the subscriber actually attaches to the bus before we POST.
            await asyncio.sleep(0.05)

            r = await client.post("/jobs", json={"url": "https://youtu.be/abc"})
            assert r.status_code == 201
            job_id = r.json()["id"]

            await asyncio.wait_for(collect_task, timeout=5.0)
            assert done.is_set()

            kinds = [e.get("event") for e in events_seen]
            assert "started" in kinds
            assert "finished" in kinds
            # Progress events should have been published before the terminal one.
            assert "progress" in kinds

            # The job row should reflect the terminal state.
            final = (await client.get(f"/jobs/{job_id}")).json()
            assert final["status"] == "done"
            assert final["output_path"]
            assert final["title"] == "Title"
