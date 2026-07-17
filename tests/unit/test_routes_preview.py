from __future__ import annotations

import asyncio
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


@pytest.fixture()
def fast_timeout_client(tmp_path: Path) -> TestClient:
    """Same app but with probe_timeout_s=1 so timeout tests resolve quickly.

    The route wraps to_thread in wait_for(probe_timeout_s + 5), so a 1s probe
    timeout means the test waits at most ~6s before the 504 fires.
    """
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
        probe_timeout_s=1,
    )
    app = build_app(cfg)
    return TestClient(app)


def _patch_probe(monkeypatch: pytest.MonkeyPatch, info: dict) -> None:
    """Replace ytdl.api.routes_preview.probe so tests don't hit yt-dlp."""
    monkeypatch.setattr(
        "ytdl.api.routes_preview.probe",
        lambda url, **kwargs: info,
    )


def _patch_probe_one(
    monkeypatch: pytest.MonkeyPatch, by_url: dict[str, dict]
) -> None:
    monkeypatch.setattr(
        "ytdl.api.routes_preview.probe_one",
        lambda url, **kwargs: by_url[url],
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
    def boom(url: str, **kwargs) -> dict:
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
    def fake_probe_one(url: str, **kwargs) -> dict:
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


# --- timeout behavior ---


def _patch_to_thread_to_hang(monkeypatch: pytest.MonkeyPatch, sleep_for: float) -> None:
    """Replace routes_preview.asyncio.to_thread so the route's wait_for fires.

    The real to_thread would actually park a thread for `sleep_for` seconds;
    we substitute a coroutine that just awaits asyncio.sleep, which is what
    wait_for cancels. Net effect on the route under test is identical to a
    truly wedged probe, but the test completes in milliseconds.
    """

    async def _slow_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        await asyncio.sleep(sleep_for)
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "ytdl.api.routes_preview.asyncio.to_thread", _slow_to_thread
    )


