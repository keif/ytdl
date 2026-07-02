from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ytdl.api import build_app
from ytdl.config import Config


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,  # disable supervisor for unit-level API tests
        cookies_browser=None,
        default_format="best",
    )
    app = build_app(cfg)
    return TestClient(app)


def test_post_jobs_accepts_https_url(client: TestClient) -> None:
    r = client.post("/jobs", json={"url": "https://youtu.be/abc"})
    assert r.status_code == 201
    body = r.json()
    assert "id" in body
    assert body["status"] == "pending"


def test_post_jobs_rejects_javascript_scheme(client: TestClient) -> None:
    r = client.post("/jobs", json={"url": "javascript:alert(1)"})
    assert r.status_code == 422


def test_post_jobs_rejects_file_scheme(client: TestClient) -> None:
    r = client.post("/jobs", json={"url": "file:///etc/passwd"})
    assert r.status_code == 422


def test_post_jobs_rejects_empty_url(client: TestClient) -> None:
    r = client.post("/jobs", json={"url": ""})
    assert r.status_code == 422


def test_post_jobs_rejects_oversize_url(client: TestClient) -> None:
    big = "https://example.com/" + ("a" * 5000)
    r = client.post("/jobs", json={"url": big})
    assert r.status_code == 422


def test_get_jobs_lists_in_creation_order_desc(client: TestClient) -> None:
    ids = []
    for url in ("https://a.com/1", "https://a.com/2", "https://a.com/3"):
        ids.append(client.post("/jobs", json={"url": url}).json()["id"])
    r = client.get("/jobs")
    assert r.status_code == 200
    listed = [j["id"] for j in r.json()["jobs"]]
    assert listed == list(reversed(ids))


def test_get_job_by_id(client: TestClient) -> None:
    job_id = client.post("/jobs", json={"url": "https://a.com/1"}).json()["id"]
    r = client.get(f"/jobs/{job_id}")
    assert r.status_code == 200
    assert r.json()["id"] == job_id


def test_get_job_unknown_returns_404(client: TestClient) -> None:
    r = client.get("/jobs/no-such-id")
    assert r.status_code == 404


def test_delete_job_cancels(client: TestClient) -> None:
    job_id = client.post("/jobs", json={"url": "https://a.com/1"}).json()["id"]
    r = client.delete(f"/jobs/{job_id}")
    assert r.status_code == 204
    after = client.get(f"/jobs/{job_id}").json()
    assert after["status"] == "canceled"


def test_delete_playlist_cascades_to_children(client: TestClient) -> None:
    """DELETE on a playlist parent flips its children's statuses too."""
    from ytdl.db import connect
    from ytdl.models import JobKind
    from ytdl.queue import enqueue, promote_to_playlist

    parent_resp = client.post(
        "/jobs", json={"url": "https://yt.com/playlist?list=PL"}
    ).json()
    parent_id = parent_resp["id"]

    # Promote to playlist + add children directly via the queue (no worker).
    db_path = client.app.state.config.db_path
    conn = connect(db_path)
    promote_to_playlist(conn, parent_id, title="My Playlist")
    child1 = enqueue(
        conn,
        url="https://yt.com/c1",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent_id,
    )
    child2 = enqueue(
        conn,
        url="https://yt.com/c2",
        kind=JobKind.VIDEO,
        format_pref="best",
        output_dir="/o",
        parent_job_id=parent_id,
    )
    # Mark one child as running so we exercise both branches.
    conn.execute("UPDATE jobs SET status='running' WHERE id=?", (child1,))
    conn.close()

    r = client.delete(f"/jobs/{parent_id}")
    assert r.status_code == 204

    # Was running -> canceling.
    body = client.get(f"/jobs/{child1}").json()
    assert body["status"] == "canceling"
    # Was pending -> canceled directly.
    body = client.get(f"/jobs/{child2}").json()
    assert body["status"] == "canceled"


def test_post_jobs_xss_in_url_stored_as_text(client: TestClient) -> None:
    # We don't render URLs as HTML server-side, but verify it doesn't crash insertion.
    payload_url = "https://example.com/<script>alert(1)</script>"
    r = client.post("/jobs", json={"url": payload_url})
    assert r.status_code == 201


def test_post_jobs_sql_injection_attempt_does_not_execute(client: TestClient) -> None:
    sneaky = "https://example.com/'; DROP TABLE jobs;--"
    r = client.post("/jobs", json={"url": sneaky})
    assert r.status_code == 201
    # Still able to list — table not dropped.
    listed = client.get("/jobs").json()["jobs"]
    assert any(j["url"] == sneaky for j in listed)


