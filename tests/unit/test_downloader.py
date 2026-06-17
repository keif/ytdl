from __future__ import annotations

import time

import pytest

from ytdl.downloader import (
    Classification,
    ProgressThrottle,
    build_format_selector,
    build_output_template,
    classify_error,
)


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
