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
