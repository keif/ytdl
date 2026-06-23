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
        "subtitles_default",
        "output_dir",
        "autosubmit_delay_s",
    }
    for key in ("deno", "ffmpeg"):
        assert set(body[key].keys()) == {"present", "path"}
        assert isinstance(body[key]["present"], bool)
        assert body[key]["path"] is None or isinstance(body[key]["path"], str)
    assert isinstance(body["subtitles_default"], bool)
    assert isinstance(body["output_dir"], str)
    assert body["output_dir"]
    assert isinstance(body["autosubmit_delay_s"], int)
    # Default in Config() is 5 — assert exactly so a future change to the
    # default is caught here.
    assert body["autosubmit_delay_s"] == 5


def test_status_surfaces_subtitles_default(tmp_path: Path) -> None:
    """When the config opts in by default, /status reflects that so the UI
    can pre-check the Subtitles checkbox."""
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        cookies_source="none",
        default_format="best",
        subtitles_default=True,
    )
    c = TestClient(build_app(cfg))
    body = c.get("/status").json()
    assert body["subtitles_default"] is True


def test_status_surfaces_autosubmit_delay(tmp_path: Path) -> None:
    """The countdown delay is read by the UI on mount; /status must surface
    the configured value so the banner uses the same default as the server."""
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        cookies_source="none",
        default_format="best",
        autosubmit_delay_s=10,
    )
    c = TestClient(build_app(cfg))
    body = c.get("/status").json()
    assert body["autosubmit_delay_s"] == 10


def test_status_surfaces_disabled_autosubmit(tmp_path: Path) -> None:
    """A delay of 0 disables the feature. /status reports it verbatim so the
    UI can short-circuit the countdown without inferring intent."""
    cfg = Config(
        output_dir=tmp_path / "out",
        db_path=tmp_path / "ytdl.db",
        workers=0,
        cookies_browser=None,
        cookies_source="none",
        default_format="best",
        autosubmit_delay_s=0,
    )
    c = TestClient(build_app(cfg))
    body = c.get("/status").json()
    assert body["autosubmit_delay_s"] == 0


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