def test_get_jobs_with_unknown_status_returns_422(client: TestClient) -> None:
    r = client.get("/jobs?status=garbage")
    assert r.status_code == 422
    assert "unknown status" in r.json()["detail"]


def test_get_jobs_with_valid_status_filter_works(client: TestClient) -> None:
    client.post("/jobs", json={"url": "https://a.com/1"})
    r = client.get("/jobs?status=pending")
    assert r.status_code == 200
    assert len(r.json()["jobs"]) == 1


def test_post_jobs_with_urls_array_enqueues_each(client: TestClient) -> None:
    urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    r = client.post("/jobs", json={"urls": urls})
    assert r.status_code == 201
    # Response is the first job (matches the single-url shape).
    first = r.json()
    assert first["url"] == "https://a.com/1"
    # All three are in the queue.
    listed = client.get("/jobs").json()["jobs"]
    in_queue = [j["url"] for j in listed if j["url"].startswith("https://a.com/")]
    assert set(in_queue) == set(urls)


def test_post_jobs_rejects_both_url_and_urls(client: TestClient) -> None:
    r = client.post(
        "/jobs",
        json={"url": "https://a.com/1", "urls": ["https://a.com/2"]},
    )
    assert r.status_code == 422


def test_post_jobs_rejects_neither_url_nor_urls(client: TestClient) -> None:
    r = client.post("/jobs", json={"format_pref": "best"})
    assert r.status_code == 422


def test_post_jobs_rejects_non_http_url_in_array(client: TestClient) -> None:
    r = client.post(
        "/jobs",
        json={"urls": ["https://a.com/1", "javascript:alert(1)"]},
    )
    assert r.status_code == 422


def test_post_jobs_urls_array_uses_default_format(client: TestClient) -> None:
    r = client.post("/jobs", json={"urls": ["https://a.com/1"]})
    assert r.status_code == 201
    assert r.json()["format_pref"] == "best"


def test_post_jobs_urls_array_honors_format_pref(client: TestClient) -> None:
    r = client.post(
        "/jobs",
        json={"urls": ["https://a.com/1", "https://a.com/2"], "format_pref": "720p"},
    )
    assert r.status_code == 201
    listed = client.get("/jobs").json()["jobs"]
    formats = {j["format_pref"] for j in listed if j["url"].startswith("https://a.com/")}
    assert formats == {"720p"}


def test_retry_endpoint_creates_new_job(client: TestClient) -> None:
    job_id = client.post("/jobs", json={"url": "https://yt/x"}).json()["id"]
    # Mark failed via DB.
    from ytdl.db import connect

    db = client.app.state.config.db_path
    conn = connect(db)
    conn.execute(
        "UPDATE jobs SET status='failed', error='boom' WHERE id=?", (job_id,)
    )
    conn.commit()
    conn.close()
    r = client.post(f"/jobs/{job_id}/retry")
    assert r.status_code == 201
    new = r.json()
    assert new["id"] != job_id
    assert new["status"] == "pending"
    assert new["url"] == "https://yt/x"


def test_retry_endpoint_rejects_pending_job(client: TestClient) -> None:
    job_id = client.post("/jobs", json={"url": "https://yt/x"}).json()["id"]
    r = client.post(f"/jobs/{job_id}/retry")
    assert r.status_code == 400


def test_retry_endpoint_returns_400_for_unknown_id(client: TestClient) -> None:
    r = client.post("/jobs/01nonexistent/retry")
    assert r.status_code == 400


def test_redownload_endpoint_creates_force_overwrite_clone(client: TestClient) -> None:
    from ytdl.db import connect

    db = client.app.state.config.db_path
    job_id = client.post("/jobs", json={"url": "https://yt/x"}).json()["id"]
    conn = connect(db)
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?", (1, job_id)
    )
    conn.commit()
    conn.close()
    r = client.post(f"/jobs/{job_id}/redownload")
    assert r.status_code == 201
    body = r.json()
    assert body["id"] != job_id
    assert body["status"] == "pending"
    assert body["force_overwrite"] is True


def test_redownload_endpoint_rejects_pending(client: TestClient) -> None:
    job_id = client.post("/jobs", json={"url": "https://yt/x"}).json()["id"]
    r = client.post(f"/jobs/{job_id}/redownload")
    assert r.status_code == 400


def test_redownload_endpoint_returns_400_for_unknown_id(client: TestClient) -> None:
    r = client.post("/jobs/01nonexistent/redownload")
    assert r.status_code == 400


