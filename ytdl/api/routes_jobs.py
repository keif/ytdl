from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ytdl.api.schemas import JobCreate, JobList, JobOut
from ytdl.db import connect, migrate
from ytdl.models import Job, JobKind, JobStatus
from ytdl.queue import (
    cancel_with_children,
    children_of,
    clear_done_jobs,
    count_clearable,
    enqueue,
    get_job,
    list_jobs,
    retry_job,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _resolve_output_dir(raw: str) -> str:
    """Expand and validate a per-job output_dir override.

    Rules:
      - Tilde expansion (``~/Music`` -> ``/home/user/Music``) so the UI can
        accept the same shorthand users write in shells.
      - If the path exists, it must be a directory and writable. Pointing at a
        file is a configuration error.
      - If the path doesn't exist, the parent must exist and be writable so
        the worker can create the directory at download time (it already calls
        ``Path(...).mkdir(parents=True, exist_ok=True)``).
      - Errors raise HTTPException 400 with a generic message; the specific
        failure mode isn't surfaced to the client to avoid leaking filesystem
        layout details.
    """
    expanded = Path(raw).expanduser()
    if expanded.exists():
        if not expanded.is_dir() or not os.access(expanded, os.W_OK):
            raise HTTPException(
                status_code=400,
                detail="output_dir must be a writable directory",
            )
    else:
        parent = expanded.parent
        if not parent.exists() or not parent.is_dir() or not os.access(
            parent, os.W_OK
        ):
            raise HTTPException(
                status_code=400,
                detail="output_dir must be a writable directory",
            )
    return str(expanded)


def _conn(request: Request):
    cfg = request.app.state.config
    conn = connect(cfg.db_path)
    migrate(conn)
    return conn


def _to_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        url=job.url,
        kind=job.kind.value,
        parent_job_id=job.parent_job_id,
        status=job.status.value,
        format_pref=job.format_pref,
        output_dir=job.output_dir,
        output_path=job.output_path,
        title=job.title,
        video_id=job.video_id,
        uploader=job.uploader,
        duration_s=job.duration_s,
        filesize_bytes=job.filesize_bytes,
        bytes_done=job.bytes_done,
        speed_bps=job.speed_bps,
        eta_s=job.eta_s,
        error=job.error,
        force_overwrite=job.force_overwrite,
        subtitles=job.subtitles,
        attempts=job.attempts,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("", status_code=201)
def post_job(payload: JobCreate, request: Request) -> JobOut:
    cfg = request.app.state.config
    fmt = payload.format_pref or cfg.default_format
    # Per-job output_dir override falls back to the server default when the
    # client omits it. Validation runs once for the whole request so a bad
    # path on the urls[] branch fails before any child is enqueued.
    if payload.output_dir is not None:
        out_dir = _resolve_output_dir(payload.output_dir)
    else:
        out_dir = str(cfg.output_dir)
    # None on the wire == "use the server default". An explicit true/false
    # always wins so users can opt out of a globally-enabled default for a
    # single URL.
    subs = (
        payload.subtitles
        if payload.subtitles is not None
        else cfg.subtitles_default
    )
    conn = _conn(request)
    try:
        if payload.url is not None:
            job_id = enqueue(
                conn,
                url=payload.url,
                kind=JobKind.VIDEO,  # playlist detection happens at worker time
                format_pref=fmt,
                output_dir=out_dir,
                subtitles=subs,
            )
        else:
            # Picked subset from a playlist preview. Each URL becomes its own
            # standalone VIDEO job — no synthetic parent (yet); the UI shows
            # them as N rows. Wrap in a single immediate-write transaction so
            # the batch is atomic and observers don't see a partial fan-out.
            assert payload.urls is not None  # exactly_one_source guarantees
            conn.execute("BEGIN IMMEDIATE")
            committed = False
            try:
                first_id: str | None = None
                for child_url in payload.urls:
                    job_id = enqueue(
                        conn,
                        url=child_url,
                        kind=JobKind.VIDEO,
                        format_pref=fmt,
                        output_dir=out_dir,
                        subtitles=subs,
                    )
                    if first_id is None:
                        first_id = job_id
                conn.execute("COMMIT")
                committed = True
                assert first_id is not None
                job_id = first_id
            except BaseException:
                if not committed:
                    try:
                        conn.execute("ROLLBACK")
                    except Exception:
                        pass
                raise

        job = get_job(conn, job_id)
        assert job is not None
        return _to_out(job)
    finally:
        conn.close()


@router.get("", response_model=JobList)
def list_endpoint(
    request: Request,
    status: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> JobList:
    conn = _conn(request)
    try:
        parsed_status: JobStatus | None = None
        if status:
            try:
                parsed_status = JobStatus(status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"unknown status {status!r}; "
                        "valid: pending|running|done|failed|canceling|canceled"
                    ),
                ) from exc
        jobs = list_jobs(conn, status=parsed_status, limit=limit, offset=offset)
        return JobList(jobs=[_to_out(j) for j in jobs], total=len(jobs))
    finally:
        conn.close()


class ClearResponse(BaseModel):
    deleted: int


class ClearPreviewResponse(BaseModel):
    clearable: int
    older_than_days: int


@router.get("/clear/preview", response_model=ClearPreviewResponse)
def clear_preview(request: Request, older_than_days: int = 7) -> ClearPreviewResponse:
    """How many DONE jobs are old enough to clear? Used by the UI to show
    the button label like 'Clear N done jobs'."""
    if older_than_days < 0:
        raise HTTPException(status_code=422, detail="older_than_days must be >= 0")
    older_than_ms = older_than_days * 86_400_000
    conn = _conn(request)
    try:
        n = count_clearable(conn, older_than_ms=older_than_ms)
        return ClearPreviewResponse(clearable=n, older_than_days=older_than_days)
    finally:
        conn.close()


@router.post("/clear", response_model=ClearResponse)
def clear_endpoint(request: Request, older_than_days: int = 7) -> ClearResponse:
    """Delete DONE jobs older than `older_than_days` (default 7)."""
    if older_than_days < 0:
        raise HTTPException(status_code=422, detail="older_than_days must be >= 0")
    older_than_ms = older_than_days * 86_400_000
    conn = _conn(request)
    try:
        n = clear_done_jobs(conn, older_than_ms=older_than_ms)
        return ClearResponse(deleted=n)
    finally:
        conn.close()


@router.get("/{job_id}")
def get_endpoint(job_id: str, request: Request) -> JobOut:
    conn = _conn(request)
    try:
        job = get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return _to_out(job)
    finally:
        conn.close()


@router.post("/{job_id}/retry", status_code=201)
def retry_endpoint(job_id: str, request: Request) -> JobOut:
    conn = _conn(request)
    try:
        new_id = retry_job(conn, job_id)
        if new_id is None:
            raise HTTPException(
                status_code=400,
                detail="job not found, or not in a state that can be retried",
            )
        job = get_job(conn, new_id)
        assert job is not None
        return _to_out(job)
    finally:
        conn.close()


@router.post("/{job_id}/redownload", status_code=201)
def redownload_endpoint(job_id: str, request: Request) -> JobOut:
    """Clone the source job with force_overwrite=True so yt-dlp re-fetches the
    file even if it already exists on disk.

    Distinct from /retry: retry leaves nooverwrites=True (yt-dlp's default),
    which means a DONE job whose output file is still on disk gets silently
    skipped. Re-download is the explicit "I really want a fresh copy" path
    — used when the previous download was corrupt, used the wrong format,
    or the YouTube source changed.
    """
    conn = _conn(request)
    try:
        new_id = retry_job(conn, job_id, force_overwrite=True)
        if new_id is None:
            raise HTTPException(
                status_code=400,
                detail="job not found, or not in a state that can be retried",
            )
        job = get_job(conn, new_id)
        assert job is not None
        return _to_out(job)
    finally:
        conn.close()


@router.delete("/{job_id}", status_code=204)
def delete_endpoint(job_id: str, request: Request) -> Response:
    conn = _conn(request)
    try:
        job = get_job(conn, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        # Cascade-cancel: also flips any children of a playlist parent.
        cancel_with_children(conn, job_id)
        sup = getattr(request.app.state, "supervisor", None)
        if sup is not None:
            sup.request_cancel(job_id)
            # Also nudge supervisor for any currently-running children so
            # their progress-hook closures see the in-memory flag quickly
            # instead of waiting for the per-tick DB status check.
            for child in children_of(conn, job_id):
                if child.status in (JobStatus.RUNNING, JobStatus.CANCELING):
                    sup.request_cancel(child.id)
        return Response(status_code=204)
    finally:
        conn.close()
