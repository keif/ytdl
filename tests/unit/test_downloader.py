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


def test_classify_http_403_as_forbidden() -> None:
    assert classify_error(_err("HTTP Error 403: Forbidden")) == Classification.FORBIDDEN


def test_classify_requested_format_unavailable_as_forbidden() -> None:
    assert (
        classify_error(_err("Requested format is not available"))
        == Classification.FORBIDDEN
    )


def test_classify_no_video_formats_as_forbidden() -> None:
    assert (
        classify_error(_err("[youtube] X: No video formats found!"))
        == Classification.FORBIDDEN
    )


def test_classify_country_unavailability_as_geo_not_forbidden() -> None:
    """Country-only block (no 403, no format issue) is geo-blocked, not
    forbidden. Cookies don't fix it."""
    msg = "This video is unavailable in your country"
    assert classify_error(_err(msg)) == Classification.GEO_BLOCKED


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


def test_build_ydl_options_sets_noplaylist(tmp_path: Path) -> None:
    """A URL like ?v=X&list=Y should download only the single video.

    yt-dlp respects noplaylist=True by treating the &list= as context.
    """
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["noplaylist"] is True


def test_build_ydl_options_enables_ejs_remote_components(tmp_path: Path) -> None:
    """yt-dlp 2026.x ships EJS challenge solvers as opt-in remote components.

    Without enabling them, YouTube's n-challenge fails and `Requested format
    is not available` cascades down. We must request the ejs:github source.
    """
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts.get("remote_components") == ["ejs:github"]


def test_build_ydl_options_sets_overwrites_when_force_overwrite(tmp_path: Path) -> None:
    from ytdl.downloader import _build_ydl_options

    job = Job(
        id="01",
        url="https://yt/x",
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
        force_overwrite=True,
    )
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts.get("overwrites") is True
    # Must also flip continuedl=False, otherwise yt-dlp's "treat complete
    # file as already downloaded" path short-circuits the re-fetch even
    # with overwrites=True. Mirrors `yt-dlp --force-overwrites` CLI.
    assert opts.get("continuedl") is False


def test_build_ydl_options_enables_subtitles_when_flag_set(tmp_path: Path) -> None:
    from ytdl.downloader import _build_ydl_options

    job = Job(
        id="01",
        url="https://yt/x",
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
        subtitles=True,
    )
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
        subtitle_langs=("es", "en"),
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts.get("writesubtitles") is True
    # Auto-CC is markedly lower quality; only fetch real subtitles.
    assert opts.get("writeautomaticsub") is False
    assert opts.get("subtitleslangs") == ["es", "en"]
    # Postprocessors must include FFmpegEmbedSubtitle so the .vtt is baked
    # into the MP4. `already_have_subtitle=True` keeps the sidecar file on
    # disk (the postprocessor's default deletes it after embedding).
    embed_pp = next(
        (p for p in opts.get("postprocessors", []) if p.get("key") == "FFmpegEmbedSubtitle"),
        None,
    )
    assert embed_pp is not None
    assert embed_pp.get("already_have_subtitle") is True


def test_build_ydl_options_omits_subtitle_keys_by_default(tmp_path: Path) -> None:
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert "writesubtitles" not in opts
    assert "writeautomaticsub" not in opts
    assert "subtitleslangs" not in opts
    assert "postprocessors" not in opts


def test_build_ydl_options_omits_overwrites_by_default(tmp_path: Path) -> None:
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert "overwrites" not in opts
    assert "continuedl" not in opts


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