def test_retry_endpoint_clone_keeps_force_overwrite_false(client: TestClient) -> None:
    """The plain retry endpoint does NOT set force_overwrite — that's what
    differentiates it from redownload."""
    from ytdl.db import connect

    db = client.app.state.config.db_path
    job_id = client.post("/jobs", json={"url": "https://yt/x"}).json()["id"]
    conn = connect(db)
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?", (1, job_id)
    )
    conn.commit()
    conn.close()
    r = client.post(f"/jobs/{job_id}/retry")
    assert r.status_code == 201
    assert r.json()["force_overwrite"] is False


def test_static_ui_served_when_present(tmp_path: Path) -> None:
    # Stage a fake built UI in the package's `web/` dir.
    import ytdl.api as api_pkg

    web_dir = Path(api_pkg.__file__).parent.parent / "web"
    web_dir.mkdir(parents=True, exist_ok=True)
    (web_dir / "index.html").write_text("<html>ytdl ui</html>")
    try:
        cfg = Config(
            output_dir=tmp_path / "out",
            db_path=tmp_path / "ytdl.db",
            workers=0,
            cookies_browser=None,
            default_format="best",
        )
        c = TestClient(build_app(cfg))
        r = c.get("/")
        assert r.status_code == 200
        assert "ytdl ui" in r.text
    finally:
        (web_dir / "index.html").unlink(missing_ok=True)
        # leave the dir; .gitignore excludes it


def test_clear_preview_returns_zero_for_fresh_db(client: TestClient) -> None:
    r = client.get("/jobs/clear/preview")
    assert r.status_code == 200
    assert r.json() == {"clearable": 0, "older_than_days": 7}


def test_clear_endpoint_deletes_old_done(client: TestClient) -> None:
    import time as _t

    from ytdl.db import connect
    db = client.app.state.config.db_path
    job_id = client.post("/jobs", json={"url": "https://yt/x"}).json()["id"]
    conn = connect(db)
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (int(_t.time() * 1000) - 30 * 86_400_000, job_id),
    )
    conn.commit()
    conn.close()
    r = client.post("/jobs/clear")
    assert r.status_code == 200
    assert r.json()["deleted"] == 1


def test_clear_endpoint_rejects_negative_days(client: TestClient) -> None:
    r = client.post("/jobs/clear?older_than_days=-1")
    assert r.status_code == 422


def test_post_jobs_with_subtitles_true_persists_flag(client: TestClient) -> None:
    r = client.post(
        "/jobs", json={"url": "https://yt/x", "subtitles": True}
    )
    assert r.status_code == 201
    body = r.json()
    assert body["subtitles"] is True


def test_post_jobs_without_subtitles_uses_config_default(tmp_path: Path) -> None:
    """When the payload omits `subtitles`, the row should pick up the
    config's `subtitles_default`."""
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
        subtitles_default=True,
    )
    c = TestClient(build_app(cfg))
    r = c.post("/jobs", json={"url": "https://yt/x"})
    assert r.status_code == 201
    assert r.json()["subtitles"] is True


def test_post_jobs_explicit_false_overrides_config_default(tmp_path: Path) -> None:
    """Explicit `subtitles: false` on a single POST must opt out even when
    the server default is true."""
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
        subtitles_default=True,
    )
    c = TestClient(build_app(cfg))
    r = c.post("/jobs", json={"url": "https://yt/x", "subtitles": False})
    assert r.status_code == 201
    assert r.json()["subtitles"] is False


def test_post_jobs_urls_array_applies_subtitles_to_every_child(
    client: TestClient,
) -> None:
    urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    r = client.post("/jobs", json={"urls": urls, "subtitles": True})
    assert r.status_code == 201
    listed = client.get("/jobs").json()["jobs"]
    flagged = [
        j for j in listed if j["url"].startswith("https://a.com/")
    ]
    assert len(flagged) == 3
    assert all(j["subtitles"] is True for j in flagged)


def test_post_jobs_with_writable_output_dir_persists_path(
    client: TestClient, tmp_path: Path
) -> None:
    """An override pointing at an existing writable directory is accepted
    and stored on the job row."""
    target = tmp_path / "music"
    target.mkdir()
    r = client.post(
        "/jobs",
        json={"url": "https://yt/x", "output_dir": str(target)},
    )
    assert r.status_code == 201
    assert r.json()["output_dir"] == str(target)


def test_post_jobs_with_nonexistent_parent_rejected(
    client: TestClient,
) -> None:
    """A path whose parent doesn't exist can't be created at worker time —
    400 instead of failing later."""
    r = client.post(
        "/jobs",
        json={
            "url": "https://yt/x",
            "output_dir": "/nonexistent-root-aaa/foo/bar",
        },
    )
    assert r.status_code == 400
    assert "output_dir" in r.json()["detail"]


