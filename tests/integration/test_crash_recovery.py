from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ytdl.db import connect, migrate
from ytdl.events_bus import EventsBus
from ytdl.models import JobKind, JobStatus
from ytdl.queue import enqueue, get_job
from ytdl.workers import Supervisor


@pytest.mark.asyncio
async def test_orphaned_running_jobs_revive_on_restart(tmp_path: Path) -> None:
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

    # Simulate a crash: mark RUNNING with no worker.
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    conn.close()

    bus = EventsBus()

    completed = asyncio.Event()

    def fake_download(job, ctx):
        from ytdl.downloader import DownloadResult

        completed.set()
        return DownloadResult(
            output_path=f"{job.output_dir}/{job.id}.mp4",
            title="t",
            video_id="vid",
            uploader=None,
            duration_s=None,
            filesize_bytes=None,
        )

    sup = Supervisor(
        db_path=db,
        workers=1,
        bus=bus,
        downloader=fake_download,
        probe=lambda url: {"_type": "video"},
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    await sup.start()
    await asyncio.wait_for(completed.wait(), timeout=2.0)
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    conn = connect(db)
    job = get_job(conn, job_id)
    conn.close()
    assert job is not None
    assert job.status == JobStatus.DONE
