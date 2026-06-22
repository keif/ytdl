from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ytdl.db import connect, migrate
from ytdl.events_bus import EventsBus
from ytdl.models import JobKind, JobStatus
from ytdl.queue import cancel as cancel_job
from ytdl.queue import enqueue, get_job
from ytdl.workers import Supervisor


class FakeDownloader:
    def __init__(self, behavior: str = "success") -> None:
        self.behavior = behavior
        self.calls = 0

    def run(self, job, ctx) -> object:
        from ytdl.downloader import DownloadResult

        self.calls += 1
        if self.behavior == "success":
            return DownloadResult(
                output_path=f"{job.output_dir}/{job.id}.mp4",
                title="t",
                video_id="vid",
                uploader="u",
                duration_s=10,
                filesize_bytes=1000,
            )
        if self.behavior == "transient_then_success":
            if self.calls < 2:
                raise RuntimeError("HTTP Error 503 transient")
            return DownloadResult(
                output_path=f"{job.output_dir}/{job.id}.mp4",
                title="t",
                video_id="vid",
                uploader=None,
                duration_s=None,
                filesize_bytes=None,
            )
        if self.behavior == "auth":
            raise RuntimeError("Sign in to confirm your age")
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_supervisor_processes_one_pending_job(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    job_id = enqueue(
        conn,
        url="u",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )

    bus = EventsBus()
    fake = FakeDownloader("success")
    sup = Supervisor(
        db_path=tmp_path / "t.db",
        workers=1,
        bus=bus,
        downloader=fake.run,
        probe=lambda url: {"_type": "video"},
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
        cookies_browser=None,
    )
    await sup.start()
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    job = get_job(conn, job_id)
    assert job is not None
    assert job.status == JobStatus.DONE
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_supervisor_retries_transient_then_succeeds(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    job_id = enqueue(
        conn,
        url="u",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )

    bus = EventsBus()
    fake = FakeDownloader("transient_then_success")
    sup = Supervisor(
        db_path=tmp_path / "t.db",
        workers=1,
        bus=bus,
        downloader=fake.run,
        probe=lambda url: {"_type": "video"},
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
        cookies_browser=None,
    )
    await sup.start()
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    job = get_job(conn, job_id)
    assert job is not None
    assert job.status == JobStatus.DONE
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_supervisor_marks_auth_error_as_failed_without_retry(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    job_id = enqueue(
        conn,
        url="u",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )

    bus = EventsBus()
    fake = FakeDownloader("auth")
    sup = Supervisor(
        db_path=tmp_path / "t.db",
        workers=1,
        bus=bus,
        downloader=fake.run,
        probe=lambda url: {"_type": "video"},
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
        cookies_browser=None,
    )
    await sup.start()
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    job = get_job(conn, job_id)
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error and "age" in job.error.lower()
    assert fake.calls == 1


@pytest.mark.asyncio
async def test_supervisor_observes_cancel_during_retry_backoff(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    job_id = enqueue(
        conn,
        url="u",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    conn.close()

    attempt_count = 0
    saw_first_call = asyncio.Event()
    loop = asyncio.get_event_loop()

    def fake_download(job, ctx):
        nonlocal attempt_count
        attempt_count += 1
        loop.call_soon_threadsafe(saw_first_call.set)
        raise RuntimeError("HTTP Error 503 transient")  # forces retry

    bus = EventsBus()
    sup = Supervisor(
        db_path=db,
        workers=1,
        bus=bus,
        downloader=fake_download,
        probe=lambda url: {"_type": "video"},
        cookies_browser=None,
        retry_delays_s=(5, 5),  # long enough to observe cancel
        rate_limit_delay_s=0,
    )
    await sup.start()
    try:
        await asyncio.wait_for(saw_first_call.wait(), timeout=2.0)
        # Job is now in retry sleep; cancel it.
        conn = connect(db)
        cancel_job(conn, job_id)
        conn.close()
        await sup.wait_idle(timeout=3.0)
    finally:
        await sup.stop()

    conn = connect(db)
    job = get_job(conn, job_id)
    conn.close()
    assert job is not None
    assert job.status == JobStatus.CANCELED
    assert attempt_count == 1, "worker should not have attempted a second download after cancel"


@pytest.mark.asyncio
async def test_supervisor_sets_bytes_done_to_filesize_on_success(tmp_path: Path) -> None:
    from ytdl.downloader import DownloadResult

    conn = connect(tmp_path / "t.db")
    migrate(conn)
    job_id = enqueue(
        conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir=str(tmp_path),
    )

    def fake_download(job, ctx) -> DownloadResult:
        # Simulate a fast download where the only progress tick captures a
        # tiny partial value before the file finishes.
        ctx.on_progress({"status": "downloading", "downloaded_bytes": 1024, "total_bytes": 500_000})
        return DownloadResult(
            output_path=f"{job.output_dir}/{job.id}.mp4",
            title="t", video_id="v", uploader=None, duration_s=None,
            filesize_bytes=500_000,
        )

    bus = EventsBus()
    sup = Supervisor(
        db_path=tmp_path / "t.db", workers=1, bus=bus,
        downloader=fake_download,
        probe=lambda url: {"_type": "video"},
        cookies_browser=None, retry_delays_s=(0, 0), rate_limit_delay_s=0,
    )
    await sup.start()
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    conn = connect(tmp_path / "t.db")
    job = get_job(conn, job_id)
    conn.close()
    assert job is not None
    assert job.status == JobStatus.DONE
    assert job.bytes_done == 500_000, f"expected bytes_done=500000 on success, got {job.bytes_done}"
    assert job.filesize_bytes == 500_000


@pytest.mark.asyncio
async def test_supervisor_publishes_finished_with_event_id_pointing_at_db_row(
    tmp_path: Path,
) -> None:
    """Persisted lifecycle events on the bus must carry _event_id so the SSE
    route can emit it as 'id:' for Last-Event-ID resume. The published id
    must point at a real row in the events table."""
    from ytdl.downloader import DownloadResult

    conn = connect(tmp_path / "t.db")
    migrate(conn)
    job_id = enqueue(
        conn,
        url="u",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    conn.close()

    def fake_download(job, ctx) -> DownloadResult:
        return DownloadResult(
            output_path=f"{job.output_dir}/{job.id}.mp4",
            title="t",
            video_id="v",
            uploader=None,
            duration_s=None,
            filesize_bytes=None,
        )

    bus = EventsBus()
    seen: list[dict] = []

    async def collect() -> None:
        async with bus.subscribe() as q:
            while True:
                msg = await asyncio.wait_for(q.get(), timeout=3.0)
                seen.append(msg)
                if msg.get("event") in ("finished", "failed", "canceled"):
                    return

    sup = Supervisor(
        db_path=tmp_path / "t.db",
        workers=1,
        bus=bus,
        downloader=fake_download,
        probe=lambda url: {"_type": "video"},
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    collect_task = asyncio.create_task(collect())
    # Let the subscriber attach before workers start publishing.
    await asyncio.sleep(0.05)
    await sup.start()
    await asyncio.wait_for(collect_task, timeout=3.0)
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    finished = next(m for m in seen if m.get("event") == "finished")
    assert finished["job_id"] == job_id
    assert "_event_id" in finished, (
        f"finished message must include _event_id, got {finished}"
    )
    event_id = finished["_event_id"]
    assert isinstance(event_id, int) and event_id > 0

    # The published _event_id must point at a real row in the events table.
    conn = connect(tmp_path / "t.db")
    row = conn.execute(
        "SELECT kind, job_id FROM events WHERE id=?", (event_id,)
    ).fetchone()
    conn.close()
    assert row is not None
    assert row["kind"] == "finished"
    assert row["job_id"] == job_id

    # And 'started' should also carry an _event_id, distinct from finished's.
    started = next(m for m in seen if m.get("event") == "started")
    assert "_event_id" in started
    assert started["_event_id"] != event_id


def test_default_download_adapter_forwards_subtitle_langs() -> None:
    """The adapter wraps the supervisor's DownloadContext with the lazy yt-dlp
    class. It must preserve subtitle_langs through that hop — otherwise
    config-driven locales silently fall back to the default tuple and
    yt-dlp never requests the configured languages.
    """
    from ytdl.downloader import DownloadContext, DownloadResult
    from ytdl.workers import _default_download_adapter

    seen: dict[str, object] = {}

    def fake_default_download(job, ctx: DownloadContext) -> DownloadResult:
        seen["subtitle_langs"] = ctx.subtitle_langs
        seen["ydl_cls"] = ctx.ydl_cls
        return DownloadResult(
            output_path="/o/x.mp4", title=None, video_id=None,
            uploader=None, duration_s=None, filesize_bytes=None,
        )

    import ytdl.workers as workers_mod
    original = workers_mod.default_download
    workers_mod.default_download = fake_default_download
    try:
        outer_ctx = DownloadContext(
            ydl_cls=None,
            cookies_browser=None,
            on_progress=None,
            cancel_flag=lambda: False,
            subtitle_langs=("es", "en"),
        )
        _default_download_adapter(object(), outer_ctx)
    finally:
        workers_mod.default_download = original

    assert seen["subtitle_langs"] == ("es", "en")
    # ydl_cls must have been swapped to the real YoutubeDL.
    assert seen["ydl_cls"] is not None


@pytest.mark.asyncio
async def test_supervisor_probe_403_includes_cookie_hint(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    job_id = enqueue(
        conn, url="https://yt/x", kind=JobKind.VIDEO,
        format_pref="best", output_dir=str(tmp_path),
    )
    conn.close()

    def fake_probe(url: str) -> dict:
        raise RuntimeError("HTTP Error 403: Forbidden")

    bus = EventsBus()
    sup = Supervisor(
        db_path=tmp_path / "t.db", workers=1, bus=bus,
        downloader=lambda job, ctx: (_ for _ in ()).throw(AssertionError("should not download")),
        probe=fake_probe,
        cookies_browser=None, retry_delays_s=(0, 0), rate_limit_delay_s=0,
    )
    await sup.start()
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    conn = connect(tmp_path / "t.db")
    job = get_job(conn, job_id)
    conn.close()
    assert job is not None
    assert job.status == JobStatus.FAILED
    assert job.error and "[probe:forbidden]" in job.error
    # Hint must mention both common fixes (JS runtime AND cookies).
    assert job.error and "deno" in job.error.lower()
    assert job.error and "cookies use" in job.error.lower()