def test_post_jobs_with_file_path_rejected(
    client: TestClient, tmp_path: Path
) -> None:
    """A path pointing at a file (not a directory) is a configuration
    error — must be 400."""
    f = tmp_path / "not_a_dir.txt"
    f.write_text("hello")
    r = client.post(
        "/jobs",
        json={"url": "https://yt/x", "output_dir": str(f)},
    )
    assert r.status_code == 400


def test_post_jobs_without_output_dir_falls_back_to_config(
    client: TestClient,
) -> None:
    """Omitting output_dir uses cfg.output_dir — preserves existing
    behavior for clients that don't know about the new field."""
    r = client.post("/jobs", json={"url": "https://yt/x"})
    assert r.status_code == 201
    cfg_dir = str(client.app.state.config.output_dir)
    assert r.json()["output_dir"] == cfg_dir


def test_post_jobs_urls_array_applies_output_dir_to_every_child(
    client: TestClient, tmp_path: Path
) -> None:
    """A per-batch output_dir override covers every child job in the
    urls[] branch."""
    target = tmp_path / "playlist_out"
    target.mkdir()
    urls = ["https://a.com/1", "https://a.com/2", "https://a.com/3"]
    r = client.post(
        "/jobs",
        json={"urls": urls, "output_dir": str(target)},
    )
    assert r.status_code == 201
    listed = client.get("/jobs").json()["jobs"]
    children = [j for j in listed if j["url"].startswith("https://a.com/")]
    assert len(children) == 3
    assert all(j["output_dir"] == str(target) for j in children)


def test_post_jobs_output_dir_expands_tilde(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``~/subdir`` must be expanded against the user's HOME so the UI can
    accept the same shorthand a shell would."""
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "Downloads" / "ytdl"
    target.mkdir(parents=True)
    r = client.post(
        "/jobs",
        json={"url": "https://yt/x", "output_dir": "~/Downloads/ytdl"},
    )
    assert r.status_code == 201
    assert r.json()["output_dir"] == str(target)


def test_post_jobs_output_dir_unresolvable_tilde_returns_400(
    client: TestClient,
) -> None:
    """``~nosuchuser/...`` raises RuntimeError from expanduser; the API must
    convert that to a 400, not let it surface as a 500."""
    r = client.post(
        "/jobs",
        json={"url": "https://yt/x", "output_dir": "~nosuchuser_xyz_12345/foo"},
    )
    assert r.status_code == 400


# --- duplicate detection ---


def _seed_library_row(
    db_path: Path, video_id: str, path: str, title: str | None = None
) -> None:
    from ytdl.db import connect, migrate
    from ytdl.library import record_downloaded

    conn = connect(db_path)
    try:
        migrate(conn)
        record_downloaded(conn, video_id, path, title, None)
    finally:
        conn.close()


def test_post_jobs_returns_409_when_url_video_id_indexed(
    client: TestClient,
) -> None:
    """POST /jobs with a URL whose video_id lives in library_files must
    reject with 409 so the client can render "already downloaded" without
    silently enqueueing a duplicate download."""
    db = client.app.state.config.db_path
    _seed_library_row(
        db, "abc12345678", "/data/out/Foo [abc12345678].mp4", "Foo"
    )
    r = client.post(
        "/jobs", json={"url": "https://youtu.be/abc12345678"}
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "duplicate"
    assert detail["path"] == "/data/out/Foo [abc12345678].mp4"
    assert "force_overwrite" in detail["hint"]


def test_post_jobs_force_overwrite_bypasses_duplicate_check(
    client: TestClient,
) -> None:
    """Setting force_overwrite=true is the escape hatch — the check is
    skipped, the job is enqueued, and the flag is persisted on the row so
    the worker instructs yt-dlp to overwrite the existing file."""
    db = client.app.state.config.db_path
    _seed_library_row(
        db, "abc12345678", "/data/out/Foo [abc12345678].mp4", "Foo"
    )
    r = client.post(
        "/jobs",
        json={
            "url": "https://youtu.be/abc12345678",
            "force_overwrite": True,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["force_overwrite"] is True
    assert body["status"] == "pending"


def test_post_jobs_urls_array_rejects_when_any_child_is_duplicate(
    client: TestClient,
) -> None:
    """The urls[] branch must fail-fast on the first indexed video_id so
    the batch stays atomic — a partial enqueue would leave the client
    thinking the whole submit failed."""
    db = client.app.state.config.db_path
    _seed_library_row(
        db, "abc12345678", "/data/out/Foo [abc12345678].mp4", None
    )
    r = client.post(
        "/jobs",
        json={
            "urls": [
                "https://youtu.be/newnew11111",  # not indexed
                "https://youtu.be/abc12345678",  # indexed — trips 409
            ]
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "duplicate"
    # Verify no rows leaked in.
    listed = client.get("/jobs").json()["jobs"]
    assert not any(
        j["url"].startswith("https://youtu.be/") for j in listed
    )


def test_post_jobs_urls_array_force_overwrite_bypasses(
    client: TestClient,
) -> None:
    """force_overwrite=true propagates to every enqueued child in the
    urls[] batch, so the whole selection re-downloads even when several
    URLs are duplicates."""
    db = client.app.state.config.db_path
    _seed_library_row(db, "abc12345678", "/data/out/A.mp4", None)
    _seed_library_row(db, "def12345678", "/data/out/B.mp4", None)
    r = client.post(
        "/jobs",
        json={
            "urls": [
                "https://youtu.be/abc12345678",
                "https://youtu.be/def12345678",
            ],
            "force_overwrite": True,
        },
    )
    assert r.status_code == 201
    listed = client.get("/jobs").json()["jobs"]
    children = [j for j in listed if j["url"].startswith("https://youtu.be/")]
    assert len(children) == 2
    assert all(j["force_overwrite"] is True for j in children)


def test_post_jobs_unrecognized_url_shape_skips_dedup_check(
    client: TestClient,
) -> None:
    """URLs that don't match a canonical YouTube shape (extract_video_id_
    from_url returns None) fall through to the enqueue. Better to let the
    probe path catch the duplicate at run time than block a valid non-YT
    URL because we can't parse an id locally."""
    db = client.app.state.config.db_path
    _seed_library_row(db, "abc12345678", "/data/out/x.mp4", None)
    # No id parseable from example.com; the seeded row is irrelevant here.
    r = client.post(
        "/jobs", json={"url": "https://example.com/some-video"}
    )
    assert r.status_code == 201


