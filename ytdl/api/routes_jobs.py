from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ytdl.api.schemas import JobCreate, JobList, JobOut
from ytdl.db import connect, migrate
from ytdl.models import Job, JobKind, JobStatus
from ytdl.queue import (
    cancel_with_children,
    children_of,
    enqueue,
    get_job,
    list_jobs,
    retry_job,
)

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
        attempts=job.attempts,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
    )


@router.post("", status_code=201)
def post_job(payload: JobCreate, request: Request) -> JobOut:
    cfg = request.app.state.config
    fmt = payload.format_pref or cfg.default_format
    out_dir = str(cfg.output_dir)
    conn = _conn(request)
    try:
        if payload.url is not None:
            job_id = enqueue(
                conn,
                url=payload.url,
                kind=JobKind.VIDEO,  # playlist detection happens at worker time
                format_pref=fmt,
                output_dir=out_dir,
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
