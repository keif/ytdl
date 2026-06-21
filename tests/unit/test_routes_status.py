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
        cookies_source="none",
        default_format="best",
    )
    return TestClient(build_app(cfg))


def test_status_returns_cookies_and_runtime_keys(client: TestClient) -> None:
    """The /status response carries cookies + deno + ffmpeg presence so the
    UI can render diagnostic chips."""
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "cookies_browser",
        "cookies_source",
        "deno",
        "ffmpeg",
    }
    for key in ("deno", "ffmpeg"):
        assert set(body[key].keys()) == {"present", "path"}
        assert isinstance(body[key]["present"], bool)
        assert body[key]["path"] is None or isinstance(body[key]["path"], str)


def test_status_reflects_missing_deno(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When deno isn't on PATH, the chip data must say so explicitly."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda name: None)
    body = client.get("/status").json()
    assert body["deno"]["present"] is False
    assert body["deno"]["path"] is None
    assert body["ffmpeg"]["present"] is False


def test_status_reflects_present_deno(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    monkeypatch.setattr(
        shutil,
        "which",
        lambda name: f"/usr/local/bin/{name}" if name in ("deno", "ffmpeg") else None,
    )
    body = client.get("/status").json()
    assert body["deno"]["present"] is True
    assert body["deno"]["path"] == "/usr/local/bin/deno"
    assert body["ffmpeg"]["present"] is True
    assert body["ffmpeg"]["path"] == "/usr/local/bin/ffmpeg"
