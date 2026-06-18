from __future__ import annotations

from pathlib import Path

import pytest

from ytdl.db import connect, migrate
from ytdl.events_bus import EventsBus
from ytdl.models import JobKind, JobStatus
from ytdl.queue import (
    children_of,
    enqueue,
    get_job,
    promote_to_playlist,
)
from ytdl.workers import _sanitize_path_component


def test_promote_to_playlist_updates_kind_and_metadata(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    parent = enqueue(
        conn,
        url="u",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    promote_to_playlist(conn, parent, title="My Playlist")
    row = conn.execute(
        "SELECT kind, title FROM jobs WHERE id=?", (parent,)
    ).fetchone()
    assert row["kind"] == JobKind.PLAYLIST.value
    assert row["title"] == "My Playlist"


def test_children_of_returns_only_direct_children(tmp_path: Path) -> None:
    conn = connect(tmp_path / "t.db")
    migrate(conn)
    parent = enqueue(
        conn,
        url="p",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    c1 = enqueue(
        conn,
        url="a",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
        parent_job_id=parent,
    )
    c2 = enqueue(
        conn,
        url="b",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
        parent_job_id=parent,
    )
    enqueue(
        conn,
        url="x",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    ids = {j.id for j in children_of(conn, parent)}
    assert ids == {c1, c2}


@pytest.mark.asyncio
async def test_supervisor_expands_playlist_into_children(tmp_path: Path) -> None:
    from ytdl.workers import Supervisor

    conn = connect(tmp_path / "t.db")
    migrate(conn)
    parent_id = enqueue(
        conn,
        url="https://yt/playlist?list=PL",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )

    def fake_probe(url: str) -> dict:
        return {
            "_type": "playlist",
            "title": "My Playlist",
            "entries": [
                {"url": "https://yt/watch?v=a", "id": "a", "title": "A"},
                {"url": "https://yt/watch?v=b", "id": "b", "title": "B"},
            ],
        }

    def fake_download(job, ctx):
        from ytdl.downloader import DownloadResult

        return DownloadResult(
            output_path=f"{job.output_dir}/{job.id}.mp4",
            title="x",
            video_id="x",
            uploader=None,
            duration_s=None,
            filesize_bytes=None,
        )

    bus = EventsBus()
    sup = Supervisor(
        db_path=tmp_path / "t.db",
        workers=1,
        bus=bus,
        downloader=fake_download,
        probe=fake_probe,
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    await sup.start()
    await sup.wait_idle(timeout=3.0)
    await sup.stop()

    parent = get_job(conn, parent_id)
    assert parent is not None
    assert parent.kind == JobKind.PLAYLIST
    assert parent.status == JobStatus.DONE

    kids = children_of(conn, parent_id)
    assert len(kids) == 2
    assert all(k.status == JobStatus.DONE for k in kids)


def test_sanitize_strips_path_separators() -> None:
    assert _sanitize_path_component("creators/my list") == "creators_my list"
    assert _sanitize_path_component("a\\b") == "a_b"


def test_sanitize_rejects_dot_dot() -> None:
    assert _sanitize_path_component("..") == "Playlist"
    assert _sanitize_path_component(".") == "Playlist"
    assert _sanitize_path_component("") == "Playlist"


def test_sanitize_truncates_long_titles() -> None:
    long = "x" * 500
    assert _sanitize_path_component(long) == "x" * 200


def test_sanitize_strips_null_bytes() -> None:
    assert _sanitize_path_component("a\x00b") == "ab"


@pytest.mark.asyncio
async def test_empty_playlist_finishes_parent_immediately(tmp_path: Path) -> None:
    from ytdl.workers import Supervisor

    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    parent_id = enqueue(
        conn,
        url="https://yt/playlist?list=PL",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )

    bus = EventsBus()
    sup = Supervisor(
        db_path=db,
        workers=1,
        bus=bus,
        downloader=lambda job, ctx: None,  # never called for an empty playlist
        probe=lambda url: {"_type": "playlist", "title": "Empty", "entries": []},
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    await sup.start()
    await sup.wait_idle(timeout=2.0)
    await sup.stop()

    parent = get_job(conn, parent_id)
    assert parent is not None
    assert parent.status == JobStatus.DONE
    assert parent.error == "empty playlist"


@pytest.mark.asyncio
async def test_cancel_playlist_parent_during_download_marks_parent_canceled(
    tmp_path: Path,
) -> None:
    """End-to-end: cancel a playlist parent while children are downloading.

    All children abort and the parent ends up CANCELED (not DONE).
    """
    import asyncio
    import time

    from ytdl.queue import cancel_with_children
    from ytdl.workers import Supervisor

    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    parent_id = enqueue(
        conn,
        url="https://yt/playlist?list=PL",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    conn.close()

    def fake_probe(url: str) -> dict:
        return {
            "_type": "playlist",
            "title": "P",
            "entries": [
                {"url": "https://yt/a", "id": "a", "title": "A"},
                {"url": "https://yt/b", "id": "b", "title": "B"},
            ],
        }

    first_started = asyncio.Event()
    loop = asyncio.get_running_loop()

    def fake_download(job, ctx):
        # Block the worker so cancel can race in. The downloader's progress
        # hook polls the cancel flag in production; here we simulate by
        # busy-waiting until ctx.cancel_flag() returns True, then raise the
        # canonical DownloadCancelled.
        from ytdl.downloader import DownloadCancelled

        loop.call_soon_threadsafe(first_started.set)
        # Spin until canceled (with a 2s safety timeout).
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if ctx.cancel_flag():
                raise DownloadCancelled()
            time.sleep(0.02)
        raise RuntimeError("cancel was not observed within 2s")

    bus = EventsBus()
    sup = Supervisor(
        db_path=db,
        workers=2,
        bus=bus,
        downloader=fake_download,
        probe=fake_probe,
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    await sup.start()
    await asyncio.wait_for(first_started.wait(), timeout=3.0)

    # Cascade-cancel the parent (and its children) via the queue helper.
    conn = connect(db)
    cancel_with_children(conn, parent_id)
    # Nudge the supervisor's in-memory flags so spinning workers see the
    # cancel via the closure check (this avoids relying on the DB-poll path
    # inside _download_video's cancel_flag).
    for c in children_of(conn, parent_id):
        sup.request_cancel(c.id)
    sup.request_cancel(parent_id)
    conn.close()

    await sup.wait_idle(timeout=5.0)
    await sup.stop()

    conn = connect(db)
    parent = get_job(conn, parent_id)
    kids = children_of(conn, parent_id)
    conn.close()

    assert parent is not None
    assert parent.status == JobStatus.CANCELED, (
        f"parent should end CANCELED, got {parent.status}"
    )
    for c in kids:
        assert c.status == JobStatus.CANCELED, (
            f"child {c.id} should end CANCELED, got {c.status}"
        )


@pytest.mark.asyncio
async def test_cancel_during_probe_marks_new_children_canceled(tmp_path: Path) -> None:
    """If the user cancels while the parent is mid-probe, the children
    enqueued from the completed probe should land directly in CANCELED."""
    import asyncio

    from ytdl.queue import cancel_with_children
    from ytdl.workers import Supervisor

    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    parent_id = enqueue(
        conn,
        url="https://yt/playlist?list=PL",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    conn.close()

    probe_started = asyncio.Event()
    probe_unblock = asyncio.Event()
    loop = asyncio.get_running_loop()

    def fake_probe(url: str) -> dict:
        # Signal main coro that probe is running, then wait for the unblock.
        # asyncio.Event lives on the loop; cross-thread set via call_soon_threadsafe.
        loop.call_soon_threadsafe(probe_started.set)
        import time as _t

        deadline = _t.monotonic() + 3.0
        while not probe_unblock.is_set() and _t.monotonic() < deadline:
            _t.sleep(0.02)
        return {
            "_type": "playlist",
            "title": "P",
            "entries": [
                {"url": "https://yt/a", "id": "a", "title": "A"},
                {"url": "https://yt/b", "id": "b", "title": "B"},
            ],
        }

    bus = EventsBus()
    sup = Supervisor(
        db_path=db,
        workers=1,
        bus=bus,
        downloader=lambda job, ctx: (_ for _ in ()).throw(
            AssertionError("no download should run")
        ),
        probe=fake_probe,
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    await sup.start()
    await asyncio.wait_for(probe_started.wait(), timeout=3.0)

    # Probe is in flight; cancel the parent now.
    conn = connect(db)
    cancel_with_children(conn, parent_id)
    conn.close()

    # Let the probe complete; the worker should mark every child CANCELED.
    probe_unblock.set()
    await sup.wait_idle(timeout=3.0)
    await sup.stop()

    conn = connect(db)
    parent = get_job(conn, parent_id)
    kids = children_of(conn, parent_id)
    conn.close()
    assert parent is not None
    assert parent.status == JobStatus.CANCELED
    assert len(kids) == 2
    for c in kids:
        assert c.status == JobStatus.CANCELED, (
            f"child {c.id} should land CANCELED after late expansion, got {c.status}"
        )


@pytest.mark.asyncio
async def test_cancel_during_expansion_loop_marks_late_children_canceled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the user cancels AFTER probe returns but DURING the child-enqueue
    loop, every child (early and late) ends up CANCELED and the parent
    finalizes CANCELED — no PENDING children left in the queue.

    To make the mid-loop race deterministic, we monkeypatch ytdl.workers.enqueue
    so that after the first few children are enqueued we trigger the cancel
    from another connection. The worker keeps inserting children as PENDING
    (snapshot is stale) and the post-loop recheck must snap them all to
    CANCELED.
    """
    import threading

    import ytdl.workers as workers_mod
    from ytdl.queue import cancel_with_children
    from ytdl.workers import Supervisor

    db = tmp_path / "t.db"
    conn = connect(db)
    migrate(conn)
    parent_id = enqueue(
        conn,
        url="https://yt/playlist?list=PL",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    conn.close()

    def fake_probe(url: str) -> dict:
        return {
            "_type": "playlist",
            "title": "P",
            "entries": [
                {"url": f"https://yt/e{i}", "id": f"e{i}", "title": f"E{i}"}
                for i in range(20)
            ],
        }

    real_enqueue = workers_mod.enqueue
    enqueue_count = {"n": 0}
    cancel_fired = threading.Event()

    def spy_enqueue(*args, **kwargs):
        # After the third child is inserted, race the cancel in from a
        # fresh connection. Then keep enqueueing — the worker's pre-loop
        # snapshot of parent_canceled_pre is stale and the post-loop
        # recheck has to clean up.
        child_id = real_enqueue(*args, **kwargs)
        enqueue_count["n"] += 1
        if enqueue_count["n"] == 3 and not cancel_fired.is_set():
            cancel_conn = connect(db)
            try:
                cancel_with_children(cancel_conn, parent_id)
            finally:
                cancel_conn.close()
            cancel_fired.set()
        return child_id

    monkeypatch.setattr(workers_mod, "enqueue", spy_enqueue)

    bus = EventsBus()
    sup = Supervisor(
        db_path=db,
        workers=1,
        bus=bus,
        downloader=lambda job, ctx: (_ for _ in ()).throw(
            AssertionError("no download should run")
        ),
        probe=fake_probe,
        cookies_browser=None,
        retry_delays_s=(0, 0),
        rate_limit_delay_s=0,
    )
    await sup.start()
    await sup.wait_idle(timeout=3.0)
    await sup.stop()

    conn = connect(db)
    parent = get_job(conn, parent_id)
    kids = children_of(conn, parent_id)
    pending_count = sum(1 for c in kids if c.status == JobStatus.PENDING)
    canceled_count = sum(1 for c in kids if c.status == JobStatus.CANCELED)
    conn.close()

    assert cancel_fired.is_set(), "test setup bug: cancel never raced into the loop"
    assert parent is not None
    assert parent.status == JobStatus.CANCELED, (
        f"parent should end CANCELED, got {parent.status}"
    )
    assert pending_count == 0, (
        f"no children should be left PENDING; got {pending_count} pending / "
        f"{canceled_count} canceled / {len(kids)} total"
    )
    # All 20 children expected to be CANCELED (terminal).
    assert canceled_count == 20, (
        f"expected 20 canceled children, got {canceled_count}"
    )


def test_default_probe_adapter_forwards_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    """The supervisor's default probe path must pass the configured browser to yt-dlp."""
    captured: dict = {}

    def fake_probe(url: str, *, cookies_browser: str | None = None) -> dict:
        captured["url"] = url
        captured["cookies_browser"] = cookies_browser
        return {"_type": "video"}

    import ytdl.downloader

    monkeypatch.setattr(ytdl.downloader, "probe", fake_probe)

    from ytdl.workers import _default_probe_adapter

    _default_probe_adapter("https://yt/x", cookies_browser="chrome")
    assert captured == {"url": "https://yt/x", "cookies_browser": "chrome"}
