from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ytdl.api import build_app
from ytdl.config import Config


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    out = tmp_path / "out"
    out.mkdir()
    (out / "Alpha [a].mp4").write_bytes(b"x" * 10)
    (out / "Beta [b].mp4").write_bytes(b"y" * 20)
    sub = out / "MyPlaylist"
    sub.mkdir()
    (sub / "01 - One [c].mp4").write_bytes(b"z" * 30)
    cfg = Config(
        output_dir=out,
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
    )
    return TestClient(build_app(cfg))


def test_library_lists_files(client: TestClient) -> None:
    r = client.get("/library")
    assert r.status_code == 200
    body = r.json()
    paths = {item["relpath"] for item in body["entries"]}
    assert "Alpha [a].mp4" in paths
    assert "Beta [b].mp4" in paths
    assert "MyPlaylist/01 - One [c].mp4" in paths


def test_library_rejects_traversal(client: TestClient) -> None:
    r = client.get("/library?subdir=../../etc")
    assert r.status_code == 400


def test_library_rescan_returns_zero_for_empty_output_dir(tmp_path: Path) -> None:
    out = tmp_path / "empty_out"
    out.mkdir()
    cfg = Config(
        output_dir=out,
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
    )
    c = TestClient(build_app(cfg))
    r = c.post("/library/rescan")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 0
    assert body["scanned_dirs"] == [str(out.resolve())]
    assert body["elapsed_s"] >= 0.0


def test_library_rescan_returns_expected_count_after_seed(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    # Two files with valid yt-dlp-style ids + one file without.
    (out / "One [aaa11111111].mp4").write_bytes(b"x" * 10)
    (out / "Two [bbb22222222].mkv").write_bytes(b"y" * 20)
    (out / "no_id_here.txt").write_text("noise")
    cfg = Config(
        output_dir=out,
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
    )
    c = TestClient(build_app(cfg))
    r = c.post("/library/rescan")
    assert r.status_code == 200
    assert r.json()["count"] == 2


def test_library_rescan_uses_configured_scan_dirs(tmp_path: Path) -> None:
    """When library_scan_dirs is set explicitly, rescan walks THOSE dirs
    instead of falling back to output_dir. Prevents a regression where
    the explicit config was silently ignored."""
    output_dir = tmp_path / "output_unused"
    output_dir.mkdir()
    (output_dir / "OutputOnly [ccc33333333].mp4").write_bytes(b"n")
    extra = tmp_path / "explicit_lib"
    extra.mkdir()
    (extra / "Explicit [ddd44444444].mp4").write_bytes(b"x")
    cfg = Config(
        output_dir=output_dir,
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
        library_scan_dirs=(str(extra),),
    )
    c = TestClient(build_app(cfg))
    body = c.post("/library/rescan").json()
    assert body["count"] == 1
    # Only the explicit dir was scanned.
    assert body["scanned_dirs"] == [str(extra.resolve())]


def test_library_lists_files_when_output_dir_is_symlinked(tmp_path: Path) -> None:
    """Regression test: macOS /tmp is a symlink to /private/tmp. The library
    must list files even when output_dir traverses through a symlink."""
    real_out = tmp_path / "real_out"
    real_out.mkdir()
    (real_out / "file.mp4").write_bytes(b"x" * 100)
    sym_out = tmp_path / "via_symlink"
    sym_out.symlink_to(real_out)

    cfg = Config(
        output_dir=sym_out,  # the config dir is symlinked
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        default_format="best",
    )
    c = TestClient(build_app(cfg))
    r = c.get("/library")
    assert r.status_code == 200
    paths = {item["relpath"] for item in r.json()["entries"]}
    assert "file.mp4" in paths, f"file.mp4 missing from {paths}"
