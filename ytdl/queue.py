"""Queue operations on top of the sqlite schema in db.py.

Every public function takes a sqlite3.Connection so the caller controls
lifetime and threading. Connections are safe to share across threads because
we open them with check_same_thread=False; sqlite serializes writes via the
busy_timeout pragma.
"""
from __future__ import annotations

import json
import sqlite3
import time

from ytdl.models import Event, Job, JobKind, JobStatus
from ytdl.ulid import new_ulid


def _now_ms() -> int:
    return int(time.time() * 1000)


def _row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        id=row["id"],
        url=row["url"],
        kind=JobKind(row["kind"]),
        parent_job_id=row["parent_job_id"],
        status=JobStatus(row["status"]),
        format_pref=row["format_pref"],
        output_dir=row["output_dir"],
        output_path=row["output_path"],
        title=row["title"],
        video_id=row["video_id"],
        uploader=row["uploader"],
        duration_s=row["duration_s"],
        filesize_bytes=row["filesize_bytes"],
        bytes_done=row["bytes_done"],
        speed_bps=row["speed_bps"],
        eta_s=row["eta_s"],
        error=row["error"],
        attempts=row["attempts"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
    )


def record_event(
    conn: sqlite3.Connection,
    job_id: str,
    kind: str,
    payload: dict | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO events(job_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (job_id, kind, json.dumps(payload or {}), _now_ms()),
    )
    return int(cur.lastrowid or 0)


def enqueue(
    conn: sqlite3.Connection,
    *,
    url: str,
    kind: JobKind,
    format_pref: str,
    output_dir: str,
    parent_job_id: str | None = None,
) -> str:
    job_id = new_ulid()
    conn.execute(
        """
        INSERT INTO jobs(
            id, url, kind, parent_job_id, status, format_pref, output_dir,
            attempts, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)
        """,
        (
            job_id,
            url,
            kind.value,
            parent_job_id,
            JobStatus.PENDING.value,
            format_pref,
            output_dir,
            _now_ms(),
        ),
    )
    record_event(conn, job_id, "enqueued", {"url": url, "kind": kind.value})
    return job_id


def claim_one(conn: sqlite3.Connection) -> Job | None:
    """Atomically pick the oldest pending job and mark it running.

    Skips PENDING jobs whose parent is no longer RUNNING (e.g., parent was
    canceled before we got to its children).
    """
    started = _now_ms()
    row = conn.execute(
        """
        UPDATE jobs
        SET status = ?, started_at = ?, attempts = attempts + 1
        WHERE id = (
            SELECT j.id FROM jobs j
            LEFT JOIN jobs p ON p.id = j.parent_job_id
            WHERE j.status = ?
              AND (j.parent_job_id IS NULL OR p.status = ?)
            ORDER BY j.created_at ASC
            LIMIT 1
        )
        RETURNING *
        """,
        (
            JobStatus.RUNNING.value,
            started,
            JobStatus.PENDING.value,
            JobStatus.RUNNING.value,
        ),
    ).fetchone()
    if row is None:
        return None
    record_event(conn, row["id"], "started", {})
    return _row_to_job(row)


def get_job(conn: sqlite3.Connection, job_id: str) -> Job | None:
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(
    conn: sqlite3.Connection,
    *,
    status: JobStatus | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Job]:
    if status is None:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (status.value, limit, offset),
        ).fetchall()
    return [_row_to_job(r) for r in rows]


def update_progress(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    bytes_done: int | None = None,
    speed_bps: int | None = None,
    eta_s: int | None = None,
    filesize_bytes: int | None = None,
) -> None:
    conn.execute(
        """
        UPDATE jobs SET
            bytes_done = COALESCE(?, bytes_done),
            speed_bps  = COALESCE(?, speed_bps),
            eta_s      = COALESCE(?, eta_s),
            filesize_bytes = COALESCE(?, filesize_bytes)
        WHERE id = ?
        """,
        (bytes_done, speed_bps, eta_s, filesize_bytes, job_id),
    )


def update_metadata(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    title: str | None = None,
    video_id: str | None = None,
    uploader: str | None = None,
    duration_s: int | None = None,
) -> None:
    conn.execute(
        """
        UPDATE jobs SET
            title    = COALESCE(?, title),
            video_id = COALESCE(?, video_id),
            uploader = COALESCE(?, uploader),
            duration_s = COALESCE(?, duration_s)
        WHERE id = ?
        """,
        (title, video_id, uploader, duration_s, job_id),
    )


