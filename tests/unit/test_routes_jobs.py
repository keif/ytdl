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
