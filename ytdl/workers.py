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
import time
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
    finish_if_status,
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
        if probe is not None:
            self._probe: Callable[[str], dict] = probe
        else:
            _cookies = cookies_browser

            def _adapter(url: str) -> dict:
                return _default_probe_adapter(url, cookies_browser=_cookies)

            self._probe = _adapter
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

    async def _cancellable_sleep(self, conn, job_id: str, delay: float) -> bool:
        """Sleep up to ``delay`` seconds, returning True if the job was canceled.

        Polls the cancel flag + DB status every 250ms so we observe cancels
        issued via the API mid-backoff instead of waiting out the full delay.
        """
        end = asyncio.get_event_loop().time() + delay
        while True:
            now = asyncio.get_event_loop().time()
            if now >= end:
                return False
            if self._cancel_flags.get(job_id):
                return True
            current = get_job(conn, job_id)
            if current is not None and current.status == JobStatus.CANCELING:
                return True
            await asyncio.sleep(min(0.25, end - now))

    async def _handle_job(self, conn, job) -> None:
        # Record + publish "started" here (not inside claim_one) so we capture
        # the event-row id and ship it on the bus as _event_id. The SSE route
        # uses that to emit the SSE id: line so EventSource can advance
        # Last-Event-ID for resume.
        started_id = record_event(conn, job.id, "started", {})
        self._bus.publish(
            {"event": "started", "job_id": job.id, "_event_id": started_id}
        )

        # Playlist detection: only probe top-level VIDEO jobs. Children that
        # were enqueued via a previous expansion already carry a parent_job_id
        # and skip straight to the video download path.
        if job.kind == JobKind.VIDEO and job.parent_job_id is None:
            try:
                info = await asyncio.to_thread(self._probe, job.url)
            except BaseException as exc:
                cls = classify_error(exc)
                fail_id = finish(
                    conn,
                    job.id,
                    status=JobStatus.FAILED,
                    error=f"[probe:{cls.value}] {exc}",
                )
                self._bus.publish(
                    {
                        "event": "failed",
                        "job_id": job.id,
                        "error": str(exc),
                        "_event_id": fail_id,
                    }
                )
                return

            if info.get("_type") == "playlist":
                # Wrap the entire expansion in BEGIN IMMEDIATE / COMMIT so
                # children become claimable as an atomic set. Without this,
                # a fast first child could be claimed and finalized (firing
                # the reaper) before the loop has inserted later siblings —
                # those late siblings would then be stranded because
                # claim_one filters PENDING children of non-RUNNING parents.
                conn.execute("BEGIN IMMEDIATE")
                committed = False
                try:
                    # Re-read the parent's current status — the user may have
                    # canceled the parent while we were probing. If so, mark
                    # all the children as canceled-on-arrival instead of
                    # enqueueing them as PENDING.
                    current = get_job(conn, job.id)
                    parent_canceled_pre = (
                        current is not None and current.status != JobStatus.RUNNING
                    )

                    playlist_title = info.get("title") or "Playlist"
                    safe_title = _sanitize_path_component(playlist_title)
                    promote_to_playlist(conn, job.id, title=playlist_title)
                    playlist_subdir = Path(job.output_dir) / safe_title

                    enqueued_child_ids: list[str] = []
                    for entry in info.get("entries") or []:
                        child_url = entry.get("webpage_url") or entry.get("url") or ""
                        if not child_url:
                            continue
                        child_id = enqueue(
                            conn,
                            url=child_url,
                            kind=JobKind.VIDEO,
                            format_pref=job.format_pref,
                            output_dir=str(playlist_subdir),
                            parent_job_id=job.id,
                        )
                        enqueued_child_ids.append(child_id)
                        if parent_canceled_pre:
                            conn.execute(
                                "UPDATE jobs SET status=?, finished_at=? WHERE id=?",
                                (
                                    JobStatus.CANCELED.value,
                                    int(time.time() * 1000),
                                    child_id,
                                ),
                            )
                            record_event(
                                conn,
                                child_id,
                                "canceled",
                                {"reason": "parent canceled before expansion"},
                            )

                    enqueued_children = len(enqueued_child_ids)

                    # Empty playlist: all_children_terminal would never fire
                    # the reaper. Finish the parent now so the queue drains.
                    if enqueued_children == 0:
                        if parent_canceled_pre:
                            # Cancel-during-probe with an empty playlist:
                            # honor the user's cancel, don't overwrite as
                            # an "empty playlist DONE".
                            canceled_id = finish_if_status(
                                conn,
                                job.id,
                                expected_status=JobStatus.CANCELING,
                                new_status=JobStatus.CANCELED,
                                error="canceled",
                            )
                            conn.execute("COMMIT")
                            committed = True
                            if canceled_id is not None:
                                self._bus.publish(
                                    {
                                        "event": "canceled",
                                        "job_id": job.id,
                                        "done": 0,
                                        "failed": 0,
                                        "_event_id": canceled_id,
                                    }
                                )
                        else:
                            finished_id = finish(
                                conn,
                                job.id,
                                status=JobStatus.DONE,
                                output_path=None,
                                error="empty playlist",
                            )
                            conn.execute("COMMIT")
                            committed = True
                            # Publish events AFTER commit so subscribers see
                            # committed state.
                            self._bus.publish(
                                {
                                    "event": "finished",
                                    "job_id": job.id,
                                    "done": 0,
                                    "failed": 0,
                                    "_event_id": finished_id,
                                }
                            )
                        return

                    # Pre-loop snapshot saw the cancel: parent already known
                    # canceled, children already CANCELED inside the loop
                    # above. Finalize the parent here (CAS CANCELING -> CANCELED).
                    if parent_canceled_pre:
                        canceled_id = finish_if_status(
                            conn,
                            job.id,
                            expected_status=JobStatus.CANCELING,
                            new_status=JobStatus.CANCELED,
                            output_path=None,
                            error="canceled",
                        )
                        conn.execute("COMMIT")
                        committed = True
                        if canceled_id is not None:
                            self._bus.publish(
                                {
                                    "event": "canceled",
                                    "job_id": job.id,
                                    "done": 0,
                                    "failed": enqueued_children,
                                    "_event_id": canceled_id,
                                }
                            )
                        return

                    # Cancel-during-loop race: re-read the parent. With
                    # BEGIN IMMEDIATE this shouldn't fire (the cancel API
                    # blocks on the write lock until we commit), but keep
                    # it as a safety net in case any future caller mutates
                    # state through a different path.
                    current = get_job(conn, job.id)
                    if current is not None and current.status == JobStatus.CANCELING:
                        for child_id in enqueued_child_ids:
                            cur = conn.execute(
                                """
                                UPDATE jobs SET status=?, finished_at=?
                                WHERE id=? AND status=?
                                """,
                                (
                                    JobStatus.CANCELED.value,
                                    int(time.time() * 1000),
                                    child_id,
                                    JobStatus.PENDING.value,
                                ),
                            )
                            if cur.rowcount > 0:
                                record_event(
                                    conn,
                                    child_id,
                                    "canceled",
                                    {"reason": "parent canceled mid-expansion"},
                                )
                        canceled_id = finish_if_status(
                            conn,
                            job.id,
                            expected_status=JobStatus.CANCELING,
                            new_status=JobStatus.CANCELED,
                            output_path=None,
                            error="canceled",
                        )
                        conn.execute("COMMIT")
                        committed = True
                        if canceled_id is not None:
                            self._bus.publish(
                                {
                                    "event": "canceled",
                                    "job_id": job.id,
                                    "done": 0,
                                    "failed": enqueued_children,
                                    "_event_id": canceled_id,
                                }
                            )
                        return

                    # Normal path: parent stays RUNNING until all children
                    # reach a terminal state; the last child to finish flips
                    # the parent.
                    conn.execute("COMMIT")
                    committed = True
                    self._bus.publish(
                        {
                            "event": "expanded",
                            "job_id": job.id,
                            "child_count": enqueued_children,
                        }
                    )
                    return
                finally:
                    if not committed:
                        try:
                            conn.execute("ROLLBACK")
                        except Exception:
                            pass

        # Regular video download (top-level video OR playlist child).
        await self._download_video(conn, job)

        # If this was a playlist child, re-check parent completion. Only the
        # last child to reach a terminal state will actually flip the parent.
        if job.parent_job_id is not None:
            terminal, done, failed = all_children_terminal(conn, job.parent_job_id)
            if terminal:
                # Try DONE only if the parent is still RUNNING. If it raced
                # to CANCELING via a user DELETE, this CAS misses and we
                # honor the cancel instead.
                done_id = finish_if_status(
                    conn,
                    job.parent_job_id,
                    expected_status=JobStatus.RUNNING,
                    new_status=JobStatus.DONE,
                    output_path=None,
                    error=(f"{failed} child(ren) failed" if failed else None),
                )
                if done_id is not None:
                    self._bus.publish(
                        {
                            "event": "finished",
                            "job_id": job.parent_job_id,
                            "done": done,
                            "failed": failed,
                            "_event_id": done_id,
                        }
                    )
                else:
                    # Parent was canceled while children were finishing.
                    # Finalize as CANCELED if it's still in CANCELING.
                    cancel_id = finish_if_status(
                        conn,
                        job.parent_job_id,
                        expected_status=JobStatus.CANCELING,
                        new_status=JobStatus.CANCELED,
                        output_path=None,
                        error="canceled",
                    )
                    if cancel_id is not None:
                        self._bus.publish(
                            {
                                "event": "canceled",
                                "job_id": job.parent_job_id,
                                "done": done,
                                "failed": failed,
                                "_event_id": cancel_id,
                            }
                        )
                    # If neither CAS hit, another worker already finalized
                    # the parent — nothing to do.

    async def _download_video(self, conn, job) -> None:
        loop = asyncio.get_running_loop()

        def cancel_flag() -> bool:
            if self._cancel_flags.get(job.id):
                return True
            current = get_job(conn, job.id)
            return current is not None and current.status == JobStatus.CANCELING

        def on_progress(d: dict) -> None:
            # Runs on the worker thread spawned by asyncio.to_thread below.
            # Anything touching loop-owned state (the bus's asyncio.Queues)
            # must be marshalled back to the loop via publish_threadsafe.
            if d.get("status") == "downloading":
                update_progress(
                    conn,
                    job.id,
                    bytes_done=d.get("downloaded_bytes"),
                    speed_bps=int(d["speed"]) if d.get("speed") else None,
                    eta_s=int(d["eta"]) if d.get("eta") else None,
                    filesize_bytes=d.get("total_bytes") or d.get("total_bytes_estimate"),
                )
            self._bus.publish_threadsafe(
                {
                    "event": "progress",
                    "job_id": job.id,
                    "status": d.get("status"),
                    "downloaded_bytes": d.get("downloaded_bytes"),
                    "total_bytes": d.get("total_bytes") or d.get("total_bytes_estimate"),
                    "speed": d.get("speed"),
                    "eta": d.get("eta"),
                },
                loop,
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
                # On success, ensure bytes_done reflects the full file. The
                # throttled progress hook may have only captured a partial
                # value if the download finished in a single tick.
                if result.filesize_bytes:
                    update_progress(
                        conn,
                        job.id,
                        bytes_done=result.filesize_bytes,
                        filesize_bytes=result.filesize_bytes,
                    )
                finished_id = finish(
                    conn, job.id, status=JobStatus.DONE, output_path=result.output_path
                )
                self._bus.publish(
                    {
                        "event": "finished",
                        "job_id": job.id,
                        "_event_id": finished_id,
                    }
                )
                return
            except DownloadCancelled:
                canceled_id = finish(
                    conn, job.id, status=JobStatus.CANCELED, error="canceled"
                )
                self._bus.publish(
                    {
                        "event": "canceled",
                        "job_id": job.id,
                        "_event_id": canceled_id,
                    }
                )
                return
            except BaseException as exc:
                cls = classify_error(exc)
                if cls == Classification.TRANSIENT and attempt < len(self._retry_delays):
                    delay = self._retry_delays[attempt]
                    attempt += 1
                    record_event(conn, job.id, "log", {"retry_after_s": delay, "reason": str(exc)})
                    if await self._cancellable_sleep(conn, job.id, float(delay)):
                        canceled_id = finish(
                            conn, job.id, status=JobStatus.CANCELED, error="canceled"
                        )
                        self._bus.publish(
                            {
                                "event": "canceled",
                                "job_id": job.id,
                                "_event_id": canceled_id,
                            }
                        )
                        return
                    continue
                if cls == Classification.RATE_LIMITED and attempt == 0:
                    attempt += 1
                    record_event(
                        conn,
                        job.id,
                        "log",
                        {"rate_limited_for_s": self._rate_limit_delay},
                    )
                    if await self._cancellable_sleep(
                        conn, job.id, float(self._rate_limit_delay)
                    ):
                        canceled_id = finish(
                            conn, job.id, status=JobStatus.CANCELED, error="canceled"
                        )
                        self._bus.publish(
                            {
                                "event": "canceled",
                                "job_id": job.id,
                                "_event_id": canceled_id,
                            }
                        )
                        return
                    continue
                failed_id = finish(
                    conn, job.id, status=JobStatus.FAILED, error=f"[{cls.value}] {exc}"
                )
                self._bus.publish(
                    {
                        "event": "failed",
                        "job_id": job.id,
                        "classification": cls.value,
                        "error": str(exc),
                        "_event_id": failed_id,
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


def _default_probe_adapter(url: str, *, cookies_browser: str | None = None) -> dict:
    """Lazy default that delegates to the downloader's probe helper."""
    from ytdl.downloader import probe as _probe

    return _probe(url, cookies_browser=cookies_browser)
