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
