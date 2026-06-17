"""Worker supervisor.

Starts N asyncio worker tasks. Each worker:
  1. claims a pending job from the queue
  2. runs the downloader inside asyncio.to_thread
  3. handles retry / classification on failure
  4. updates the row and publishes lifecycle events

The supervisor owns its own sqlite connection per worker (sqlite handles are
thread-affine in some Python builds, even with check_same_thread=False).
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ytdl.db import connect, migrate
from ytdl.downloader import (
    Classification,
    DownloadCancelled,
    DownloadContext,
    DownloadResult,
    classify_error,
)
from ytdl.downloader import download as default_download
from ytdl.events_bus import EventsBus
from ytdl.models import JobKind, JobStatus
from ytdl.queue import (
    all_children_terminal,
    claim_one,
    enqueue,
    finish,
    get_job,
    list_jobs,
    promote_to_playlist,
    record_event,
    revive_orphans,
    update_metadata,
    update_progress,
)

log = logging.getLogger(__name__)

Downloader = Callable[[Any, DownloadContext], DownloadResult]


def _sanitize_path_component(name: str) -> str:
    """Make a yt-dlp-supplied title safe to use as a single directory component.

    Strips path separators, NULs, and rejects '.' / '..' which would traverse
    out of output_dir or shadow the parent. Truncates to 200 chars so unusually
    long titles don't blow past filesystem name limits.
    """
    cleaned = name.replace("\\", "_").replace("/", "_").replace("\x00", "")
    cleaned = cleaned.strip().strip(".")
    if not cleaned or cleaned in (".", ".."):
        cleaned = "Playlist"
    return cleaned[:200]


class Supervisor:
    def __init__(
        self,
        *,
        db_path: Path,
        workers: int,
        bus: EventsBus,
        cookies_browser: str | None,
        downloader: Downloader | None = None,
        probe: Callable[[str], dict] | None = None,
        retry_delays_s: tuple[int, ...] = (2, 8),
        rate_limit_delay_s: int = 60,
    ) -> None:
        self._db_path = db_path
        self._n = workers
        self._bus = bus
        self._cookies = cookies_browser
        self._download: Downloader = downloader or _default_download_adapter
        self._probe: Callable[[str], dict] = probe or _default_probe_adapter
        self._retry_delays = retry_delays_s
        self._rate_limit_delay = rate_limit_delay_s
        self._tasks: list[asyncio.Task] = []
        self._stop = asyncio.Event()
        self._idle_event = asyncio.Event()
        self._busy_workers = 0
        self._cancel_flags: dict[str, bool] = {}

    async def start(self) -> None:
        # Revive crashed-in-progress jobs from previous runs.
        conn = connect(self._db_path)
        migrate(conn)
        revive_orphans(conn)
        conn.close()

        self._stop.clear()
        self._idle_event.set()
        for _ in range(self._n):
            self._tasks.append(asyncio.create_task(self._worker_loop()))

    async def stop(self) -> None:
        self._stop.set()
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()

    def request_cancel(self, job_id: str) -> None:
        self._cancel_flags[job_id] = True

    async def wait_idle(self, timeout: float = 5.0) -> None:
        """Wait until no work is in flight and no pending jobs remain.

        Used by tests to deterministically synchronize on queue drain.
        """

        async def _drained() -> bool:
            conn = connect(self._db_path)
            try:
                pending = list_jobs(conn, status=JobStatus.PENDING, limit=1)
                running = list_jobs(conn, status=JobStatus.RUNNING, limit=1)
                canceling = list_jobs(conn, status=JobStatus.CANCELING, limit=1)
            finally:
                conn.close()
            return not pending and not running and not canceling and self._busy_workers == 0

        async def _poll() -> None:
            while True:
                if await _drained():
                    return
                await asyncio.sleep(0.02)

        await asyncio.wait_for(_poll(), timeout=timeout)

    async def _worker_loop(self) -> None:
        conn = connect(self._db_path)
        try:
            while not self._stop.is_set():
                job = claim_one(conn)
                if job is None:
                    await asyncio.sleep(0.05)
                    continue
                self._busy_workers += 1
                try:
                    await self._handle_job(conn, job)
                finally:
                    self._busy_workers -= 1
                    self._cancel_flags.pop(job.id, None)
        finally:
            conn.close()

    async def _handle_job(self, conn, job) -> None:
        self._bus.publish({"event": "started", "job_id": job.id})

        # Playlist detection: only probe top-level VIDEO jobs. Children that
        # were enqueued via a previous expansion already carry a parent_job_id
        # and skip straight to the video download path.
        if job.kind == JobKind.VIDEO and job.parent_job_id is None:
            try:
                info = await asyncio.to_thread(self._probe, job.url)
            except BaseException as exc:
                cls = classify_error(exc)
                finish(
                    conn,
                    job.id,
                    status=JobStatus.FAILED,
                    error=f"[probe:{cls.value}] {exc}",
                )
                self._bus.publish(
                    {"event": "failed", "job_id": job.id, "error": str(exc)}
                )
                return

            if info.get("_type") == "playlist":
                playlist_title = info.get("title") or "Playlist"
                safe_title = _sanitize_path_component(playlist_title)
                promote_to_playlist(conn, job.id, title=playlist_title)
                playlist_subdir = Path(job.output_dir) / safe_title
                entries = info.get("entries") or []
                for entry in entries:
                    child_url = entry.get("webpage_url") or entry.get("url") or ""
                    if not child_url:
                        continue
                    enqueue(
                        conn,
                        url=child_url,
                        kind=JobKind.VIDEO,
                        format_pref=job.format_pref,
                        output_dir=str(playlist_subdir),
                        parent_job_id=job.id,
                    )
                # Parent stays RUNNING until all children reach a terminal
                # state; the last child to finish flips the parent (see below).
                self._bus.publish(
                    {
                        "event": "expanded",
                        "job_id": job.id,
                        "child_count": len(entries),
                    }
                )
                return

        # Regular video download (top-level video OR playlist child).
        await self._download_video(conn, job)

        # If this was a playlist child, re-check parent completion. Only the
        # last child to reach a terminal state will actually flip the parent.
        if job.parent_job_id is not None:
            terminal, done, failed = all_children_terminal(conn, job.parent_job_id)
            if terminal:
                finish(
                    conn,
                    job.parent_job_id,
                    status=JobStatus.DONE,
                    output_path=None,
                    error=(f"{failed} child(ren) failed" if failed else None),
                )
                self._bus.publish(
                    {
                        "event": "finished",
                        "job_id": job.parent_job_id,
                        "done": done,
                        "failed": failed,
                    }
                )

    async def _download_video(self, conn, job) -> None:
        def cancel_flag() -> bool:
            if self._cancel_flags.get(job.id):
                return True
            current = get_job(conn, job.id)
            return current is not None and current.status == JobStatus.CANCELING

        def on_progress(d: dict) -> None:
            if d.get("status") == "downloading":
                update_progress(
                    conn,
                    job.id,
                    bytes_done=d.get("downloaded_bytes"),
                    speed_bps=int(d["speed"]) if d.get("speed") else None,
                    eta_s=int(d["eta"]) if d.get("eta") else None,
                    filesize_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
                )
            self._bus.publish(
                {
                    "event": "progress",
                    "job_id": job.id,
                    "status": d.get("status"),
                    "downloaded_bytes": d.get("downloaded_bytes"),
                    "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate"),
                    "speed": d.get("speed"),
                    "eta": d.get("eta"),
                }
            )

        ctx = DownloadContext(
            ydl_cls=None,  # set by the default adapter; tests stub the whole callable
            cookies_browser=self._cookies,
            on_progress=on_progress,
            cancel_flag=cancel_flag,
        )

        Path(job.output_dir).mkdir(parents=True, exist_ok=True)

        attempt = 0
        while True:
            try:
                result = await asyncio.to_thread(self._download, job, ctx)
                update_metadata(
                    conn,
                    job.id,
                    title=result.title,
                    video_id=result.video_id,
                    uploader=result.uploader,
                    duration_s=result.duration_s,
                )
                finish(conn, job.id, status=JobStatus.DONE, output_path=result.output_path)
                self._bus.publish({"event": "finished", "job_id": job.id})
                return
            except DownloadCancelled:
                finish(conn, job.id, status=JobStatus.CANCELED, error="canceled")
                self._bus.publish({"event": "canceled", "job_id": job.id})
                return
            except BaseException as exc:
                cls = classify_error(exc)
                if cls == Classification.TRANSIENT and attempt < len(self._retry_delays):
                    delay = self._retry_delays[attempt]
                    attempt += 1
                    record_event(conn, job.id, "log", {"retry_after_s": delay, "reason": str(exc)})
                    await asyncio.sleep(delay)
                    continue
                if cls == Classification.RATE_LIMITED and attempt == 0:
                    attempt += 1
                    record_event(
                        conn,
                        job.id,
                        "log",
                        {"rate_limited_for_s": self._rate_limit_delay},
                    )
                    await asyncio.sleep(self._rate_limit_delay)
                    continue
                finish(conn, job.id, status=JobStatus.FAILED, error=f"[{cls.value}] {exc}")
                self._bus.publish(
                    {
                        "event": "failed",
                        "job_id": job.id,
                        "classification": cls.value,
                        "error": str(exc),
                    }
                )
                return


def _default_download_adapter(job, ctx: DownloadContext) -> DownloadResult:
    """Inject the real yt-dlp class lazily.

    Importing this module shouldn't require yt_dlp at parse time.
    """
    from yt_dlp import YoutubeDL

    real_ctx = DownloadContext(
        ydl_cls=YoutubeDL,
        cookies_browser=ctx.cookies_browser,
        on_progress=ctx.on_progress,
        cancel_flag=ctx.cancel_flag,
        throttle_interval_s=ctx.throttle_interval_s,
    )
    return default_download(job, real_ctx)


def _default_probe_adapter(url: str) -> dict:
    """Lazy default that delegates to the downloader's probe helper."""
    from ytdl.downloader import probe as _probe

    return _probe(url)