def finish(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    status: JobStatus,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE jobs SET
            status = ?,
            output_path = COALESCE(?, output_path),
            error = COALESCE(?, error),
            finished_at = ?
        WHERE id = ?
        """,
        (status.value, output_path, error, _now_ms(), job_id),
    )
    record_event(
        conn,
        job_id,
        {
            JobStatus.DONE: "finished",
            JobStatus.FAILED: "failed",
            JobStatus.CANCELED: "canceled",
        }.get(status, "log"),
        {"output_path": output_path, "error": error},
    )


def finish_if_status(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    expected_status: JobStatus,
    new_status: JobStatus,
    output_path: str | None = None,
    error: str | None = None,
) -> bool:
    """Atomically finish a job only if it's currently in expected_status.

    Used by the playlist reaper so a concurrent cancel can't be overwritten:
    if the parent flipped from RUNNING to CANCELING between read and write,
    the UPDATE matches zero rows and the reaper falls back to a CANCELED
    finalization.
    """
    cur = conn.execute(
        """
        UPDATE jobs SET
            status = ?,
            output_path = COALESCE(?, output_path),
            error = COALESCE(?, error),
            finished_at = ?
        WHERE id = ? AND status = ?
        """,
        (
            new_status.value,
            output_path,
            error,
            _now_ms(),
            job_id,
            expected_status.value,
        ),
    )
    if cur.rowcount > 0:
        kind = {
            JobStatus.DONE: "finished",
            JobStatus.FAILED: "failed",
            JobStatus.CANCELED: "canceled",
        }.get(new_status, "log")
        record_event(conn, job_id, kind, {"output_path": output_path, "error": error})
        return True
    return False


def cancel(conn: sqlite3.Connection, job_id: str) -> bool:
    """Cancel pending -> canceled directly; running -> canceling (worker observes flag)."""
    cur = conn.execute(
        """
        UPDATE jobs SET status = ?
        WHERE id = ? AND status = ?
        """,
        (JobStatus.CANCELED.value, job_id, JobStatus.PENDING.value),
    )
    if cur.rowcount > 0:
        record_event(conn, job_id, "canceled", {})
        return True
    cur = conn.execute(
        """
        UPDATE jobs SET status = ?
        WHERE id = ? AND status = ?
        """,
        (JobStatus.CANCELING.value, job_id, JobStatus.RUNNING.value),
    )
    return cur.rowcount > 0


def cancel_with_children(conn: sqlite3.Connection, job_id: str) -> bool:
    """Cascade-cancel a job and all of its non-terminal children atomically.

    Order matters: flip the parent state first so any reaper that races in
    sees the CANCELING/CANCELED intent before deciding the parent's
    terminal status. Then transition children.

    For each non-terminal child:
      - PENDING -> CANCELED (terminal, emits 'canceled' event)
      - RUNNING -> CANCELING (worker will observe and abort)
    Already-terminal children are left alone.

    Returns True if anything changed (parent or any child), else False.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        changed = False

        # Parent first — same logic as cancel() but inlined inside the txn.
        parent_pending = conn.execute(
            """
            UPDATE jobs SET status = ?, finished_at = ?
            WHERE id = ? AND status = ?
            """,
            (JobStatus.CANCELED.value, _now_ms(), job_id, JobStatus.PENDING.value),
        ).rowcount
        if parent_pending > 0:
            record_event(conn, job_id, "canceled", {})
            changed = True
        else:
            parent_running = conn.execute(
                """
                UPDATE jobs SET status = ?
                WHERE id = ? AND status = ?
                """,
                (JobStatus.CANCELING.value, job_id, JobStatus.RUNNING.value),
            ).rowcount
            if parent_running > 0:
                changed = True

        # Children second.
        pending_kids = conn.execute(
            """
            UPDATE jobs SET status = ?, finished_at = ?
            WHERE parent_job_id = ? AND status = ?
            RETURNING id
            """,
            (JobStatus.CANCELED.value, _now_ms(), job_id, JobStatus.PENDING.value),
        ).fetchall()
        for row in pending_kids:
            record_event(conn, row["id"], "canceled", {"reason": "parent canceled"})
            changed = True

        running_kids = conn.execute(
            """
            UPDATE jobs SET status = ?
            WHERE parent_job_id = ? AND status = ?
            RETURNING id
            """,
            (JobStatus.CANCELING.value, job_id, JobStatus.RUNNING.value),
        ).fetchall()
        if running_kids:
            changed = True

        # If the cascade left no non-terminal children behind AND the
        # parent is a playlist (so no worker will fire the reaper),
        # finalize the parent here so the queue drains. For standalone
        # videos, the active downloader thread handles the terminal
        # transition once it observes CANCELING.
        parent_kind_row = conn.execute(
            "SELECT kind FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if parent_kind_row is not None and parent_kind_row["kind"] == JobKind.PLAYLIST.value:
            any_alive = conn.execute(
                """
                SELECT 1 FROM jobs
                WHERE parent_job_id = ?
                  AND status NOT IN (?, ?, ?)
                LIMIT 1
                """,
                (
                    job_id,
                    JobStatus.DONE.value,
                    JobStatus.FAILED.value,
                    JobStatus.CANCELED.value,
                ),
            ).fetchone()
            if any_alive is None:
                cur = conn.execute(
                    """
                    UPDATE jobs SET status = ?, finished_at = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        JobStatus.CANCELED.value,
                        _now_ms(),
                        job_id,
                        JobStatus.CANCELING.value,
                    ),
                )
                if cur.rowcount > 0:
                    record_event(conn, job_id, "canceled", {})
                    changed = True

        conn.execute("COMMIT")
        return changed
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def revive_orphans(conn: sqlite3.Connection, *, max_attempts: int = 3) -> int:
    """Reset orphans after a crash.

    - CANCELING (user requested cancel, worker died before observing) -> CANCELED.
    - RUNNING with attempts >= max_attempts -> FAILED (exhausted).
    - RUNNING -> PENDING (will be retried).
    """
    # Honor any in-flight cancel requests that the worker can no longer observe.
    canceling_now = conn.execute(
        """
        UPDATE jobs SET status = ?, finished_at = ?
        WHERE status = ?
        """,
        (JobStatus.CANCELED.value, _now_ms(), JobStatus.CANCELING.value),
    ).rowcount
    # Mark exhausted as failed first so the reset query doesn't move them.
    exhausted = conn.execute(
        """
        UPDATE jobs SET status = ?, error = ?, finished_at = ?
        WHERE status = ? AND attempts >= ?
        """,
        (
            JobStatus.FAILED.value,
            "worker crashed and retries exhausted",
            _now_ms(),
            JobStatus.RUNNING.value,
            max_attempts,
        ),
    ).rowcount
    revived = conn.execute(
        """
        UPDATE jobs SET status = ?, started_at = NULL
        WHERE status = ?
        """,
        (JobStatus.PENDING.value, JobStatus.RUNNING.value),
    ).rowcount
    return revived + exhausted + canceling_now


def promote_to_playlist(
    conn: sqlite3.Connection, job_id: str, *, title: str | None
) -> None:
    """Re-classify a job as a playlist parent and optionally set its title."""
    conn.execute(
        "UPDATE jobs SET kind = ?, title = COALESCE(?, title) WHERE id = ?",
        (JobKind.PLAYLIST.value, title, job_id),
    )


def children_of(conn: sqlite3.Connection, parent_id: str) -> list[Job]:
    """Return all direct children of a playlist parent, oldest first."""
    rows = conn.execute(
        "SELECT * FROM jobs WHERE parent_job_id = ? ORDER BY created_at ASC",
        (parent_id,),
    ).fetchall()
    return [_row_to_job(r) for r in rows]


def all_children_terminal(
    conn: sqlite3.Connection, parent_id: str
) -> tuple[bool, int, int]:
    """Return (all_terminal, done_count, failed_count) for a parent's children.

    Returns (False, 0, 0) when the parent has no children yet.
    """
    rows = conn.execute(
        "SELECT status FROM jobs WHERE parent_job_id = ?", (parent_id,)
    ).fetchall()
    if not rows:
        return False, 0, 0
    terminal_set = {
        JobStatus.DONE.value,
        JobStatus.FAILED.value,
        JobStatus.CANCELED.value,
    }
    done = sum(1 for r in rows if r["status"] == JobStatus.DONE.value)
    failed = sum(1 for r in rows if r["status"] == JobStatus.FAILED.value)
    return all(r["status"] in terminal_set for r in rows), done, failed


def list_events_since(
    conn: sqlite3.Connection, since_id: int, limit: int = 1000
) -> list[Event]:
    rows = conn.execute(
        "SELECT * FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
        (since_id, limit),
    ).fetchall()
    return [
        Event(
            id=r["id"],
            job_id=r["job_id"],
            kind=r["kind"],
            payload=json.loads(r["payload_json"]),
            created_at=r["created_at"],
        )
        for r in rows
    ]
