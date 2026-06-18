from __future__ import annotations

import json
import threading
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


def test_finish_if_status_cas_misses_when_status_differs(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute("UPDATE jobs SET status='canceling' WHERE id=?", (job_id,))
    ok = finish_if_status(
        conn,
        job_id,
        expected_status=JobStatus.RUNNING,
        new_status=JobStatus.DONE,
    )
    assert ok is False
    row = conn.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
    assert row["status"] == JobStatus.CANCELING.value


def test_finish_if_status_cas_hits_when_status_matches(tmp_path: Path) -> None:
    conn = _setup(tmp_path)
    job_id = enqueue(conn, url="u", kind=JobKind.VIDEO, format_pref="best", output_dir="/o")
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    ok = finish_if_status(
        conn,
        job_id,
        expected_status=JobStatus.RUNNING,
        new_status=JobStatus.DONE,
        output_path="/out/x.mp4",
    )
    assert ok is True
    row = conn.execute(
        "SELECT status, output_path FROM jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert row["status"] == JobStatus.DONE.value
    assert row["output_path"] == "/out/x.mp4"


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
