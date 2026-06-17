from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ytdl.downloader import (
    Classification,
    DownloadContext,
    DownloadResult,
    ProgressThrottle,
    build_format_selector,
    build_output_template,
    classify_error,
    download,
)
from ytdl.models import Job, JobKind, JobStatus


def test_format_selector_best() -> None:
    assert build_format_selector("best") == "bv*+ba/best"


def test_format_selector_1080p() -> None:
    assert build_format_selector("1080p") == (
        "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/best"
    )


def test_format_selector_audio_only() -> None:
    assert build_format_selector("audio_only") == "bestaudio/best"


def test_format_selector_passthrough() -> None:
    # Anything else is treated as a raw yt-dlp format string.
    assert build_format_selector("137+140") == "137+140"


def test_output_template_video_uses_id_suffix() -> None:
    tpl = build_output_template("/out", is_playlist_child=False)
    assert tpl == "/out/%(title)s [%(id)s].%(ext)s"


def test_output_template_playlist_child_includes_index() -> None:
    tpl = build_output_template("/out/My Playlist", is_playlist_child=True)
    assert tpl == "/out/My Playlist/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"


# --- error classification ---

def _err(msg: str, cls: str = "DownloadError") -> Exception:
    e = type(cls, (Exception,), {})(msg)
    return e


def test_classify_transient_network() -> None:
    assert classify_error(_err("HTTP Error 503: Service Unavailable")) == Classification.TRANSIENT
    assert classify_error(_err("Connection reset by peer")) == Classification.TRANSIENT
    assert classify_error(_err("Read timed out")) == Classification.TRANSIENT


def test_classify_rate_limited() -> None:
    assert classify_error(_err("HTTP Error 429: Too Many Requests")) == Classification.RATE_LIMITED


def test_classify_age_gated() -> None:
    msg = "Sign in to confirm your age. This video may be inappropriate"
    assert classify_error(_err(msg)) == Classification.AUTH_REQUIRED


def test_classify_private() -> None:
    assert classify_error(_err("Private video")) == Classification.AUTH_REQUIRED
    assert classify_error(_err("Members-only content")) == Classification.AUTH_REQUIRED


def test_classify_geo() -> None:
    assert (
        classify_error(_err("Geo restricted; not available in your country"))
        == Classification.GEO_BLOCKED
    )


def test_classify_unavailable() -> None:
    assert classify_error(_err("Video unavailable")) == Classification.UNAVAILABLE


def test_classify_disk_full() -> None:
    err = OSError(28, "No space left on device")
    assert classify_error(err) == Classification.DISK_FULL


def test_classify_permission() -> None:
    assert classify_error(PermissionError("denied")) == Classification.DISK_PERMISSION


def test_classify_unknown_defaults_to_permanent() -> None:
    assert classify_error(_err("something weird happened")) == Classification.PERMANENT


# --- throttle ---

def test_throttle_first_call_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    t = ProgressThrottle(interval_s=1.0)
    assert t.should_emit() is True


def test_throttle_blocks_within_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    t = ProgressThrottle(interval_s=1.0)
    t.should_emit()
    now[0] += 0.5
    assert t.should_emit() is False


def test_throttle_releases_after_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: now[0])
    t = ProgressThrottle(interval_s=1.0)
    t.should_emit()
    now[0] += 1.5
    assert t.should_emit() is True


def _make_job(tmp_path: Path) -> Job:
    return Job(
        id="01",
        url="https://youtu.be/abc",
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
    )


def test_download_calls_yt_dlp_with_built_options(tmp_path: Path) -> None:
    fake_ydl = MagicMock()
    fake_ydl_cls = MagicMock(return_value=fake_ydl)
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)
    fake_ydl.extract_info.return_value = {
        "id": "abc",
        "title": "Test Video",
        "uploader": "Test Channel",
        "duration": 42,
        "requested_downloads": [{"filepath": str(tmp_path / "Test Video [abc].mp4")}],
    }

    progress_events: list[dict] = []
    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=fake_ydl_cls,
        cookies_browser="chrome",
        on_progress=lambda d: progress_events.append(d),
        cancel_flag=lambda: False,
    )
    result = download(job, ctx)

    assert isinstance(result, DownloadResult)
    assert result.output_path == str(tmp_path / "Test Video [abc].mp4")
    assert result.title == "Test Video"
    assert result.video_id == "abc"

    # The YoutubeDL options should include our format string and cookie tuple.
    opts = fake_ydl_cls.call_args.args[0]
    assert opts["format"] == "bv*+ba/best"
    assert opts["cookiesfrombrowser"] == ("chrome",)
    assert opts["restrictfilenames"] is True
    assert "progress_hooks" in opts


def test_download_progress_hook_fires_through_throttle(tmp_path: Path) -> None:
    fake_ydl = MagicMock()
    fake_ydl_cls = MagicMock(return_value=fake_ydl)
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)

    captured: list[dict] = []

    def extract_info(url: str, download: bool = True) -> dict:
        # simulate yt-dlp invoking the hook several times
        hook = fake_ydl_cls.call_args.args[0]["progress_hooks"][0]
        for i in range(3):
            hook({"status": "downloading", "downloaded_bytes": (i + 1) * 1000})
        hook({"status": "finished", "filename": str(tmp_path / "x.mp4")})
        return {
            "id": "x",
            "title": "X",
            "requested_downloads": [{"filepath": str(tmp_path / "x.mp4")}],
        }

    fake_ydl.extract_info.side_effect = extract_info
    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=fake_ydl_cls,
        cookies_browser=None,
        on_progress=lambda d: captured.append(d),
        cancel_flag=lambda: False,
        throttle_interval_s=0.0,  # no throttling for the test
    )
    download(job, ctx)
    # At least the first call goes through; throttle off means all do.
    assert len(captured) >= 1
    assert captured[0]["status"] == "downloading"


def test_download_aborts_when_cancel_flag_set(tmp_path: Path) -> None:
    from ytdl.downloader import DownloadCancelled

    fake_ydl = MagicMock()
    fake_ydl_cls = MagicMock(return_value=fake_ydl)
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)

    def extract_info(url: str, download: bool = True) -> dict:
        hook = fake_ydl_cls.call_args.args[0]["progress_hooks"][0]
        hook({"status": "downloading", "downloaded_bytes": 1000})  # raises
        return {}

    fake_ydl.extract_info.side_effect = extract_info
    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=fake_ydl_cls,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: True,
        throttle_interval_s=0.0,
    )
    with pytest.raises(DownloadCancelled):
        download(job, ctx)
