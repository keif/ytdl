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
