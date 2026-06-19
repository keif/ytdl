from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from ytdl.db import connect, migrate
from ytdl.models import JobKind, JobStatus
from ytdl.queue import (
    cancel,
    cancel_with_children,
    claim_one,
    enqueue,
    finish,
    finish_if_status,
    get_job,
    list_jobs,
    promote_to_playlist,
    record_event,
    revive_orphans,
    update_progress,
)


def _setup(tmp_path: Path):
    conn = connect(tmp_path / "test.db")
    migrate(conn)
    return conn


def test_enqueue_creates_pending_job_and_event(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(
        conn,
        url="https://youtu.be/abc",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/out",
    )
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.PENDING
    assert row["url"] == "https://youtu.be/abc"
    evt = conn.execute(
        "SELECT * FROM events WHERE job_id=?", (job_id,)
    ).fetchone()
    assert evt["kind"] == "enqueued"


def test_claim_one_returns_oldest_pending_and_marks_running(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    a = enqueue(conn, url="a", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    b = enqueue(conn, url="b", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")

    claimed = claim_one(conn)
    assert claimed is not None
    assert claimed.id == a
    assert claimed.status == JobStatus.RUNNING

    second = claim_one(conn)
    assert second is not None
    assert second.id == b


def test_claim_one_returns_none_when_empty(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    assert claim_one(conn) is None


def test_concurrent_claim_each_job_claimed_once(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    ids = [
        enqueue(conn, url=f"u{i}", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
        for i in range(20)
    ]
    claimed: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        local = connect(tmp_path / "test.db")
        while True:
            job = claim_one(local)
            if job is None:
                return
            with lock:
                claimed.append(job.id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(claimed) == sorted(ids), "every job claimed exactly once"


def test_update_progress_writes_throttled_fields(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)
    update_progress(conn, job_id, bytes_done=1024, speed_bps=512, eta_s=10)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["bytes_done"] == 1024
    assert row["speed_bps"] == 512
    assert row["eta_s"] == 10


def test_finish_success(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)
    finish(conn, job_id, status=JobStatus.DONE, output_path="/out/x.mp4")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.DONE
    assert row["output_path"] == "/out/x.mp4"
    assert row["finished_at"] is not None


def test_finish_failure_records_error(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)
    finish(conn, job_id, status=JobStatus.FAILED, error="age-restricted")
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.FAILED
    assert row["error"] == "age-restricted"


def test_cancel_pending_goes_straight_to_canceled(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    cancel(conn, job_id)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELED


def test_cancel_running_goes_to_canceling(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)
    cancel(conn, job_id)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELING


def test_cancel_with_children_no_children(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(
        conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o"
    )
    assert cancel_with_children(conn, job_id) is True
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELED.value


def test_cancel_with_children_cascades_to_pending_and_running(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    parent = enqueue(
        conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o"
    )
    child_pending = enqueue(
        conn,
        url="cp",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent,
    )
    child_running = enqueue(
        conn,
        url="cr",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent,
    )
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (child_running,))
    child_done = enqueue(
        conn,
        url="cd",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent,
    )
    conn.execute("UPDATE jobs SET status='done' WHERE id=?", (child_done,))
    # Promote parent to playlist + flip to RUNNING so cancel routes through
    # the running branch (the more interesting case post-expansion).
    promote_to_playlist(conn, parent, title="P")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (parent,))

    assert cancel_with_children(conn, parent) is True
    statuses = {
        r["id"]: r["status"]
        for r in conn.execute(
            "SELECT id, status FROM jobs WHERE id IN (?, ?, ?, ?)",
            (parent, child_pending, child_running, child_done),
        ).fetchall()
    }
    # Running parent -> CANCELING (worker reaper will finalize as CANCELED).
    assert statuses[parent] == JobStatus.CANCELING.value
    assert statuses[child_pending] == JobStatus.CANCELED.value
    assert statuses[child_running] == JobStatus.CANCELING.value
    # Already-terminal child is left alone.
    assert statuses[child_done] == JobStatus.DONE.value


def test_cancel_with_children_finalizes_parent_when_no_running_kids(tmp_path: Path) -> None:
    """A playlist with all-PENDING children gets canceled before any worker
    starts. Parent should land CANCELED (not stuck CANCELING) because no
    reaper will ever fire."""
    conn = _setup(tmp_path)
    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    promote_to_playlist(conn, parent, title="P")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (parent,))
    for i in range(3):
        enqueue(
            conn,
            url=f"c{i}",
            kind=JobKind.VIDEO,
            format_pref="best",
            output_dir="/o",
            parent_job_id=parent,
        )
    # All children are PENDING.
    assert cancel_with_children(conn, parent) is True
    parent_row = conn.execute("SELECT status FROM jobs WHERE id=?", (parent,)).fetchone()
    assert parent_row["status"] == JobStatus.CANCELED.value, (
        f"parent should be CANCELED when cascade leaves no running children, "
        f"got {parent_row['status']}"
    )


def test_cancel_with_children_keeps_parent_canceling_when_kids_still_running(
    tmp_path: Path,
) -> None:
    """When the cascade leaves CANCELING children behind, those workers will
    eventually fire the reaper. The parent stays CANCELING until then."""
    conn = _setup(tmp_path)
    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    promote_to_playlist(conn, parent, title="P")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (parent,))
    running_child = enqueue(
        conn,
        url="rc",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent,
    )
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (running_child,))
    assert cancel_with_children(conn, parent) is True
    parent_row = conn.execute("SELECT status FROM jobs WHERE id=?", (parent,)).fetchone()
    # Still CANCELING — the running child must abort before we finalize.
    assert parent_row["status"] == JobStatus.CANCELING.value


def test_cancel_with_children_atomic_parent_first(tmp_path: Path) -> None:
    """The cascade transitions the parent before the children, so a reaper
    racing during the cancel sees the parent's CANCELING state."""
    conn = _setup(tmp_path)
    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    child = enqueue(
        conn,
        url="c",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent,
    )
    conn.execute("UPDATE jobs SET status='running' WHERE id IN (?, ?)", (parent, child))

    assert cancel_with_children(conn, parent) is True
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (parent,)).fetchone()
    assert row["status"] == JobStatus.CANCELING.value


def test_cancel_with_children_does_not_finalize_standalone_running_video(tmp_path: Path) -> None:
    """A standalone (non-playlist) running video must stay CANCELING after
    cancel — the active downloader thread owns the terminal transition.
    Finalizing here would race ahead of the worker's DownloadCancelled
    handler."""
    conn = _setup(tmp_path)
    job_id = enqueue(
        conn, url="https://yt/x", kind=JobKind.VIDEO,
        format_pref="best", output_dir="/o",
    )
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    # Standalone video — no children, kind=VIDEO.
    assert cancel_with_children(conn, job_id) is True
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELING.value, (
        f"standalone video should stay CANCELING after cancel; got {row['status']}"
    )


def test_finish_if_status_cas_misses_when_status_differs(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute("UPDATE jobs SET status='canceling' WHERE id=?", (job_id,))
    result = finish_if_status(
        conn,
        job_id,
        expected_status=JobStatus.RUNNING,
        new_status=JobStatus.DONE,
    )
    assert result is None
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELING.value


def test_finish_if_status_cas_hits_when_status_matches(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    result = finish_if_status(
        conn,
        job_id,
        expected_status=JobStatus.RUNNING,
        new_status=JobStatus.DONE,
        output_path="/out/x.mp4",
    )
    assert isinstance(result, int) and result > 0
    row = conn.execute(
        "SELECT status, output_path FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert row["status"] == JobStatus.DONE.value
    assert row["output_path"] == "/out/x.mp4"


def test_claim_one_skips_children_of_non_running_parent(tmp_path: Path) -> None:
    """A PENDING child whose parent is no longer RUNNING must not be claimed."""
    conn = _setup(tmp_path)
    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    enqueue(
        conn,
        url="c",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent,
    )
    # Parent canceled before its child gets claimed.
    conn.execute("UPDATE jobs SET status='canceled' WHERE id=?", (parent,))
    claimed = claim_one(conn)
    assert claimed is None
    # An unrelated top-level pending job should still claim fine.
    standalone = enqueue(conn, url="s", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claimed = claim_one(conn)
    assert claimed is not None
    assert claimed.id == standalone


def test_cancel_with_children_leaves_terminal_parent_alone(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(
        conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o"
    )
    conn.execute("UPDATE jobs SET status='done' WHERE id=?", (job_id,))
    assert cancel_with_children(conn, job_id) is False
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.DONE.value


def test_revive_orphans_resets_running_to_pending(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)  # now RUNNING
    n = revive_orphans(conn, max_attempts=3)
    assert n == 1
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.PENDING


def test_revive_orphans_finishes_canceling_jobs(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)  # RUNNING
    cancel(conn, job_id)  # CANCELING
    n = revive_orphans(conn)
    assert n == 1
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELED.value


def test_revive_orphans_marks_exhausted_as_failed(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)
    conn.execute("UPDATE jobs SET attempts=3 WHERE id=?", (job_id,))
    revive_orphans(conn, max_attempts=3)
    row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.FAILED


def test_list_jobs_filters_by_status(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    a = enqueue(conn, url="a", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    b = enqueue(conn, url="b", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claim_one(conn)  # a -> running
    pending = list_jobs(conn, status=JobStatus.PENDING)
    running = list_jobs(conn, status=JobStatus.RUNNING)
    assert [j.id for j in pending] == [b]
    assert [j.id for j in running] == [a]


def test_record_event_serializes_payload(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    record_event(conn, job_id, kind="log", payload={"msg": "hello", "n": 7})
    row = conn.execute(
        "SELECT * FROM events WHERE job_id=? AND kind='log'", (job_id,)
    ).fetchone()
    assert json.loads(row["payload_json"]) == {"msg": "hello", "n": 7}


def test_get_job_returns_none_for_unknown(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    assert get_job(conn, "no-such-id") is None


def test_finish_returns_event_id(tmp_path: Path) -> None:
    """finish() returns the new events row id so callers can publish it on
    the bus as _event_id for SSE Last-Event-ID resume."""
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    event_id = finish(conn, job_id, status=JobStatus.DONE, output_path="/out/x.mp4")
    assert isinstance(event_id, int) and event_id > 0
    row = conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
    assert row["kind"] == "finished"
    assert row["job_id"] == job_id


def test_finish_if_status_returns_id_on_hit_and_none_on_miss(tmp_path: Path) -> None:
    """finish_if_status() returns int on a successful CAS, None when the CAS
    misses (status was not the expected value)."""
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    hit = finish_if_status(
        conn,
        job_id,
        expected_status=JobStatus.RUNNING,
        new_status=JobStatus.DONE,
        output_path="/out/x.mp4",
    )
    assert isinstance(hit, int) and hit > 0

    # Second call misses (status is now DONE, not RUNNING).
    miss = finish_if_status(
        conn,
        job_id,
        expected_status=JobStatus.RUNNING,
        new_status=JobStatus.DONE,
    )
    assert miss is None


def test_retry_job_creates_new_pending_job_from_failed(tmp_path: Path) -> None:
    from ytdl.queue import retry_job

    conn = _setup(tmp_path)
    job_id = enqueue(
        conn, url="https://yt/x", kind=JobKind.VIDEO,
        format_pref="1080p", output_dir="/o",
    )
    conn.execute(
        "UPDATE jobs SET status='failed', error='boom' WHERE id=?", (job_id,)
    )

    new_id = retry_job(conn, job_id)
    assert new_id is not None
    assert new_id != job_id
    row = conn.execute(
        "SELECT url, format_pref, output_dir, status FROM jobs WHERE id=?",
        (new_id,),
    ).fetchone()
    assert row["url"] == "https://yt/x"
    assert row["format_pref"] == "1080p"
    assert row["output_dir"] == "/o"
    assert row["status"] == JobStatus.PENDING.value
    # Original row still in failed state — history preserved.
    orig = conn.execute(
        "SELECT status FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert orig["status"] == JobStatus.FAILED.value


def test_retry_job_works_for_canceled_and_done(tmp_path: Path) -> None:
    from ytdl.queue import retry_job

    conn = _setup(tmp_path)
    for status in ("canceled", "done"):
        job_id = enqueue(
            conn, url=f"https://yt/{status}", kind=JobKind.VIDEO,
            format_pref="best", output_dir="/o",
        )
        conn.execute("UPDATE jobs SET status=? WHERE id=?", (status, job_id))
        new_id = retry_job(conn, job_id)
        assert new_id is not None, f"retry should succeed for {status}"


def test_retry_job_rejects_running_or_pending(tmp_path: Path) -> None:
    from ytdl.queue import retry_job

    conn = _setup(tmp_path)
    job_id = enqueue(
        conn, url="https://yt/x", kind=JobKind.VIDEO,
        format_pref="best", output_dir="/o",
    )
    # Pending — not retryable, would create a dup.
    assert retry_job(conn, job_id) is None
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    assert retry_job(conn, job_id) is None


def test_retry_job_returns_none_for_missing_id(tmp_path: Path) -> None:
    from ytdl.queue import retry_job

    conn = _setup(tmp_path)
    assert retry_job(conn, "01nonexistent") is None


def test_claim_one_does_not_record_started_event(tmp_path: Path) -> None:
    """claim_one is now a pure claim primitive. The supervisor records the
    'started' event so it can capture the event id and publish it on the bus
    as _event_id (needed for SSE id: lines on live frames)."""
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    claimed = claim_one(conn)
    assert claimed is not None
    started = conn.execute(
        "SELECT COUNT(*) AS n FROM events WHERE job_id=? AND kind='started'",
        (job_id,),
    ).fetchone()
    assert started["n"] == 0, "claim_one should not write the 'started' event"


def test_clear_done_jobs_only_deletes_old_done(tmp_path: Path) -> None:
    from ytdl.queue import clear_done_jobs, count_clearable
    conn = _setup(tmp_path)
    now = int(time.time() * 1000)
    # Old DONE — should be deleted.
    old_id = enqueue(conn, url="old", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (now - 30 * 86_400_000, old_id),
    )
    # Recent DONE — should stay.
    recent_id = enqueue(conn, url="recent", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (now - 1 * 86_400_000, recent_id),
    )
    # Old FAILED — should stay (we don't delete failed jobs).
    failed_id = enqueue(conn, url="failed", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute(
        "UPDATE jobs SET status='failed', finished_at=? WHERE id=?",
        (now - 30 * 86_400_000, failed_id),
    )

    threshold = 7 * 86_400_000
    assert count_clearable(conn, older_than_ms=threshold) == 1
    deleted = clear_done_jobs(conn, older_than_ms=threshold)
    assert deleted == 1
    remaining = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    assert old_id not in remaining
    assert recent_id in remaining
    assert failed_id in remaining


def test_clear_done_jobs_keeps_parent_with_live_children(tmp_path: Path) -> None:
    """A DONE parent that still has a PENDING child must not be deleted —
    deleting it would orphan the child (claim_one would skip the child)."""
    from ytdl.queue import clear_done_jobs, promote_to_playlist
    conn = _setup(tmp_path)
    now = int(time.time() * 1000)
    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    promote_to_playlist(conn, parent, title="P")
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (now - 30 * 86_400_000, parent),
    )
    # Live (pending) child.
    enqueue(
        conn, url="c", kind=JobKind.VIDEO, format_pref="best",
        output_dir="/o", parent_job_id=parent,
    )
    deleted = clear_done_jobs(conn, older_than_ms=7 * 86_400_000)
    assert deleted == 0
    remaining = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    assert parent in remaining


def test_clear_done_jobs_deletes_parent_when_all_children_are_old_done(tmp_path: Path) -> None:
    from ytdl.queue import clear_done_jobs, promote_to_playlist
    conn = _setup(tmp_path)
    now = int(time.time() * 1000)
    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    promote_to_playlist(conn, parent, title="P")
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (now - 30 * 86_400_000, parent),
    )
    child = enqueue(
        conn, url="c", kind=JobKind.VIDEO, format_pref="best",
        output_dir="/o", parent_job_id=parent,
    )
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (now - 30 * 86_400_000, child),
    )
    deleted = clear_done_jobs(conn, older_than_ms=7 * 86_400_000)
    # Both parent and child are stale DONE — both delete.
    assert deleted == 2


def test_clear_done_jobs_does_not_orphan_child_of_retained_parent(tmp_path: Path) -> None:
    """A stale DONE child must stay if its parent is being retained — otherwise
    `all_children_terminal()` sees an inconsistent child set."""
    from ytdl.queue import clear_done_jobs, promote_to_playlist
    conn = _setup(tmp_path)
    now = int(time.time() * 1000)

    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    promote_to_playlist(conn, parent, title="P")
    # Parent left RUNNING — definitely retained.
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (parent,))

    stale_done_child = enqueue(
        conn, url="c1", kind=JobKind.VIDEO, format_pref="best",
        output_dir="/o", parent_job_id=parent,
    )
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (now - 30 * 86_400_000, stale_done_child),
    )
    # Sibling still pending — makes the parent unambiguously retained.
    enqueue(
        conn, url="c2", kind=JobKind.VIDEO, format_pref="best",
        output_dir="/o", parent_job_id=parent,
    )

    deleted = clear_done_jobs(conn, older_than_ms=7 * 86_400_000)
    assert deleted == 0, "must not orphan a child of a retained parent"
    remaining = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    assert stale_done_child in remaining
    assert parent in remaining


def test_clear_done_jobs_keeps_children_of_non_done_parent(tmp_path: Path) -> None:
    """A child should not be deleted when its parent is itself non-deletable
    because it isn't DONE — even if all the children are old DONE rows.

    Regression: an interrupted worker can leave a parent stuck RUNNING or
    CANCELING. We must not sweep its DONE children out from under it; that
    leaves the parent with no children at all and breaks
    `all_children_terminal()`.
    """
    from ytdl.queue import clear_done_jobs, promote_to_playlist
    conn = _setup(tmp_path)
    now = int(time.time() * 1000)

    parent = enqueue(conn, url="p", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    promote_to_playlist(conn, parent, title="P")
    # Stuck RUNNING — not DONE, so not deletable.
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (parent,))

    # All children are stale DONE — would naively look "swept" since there's
    # no non-stale sibling, but the parent isn't going anywhere.
    for url in ("c1", "c2", "c3"):
        cid = enqueue(
            conn, url=url, kind=JobKind.VIDEO, format_pref="best",
            output_dir="/o", parent_job_id=parent,
        )
        conn.execute(
            "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
            (now - 30 * 86_400_000, cid),
        )

    deleted = clear_done_jobs(conn, older_than_ms=7 * 86_400_000)
    assert deleted == 0, "must not delete children when parent isn't going too"
    rows = conn.execute(
        "SELECT COUNT(*) AS n FROM jobs WHERE parent_job_id=?", (parent,)
    ).fetchone()
    assert rows["n"] == 3