def test_preview_returns_504_when_probe_times_out(
    fast_timeout_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe that never returns must surface as a 504 with a clear message.

    The route wraps to_thread in wait_for(probe_timeout_s + 5). With
    probe_timeout_s=1, hang the probe for ~30s and verify wait_for fires
    quickly (well before the artificial sleep) and the route reports 504.
    """
    _patch_to_thread_to_hang(monkeypatch, sleep_for=30.0)
    # The probe target is never reached because to_thread is patched, but
    # we still need a callable in the symbol table for the route's lambda.
    _patch_probe(monkeypatch, {"_type": "video"})

    r = fast_timeout_client.post(
        "/preview", json={"url": "https://yt.example/v/stuck"}
    )
    assert r.status_code == 504
    detail = r.json()["detail"]
    assert "probe timed out" in detail
    assert "cookies" in detail


def test_preview_succeeds_when_probe_returns_quickly(
    fast_timeout_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Happy path on the same fast_timeout_client fixture — confirms the
    wait_for wrapper doesn't break normal returns."""
    _patch_probe(
        monkeypatch,
        {
            "_type": "video",
            "title": "Fast",
            "id": "ok",
            "webpage_url": "https://yt.example/v/ok",
        },
    )
    r = fast_timeout_client.post(
        "/preview", json={"url": "https://yt.example/v/ok"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "video"
    assert body["title"] == "Fast"


# --- duplicate detection ---


def _seed_library(db_path: Path, video_id: str, path: str, title: str | None = None) -> None:
    """Insert one row into the library_files index — simulates a rescan
    having already found this video on disk."""
    from ytdl.db import connect, migrate
    from ytdl.library import record_downloaded

    conn = connect(db_path)
    try:
        migrate(conn)
        record_downloaded(conn, video_id, path, title, None)
    finally:
        conn.close()


def test_preview_marks_entry_already_downloaded_when_indexed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the previewed video's id is in library_files, the entry's
    already_downloaded field carries the path + title so the UI can render
    the warning banner."""
    _seed_library(
        tmp_path / "ytdl.db",
        "abc12345678",
        "/data/out/Foo [abc12345678].mp4",
        "Foo",
    )
    _patch_probe(
        monkeypatch,
        {
            "_type": "video",
            "title": "Foo",
            "id": "abc12345678",
            "webpage_url": "https://youtu.be/abc12345678",
        },
    )
    r = client.post(
        "/preview", json={"url": "https://youtu.be/abc12345678"}
    )
    assert r.status_code == 200
    entry = r.json()["entries"][0]
    assert entry["already_downloaded"] is not None
    assert entry["already_downloaded"]["path"] == "/data/out/Foo [abc12345678].mp4"
    assert entry["already_downloaded"]["title"] == "Foo"


def test_preview_omits_already_downloaded_when_not_indexed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No library row => the field is None (present in the schema, but
    null so the UI's `entry.already_downloaded` check is falsy)."""
    _patch_probe(
        monkeypatch,
        {
            "_type": "video",
            "title": "Fresh",
            "id": "zzz99999999",
            "webpage_url": "https://youtu.be/zzz99999999",
        },
    )
    r = client.post(
        "/preview", json={"url": "https://youtu.be/zzz99999999"}
    )
    entry = r.json()["entries"][0]
    assert entry["already_downloaded"] is None


def test_preview_playlist_marks_only_duplicate_entries(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A playlist preview annotates each duplicate entry independently —
    entries not in the library keep already_downloaded=None."""
    _seed_library(
        tmp_path / "ytdl.db",
        "bbb22222222",
        "/data/out/Beta [bbb22222222].mp4",
        "Beta",
    )
    _patch_probe(
        monkeypatch,
        {
            "_type": "playlist",
            "title": "Mix",
            "entries": [
                {
                    "id": "aaa11111111",
                    "title": "Alpha",
                    "webpage_url": "https://yt.example/v/aaa11111111",
                },
                {
                    "id": "bbb22222222",
                    "title": "Beta",
                    "webpage_url": "https://yt.example/v/bbb22222222",
                },
            ],
        },
    )
    r = client.post("/preview", json={"url": "https://yt.example/list"})
    body = r.json()
    by_id = {e["id"]: e for e in body["entries"]}
    assert by_id["aaa11111111"]["already_downloaded"] is None
    assert by_id["bbb22222222"]["already_downloaded"] is not None
    assert (
        by_id["bbb22222222"]["already_downloaded"]["path"]
        == "/data/out/Beta [bbb22222222].mp4"
    )


def test_enrich_marks_per_url_timeout_without_failing_batch(
    fast_timeout_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One slow URL must not poison the whole batch.

    Replace asyncio.to_thread with a per-call hang/no-hang dispatcher: the
    "stuck" URL sleeps past wait_for, others return immediately. The slow
    entry's error field should read "probe timeout"; siblings succeed.
    """
    good_info = {
        "title": "ok",
        "duration": 10,
        "uploader": "u",
        "thumbnail": "t",
    }
    monkeypatch.setattr(
        "ytdl.api.routes_preview.probe_one",
        lambda url, **kwargs: good_info,
    )

    async def _per_url_to_thread(func, *args, **kwargs):  # type: ignore[no-untyped-def]
        # First positional arg is the URL (probe_one's first arg).
        url = args[0] if args else kwargs.get("url")
        if url == "https://x/stuck":
            await asyncio.sleep(30.0)
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "ytdl.api.routes_preview.asyncio.to_thread", _per_url_to_thread
    )

    r = fast_timeout_client.post(
        "/preview/enrich",
        json={"urls": ["https://x/good", "https://x/stuck"]},
    )
    assert r.status_code == 200
    entries = {e["url"]: e for e in r.json()["entries"]}
    assert entries["https://x/good"]["error"] is None
    assert entries["https://x/good"]["title"] == "ok"
    assert entries["https://x/stuck"]["error"] == "probe timeout"
    assert entries["https://x/stuck"]["title"] is None