def test_post_jobs_playlist_url_skips_single_video_dedup_check(
    client: TestClient,
) -> None:
    """A URL with `list=PL...` targets a playlist. The pre-enqueue check
    extracts the anchor `v=` id, but the worker will expand the URL into
    all playlist entries — blocking on the anchor being a duplicate
    would prevent queueing the whole playlist. Codex-caught: the picker
    flow already handles per-entry dedup at the UI level; direct submit
    of a playlist URL should skip the single-video check entirely."""
    db = client.app.state.config.db_path
    # Seed the anchor video as an existing duplicate.
    _seed_library_row(db, "abcVIDEO1234", "/data/out/anchor.mp4", None)

    # The playlist URL uses that anchor as `?v=` but the intent is the
    # whole playlist. Must accept (201), NOT reject (409).
    r = client.post(
        "/jobs",
        json={"url": "https://www.youtube.com/watch?v=abcVIDEO1234&list=PLxyz"},
    )
    assert r.status_code == 201, (
        f"expected 201 (playlist enqueue proceeds), got {r.status_code}: {r.text}"
    )


def test_post_jobs_urls_batch_checks_dedup_even_when_url_has_list_param(
    client: TestClient,
) -> None:
    """The urls[] branch is the picker submitting a chosen subset. Each
    URL is a picked video, even if it carries `&list=...` params from
    the address bar. Codex-caught: the earlier fix skipped playlist-
    shaped URLs entirely, which meant a duplicate picked entry with
    `list=...` slipped through. The single-URL branch still skips
    playlist shapes (that's the top-level playlist submit case)."""
    db = client.app.state.config.db_path
    _seed_library_row(db, "pickedDUP12", "/data/out/existing.mp4", None)

    # Picker-style submit — urls[] batch. The picked video happens to
    # carry a list= param. Must return 409, NOT 201.
    r = client.post(
        "/jobs",
        json={
            "urls": [
                "https://www.youtube.com/watch?v=pickedDUP12&list=PLxyz"
            ],
        },
    )
    assert r.status_code == 409, (
        f"expected 409 (picked video is a duplicate), "
        f"got {r.status_code}: {r.text}"
    )


def test_post_jobs_duplicate_check_disabled_when_dedup_off(
    tmp_path: Path,
) -> None:
    """When cfg.dedup_enabled=False, the 409 path is bypassed entirely so
    an operator who wants to disable the feature globally can, without
    having to empty library_scan_dirs."""
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
        dedup_enabled=False,
    )
    c = TestClient(build_app(cfg))
    _seed_library_row(
        cfg.db_path, "abc12345678", "/data/out/A.mp4", None
    )
    r = c.post("/jobs", json={"url": "https://youtu.be/abc12345678"})
    assert r.status_code == 201
