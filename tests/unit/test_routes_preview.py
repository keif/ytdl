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
        workers=0,
        cookies_browser=None,
        default_format="best",
    )
    app = build_app(cfg)
    return TestClient(app)


def _patch_probe(monkeypatch: pytest.MonkeyPatch, info: dict) -> None:
    """Replace ytdl.api.routes_preview.probe so tests don't hit yt-dlp."""
    monkeypatch.setattr(
        "ytdl.api.routes_preview.probe", lambda url, cookies_browser=None: info
    )


def _patch_probe_one(
    monkeypatch: pytest.MonkeyPatch, by_url: dict[str, dict]
) -> None:
    monkeypatch.setattr(
        "ytdl.api.routes_preview.probe_one",
        lambda url, cookies_browser=None: by_url[url],
    )


def test_preview_returns_video_kind_for_single(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probe(
        monkeypatch,
        {
            "_type": "video",
            "title": "Just one",
            "id": "abc",
            "webpage_url": "https://yt.example/v/abc",
        },
    )
    r = client.post("/preview", json={"url": "https://yt.example/v/abc"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "video"
    assert body["title"] == "Just one"
    assert len(body["entries"]) == 1
    assert body["entries"][0]["url"] == "https://yt.example/v/abc"


def test_preview_returns_playlist_entries_in_order(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probe(
        monkeypatch,
        {
            "_type": "playlist",
            "title": "Mix",
            "entries": [
                {
                    "id": "a",
                    "title": "Track A",
                    "webpage_url": "https://yt.example/v/a",
                },
                {
                    "id": "b",
                    "title": "Track B",
                    "webpage_url": "https://yt.example/v/b",
                    "playlist_index": 2,
                },
                {
                    "id": "c",
                    "title": "Track C",
                    "webpage_url": "https://yt.example/v/c",
                },
            ],
        },
    )
    r = client.post("/preview", json={"url": "https://yt.example/list?p=PL"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "playlist"
    assert body["title"] == "Mix"
    titles = [e["title"] for e in body["entries"]]
    assert titles == ["Track A", "Track B", "Track C"]
    positions = [e["position"] for e in body["entries"]]
    # First and third fall back to index+1, second uses playlist_index.
    assert positions == [1, 2, 3]


def test_preview_drops_entries_without_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probe(
        monkeypatch,
        {
            "_type": "playlist",
            "title": "Has holes",
            "entries": [
                {"id": "a", "title": "ok", "webpage_url": "https://x/a"},
                {"id": "b", "title": "no url"},
                None,
                "junk",
            ],
        },
    )
    r = client.post("/preview", json={"url": "https://x/list"})
    assert r.status_code == 200
    assert len(r.json()["entries"]) == 1


def test_preview_rejects_non_http(client: TestClient) -> None:
    r = client.post("/preview", json={"url": "javascript:alert(1)"})
    assert r.status_code == 422


def test_preview_propagates_probe_failure_as_400(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(url: str, cookies_browser: str | None = None) -> dict:
        raise RuntimeError("nope")

    monkeypatch.setattr("ytdl.api.routes_preview.probe", boom)
    r = client.post("/preview", json={"url": "https://x/whatever"})
    assert r.status_code == 400
    assert "probe failed" in r.json()["detail"]


def test_enrich_returns_metadata_per_url(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_probe_one(
        monkeypatch,
        {
            "https://x/1": {
                "title": "One",
                "duration": 65.7,
                "uploader": "Channel One",
                "thumbnail": "https://x/1.jpg",
            },
            "https://x/2": {
                "title": "Two",
                "duration": 30,
                "channel": "Channel Two",
                "thumbnail": "https://x/2.jpg",
            },
        },
    )
    r = client.post(
        "/preview/enrich", json={"urls": ["https://x/1", "https://x/2"]}
    )
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert len(entries) == 2
    by_url = {e["url"]: e for e in entries}
    assert by_url["https://x/1"]["title"] == "One"
    assert by_url["https://x/1"]["duration_s"] == 65
    assert by_url["https://x/1"]["uploader"] == "Channel One"
    # Falls back to 'channel' when 'uploader' is missing.
    assert by_url["https://x/2"]["uploader"] == "Channel Two"


def test_enrich_individual_failures_surface_in_response(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_probe_one(url: str, cookies_browser: str | None = None) -> dict:
        if url == "https://x/bad":
            raise RuntimeError("video unavailable")
        return {"title": "ok", "duration": 10, "uploader": "u", "thumbnail": "t"}

    monkeypatch.setattr("ytdl.api.routes_preview.probe_one", fake_probe_one)
    r = client.post(
        "/preview/enrich",
        json={"urls": ["https://x/good", "https://x/bad"]},
    )
    assert r.status_code == 200
    entries = {e["url"]: e for e in r.json()["entries"]}
    assert entries["https://x/good"]["error"] is None
    assert entries["https://x/bad"]["error"] == "video unavailable"
    assert entries["https://x/bad"]["title"] is None


def test_enrich_rejects_empty_list(client: TestClient) -> None:
    r = client.post("/preview/enrich", json={"urls": []})
    assert r.status_code == 422


def test_enrich_rejects_oversize_batch(client: TestClient) -> None:
    urls = [f"https://x/{i}" for i in range(50)]
    r = client.post("/preview/enrich", json={"urls": urls})
    assert r.status_code == 422


def test_enrich_rejects_non_http_member(client: TestClient) -> None:
    r = client.post(
        "/preview/enrich",
        json={"urls": ["https://x/ok", "javascript:alert(1)"]},
    )
    assert r.status_code == 422
