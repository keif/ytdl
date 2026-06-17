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
