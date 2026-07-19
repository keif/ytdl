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


def test_classify_not_a_bot_as_forbidden() -> None:
    """YouTube's anti-bot challenge ('Sign in to confirm you're not a bot') is
    the canonical cookie-required signal. It must classify as FORBIDDEN so the
    worker surfaces the cookie-setup hint rather than a dead-end [permanent]
    error. The real message ships a curly apostrophe, so the pattern keys off
    'not a bot' rather than the quote."""
    msg = (
        "Sign in to confirm you’re not a bot. Use --cookies-from-browser "
        "or --cookies for the authentication."
    )
    assert classify_error(_err(msg)) == Classification.FORBIDDEN


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


def test_download_captures_thumbnail_from_info(tmp_path: Path) -> None:
    """The download must capture yt-dlp's thumbnail URL so the queue row shows
    the video's image even for jobs that never went through a preview (worker-
    promoted playlist children) — those otherwise have no thumbnail at all."""
    fake_ydl = MagicMock()
    fake_ydl_cls = MagicMock(return_value=fake_ydl)
    fake_ydl.__enter__ = MagicMock(return_value=fake_ydl)
    fake_ydl.__exit__ = MagicMock(return_value=False)
    fake_ydl.extract_info.return_value = {
        "id": "abc",
        "title": "Test Video",
        "thumbnail": "https://i.ytimg.com/vi/abc/hqdefault.jpg",
        "requested_downloads": [{"filepath": str(tmp_path / "Test Video [abc].mp4")}],
    }
    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=fake_ydl_cls,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    result = download(job, ctx)
    assert result.thumbnail_url == "https://i.ytimg.com/vi/abc/hqdefault.jpg"


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


def test_build_ydl_options_sets_cookiefile_when_configured(tmp_path: Path) -> None:
    """A cookies.txt file (yt-dlp's `cookiefile`) is the only way to
    authenticate in Docker, where no host browser cookie store is reachable.
    When set on the context it must reach yt-dlp's opts."""
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        cookies_file="/cookies.txt",
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["cookiefile"] == "/cookies.txt"


def test_build_ydl_options_omits_cookiefile_when_unset(tmp_path: Path) -> None:
    """No cookies file configured -> yt-dlp opts must not carry a `cookiefile`
    key (an empty/None value would make yt-dlp try to open it and error)."""
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert "cookiefile" not in opts


def test_build_probe_opts_sets_cookiefile_when_configured() -> None:
    """The probe path must honor the same cookies file as the download path,
    so preview/enrich in Docker can get past the anti-bot gate too."""
    from ytdl.downloader import _build_probe_opts

    opts = _build_probe_opts(
        "https://youtu.be/abc",
        None,
        30,
        flat=True,
        use_playlist=False,
        cookies_file="/cookies.txt",
    )
    assert opts["cookiefile"] == "/cookies.txt"


def test_build_probe_opts_omits_cookiefile_when_unset() -> None:
    from ytdl.downloader import _build_probe_opts

    opts = _build_probe_opts(
        "https://youtu.be/abc",
        None,
        30,
        flat=True,
        use_playlist=False,
    )
    assert "cookiefile" not in opts


def test_build_ydl_options_sets_pot_provider_extractor_args(tmp_path: Path) -> None:
    """When a PO token provider URL is configured, yt-dlp opts must carry the
    bgutil HTTP provider base_url so it can mint Proof-of-Origin tokens — the
    only way past YouTube's anti-bot gate once cookies alone stop working."""
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        pot_provider_url="http://bgutil-provider:4416",
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
        "http://bgutil-provider:4416"
    ]


def test_build_ydl_options_omits_extractor_args_without_pot(tmp_path: Path) -> None:
    """No provider configured -> no extractor_args key, so installs without a
    PO token sidecar behave exactly as before."""
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert "extractor_args" not in opts


def test_build_probe_opts_sets_pot_provider_extractor_args() -> None:
    """The probe path needs the PO token provider too — preview/enrich hit the
    same anti-bot gate as the download."""
    from ytdl.downloader import _build_probe_opts

    opts = _build_probe_opts(
        "https://youtu.be/abc",
        None,
        30,
        flat=True,
        use_playlist=False,
        pot_provider_url="http://bgutil-provider:4416",
    )
    assert opts["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
        "http://bgutil-provider:4416"
    ]


def test_build_probe_opts_omits_extractor_args_without_pot() -> None:
    from ytdl.downloader import _build_probe_opts

    opts = _build_probe_opts(
        "https://youtu.be/abc", None, 30, flat=True, use_playlist=False
    )
    assert "extractor_args" not in opts


def test_build_ydl_options_sets_noplaylist_for_plain_video(tmp_path: Path) -> None:
    """Plain video URLs (no list parameter) must keep noplaylist=True so
    yt-dlp's multifeed/multicamera handling doesn't expand them into
    feed-as-playlist results."""
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


def test_build_ydl_options_expands_radio_mix_for_top_level_job(tmp_path: Path) -> None:
    """Radio-mix URLs (?v=X&list=RDX) used to be forced to single-video
    as defensive cover for the auto-redirect case. We now treat them
    like any other list: the user gets the picker. Users in the auto-
    redirect case can paste the bare ?v=X URL instead."""
    from ytdl.downloader import _build_ydl_options
    from ytdl.models import Job, JobKind, JobStatus

    job = Job(
        id="01",
        url="https://www.youtube.com/watch?v=abc&list=RDabc",
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["noplaylist"] is False


def test_build_ydl_options_clears_noplaylist_for_real_hybrid_playlist(tmp_path: Path) -> None:
    """A direct `ytdl get https://...watch?v=X&list=PL...` must expand the
    playlist, not download only the single video. This is the bug report
    case — the CLI bypasses probe() and calls download() directly."""
    from ytdl.downloader import _build_ydl_options
    from ytdl.models import Job, JobKind, JobStatus

    job = Job(
        id="01",
        url="https://www.youtube.com/watch?v=abc&list=PLxyz",
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["noplaylist"] is False


def test_build_ydl_options_forces_noplaylist_for_playlist_children(tmp_path: Path) -> None:
    """Playlist children carry single-video URLs (the worker extracted them
    from the playlist probe). Even if the URL somehow looked playlist-shaped,
    noplaylist must be True to prevent re-expansion."""
    from ytdl.downloader import _build_ydl_options
    from ytdl.models import Job, JobKind, JobStatus

    job = Job(
        id="01",
        url="https://www.youtube.com/watch?v=abc&list=PLxyz",
        kind=JobKind.VIDEO,
        parent_job_id="parent-id-xyz",
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
    )
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


def test_build_ydl_options_sets_socket_timeout_from_ctx(tmp_path: Path) -> None:
    """The ctx.probe_timeout_s field must flow into yt-dlp's socket_timeout.

    Without this, a non-responsive HTTP socket would park the worker thread
    indefinitely — the bug that motivated the timeout work.
    """
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
        probe_timeout_s=45,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["socket_timeout"] == 45


def test_build_ydl_options_socket_timeout_defaults_to_thirty(tmp_path: Path) -> None:
    """When ctx omits probe_timeout_s, the dataclass default (30) lands in opts."""
    from ytdl.downloader import _build_ydl_options

    job = _make_job(tmp_path)
    ctx = DownloadContext(
        ydl_cls=None,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
    )
    opts = _build_ydl_options(job, ctx, ProgressThrottle())
    assert opts["socket_timeout"] == 30


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


# ---- noplaylist discriminator ----
# The probe uses `noplaylist=True` by default — yt-dlp uses it to keep
# plain `?v=X` URLs and multifeed/multicamera videos as single-video.
# Any URL with a `list=` parameter (real playlist OR radio mix) gets
# `noplaylist=False` so the probe returns playlist info and the UI
# offers the picker. We previously distinguished RD-prefixed (radio)
# from PL/OL/etc. (curated) lists; that protection over-applied for
# users who deliberately grabbed a radio URL.


def test_url_targets_a_playlist_pure_video_url() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # Plain video URL — no list at all. Must be False so noplaylist=True
    # protects against multifeed/multicamera expansion.
    assert _url_targets_a_playlist("https://www.youtube.com/watch?v=abc") is False


def test_url_targets_a_playlist_pure_playlist_url() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # Pure playlist URL — no `v=`, list ID is a PL. Must expand.
    assert _url_targets_a_playlist("https://www.youtube.com/playlist?list=PLxyz") is True


def test_url_targets_a_playlist_hybrid_real_playlist() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # User pastes a video URL while watching inside a real playlist.
    # Address bar has both `v=` and a PL-prefixed list. Must expand.
    assert (
        _url_targets_a_playlist(
            "https://www.youtube.com/watch?v=abc&list=PLxyz123"
        )
        is True
    )


def test_url_targets_a_playlist_radio_mix_url() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # YouTube radio mix — `&list=RD...`. Used to be False (single video)
    # as defensive cover for the auto-redirect case. Now True: users who
    # deliberately paste a radio URL want the picker, and the auto-
    # redirect case is recoverable (paste bare `?v=X` instead).
    assert (
        _url_targets_a_playlist(
            "https://www.youtube.com/watch?v=abc&list=RDabc"
        )
        is True
    )


def test_url_targets_a_playlist_mix_radio_variants() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # All RD-prefixed lists are radio/mix variants. All expand now.
    assert (
        _url_targets_a_playlist("https://www.youtube.com/watch?v=x&list=RDMM123")
        is True
    )
    assert (
        _url_targets_a_playlist("https://www.youtube.com/watch?v=x&list=RDCLAK1")
        is True
    )


def test_url_targets_a_playlist_other_curated_lists() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # Liked, Watch Later, Library Mix — user-curated, expand them.
    assert _url_targets_a_playlist("https://www.youtube.com/playlist?list=LL") is True
    assert _url_targets_a_playlist("https://www.youtube.com/playlist?list=WL") is True
    assert _url_targets_a_playlist("https://www.youtube.com/playlist?list=LM") is True


def test_url_targets_a_playlist_malformed_url() -> None:
    from ytdl.downloader import _url_targets_a_playlist

    # Garbage input shouldn't raise — return False (defer to yt-dlp).
    assert _url_targets_a_playlist("not a url at all") is False
    assert _url_targets_a_playlist("") is False


# ---- probe()/probe_one() subprocess pipeline ----
# probe() and probe_one() shell out to ytdl._probe_worker so the OS can
# kill a wedged yt-dlp. The asyncio.wait_for backstop in routes_preview
# can cancel the AWAITER but not the underlying thread, which is how
# repeated probe hangs filled the executor pool. The tests below verify
# the wrapper's contract without actually launching a subprocess.


def _fake_completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Build a CompletedProcess test double for subprocess.run patches."""
    import subprocess

    return subprocess.CompletedProcess(
        args=[], returncode=returncode, stdout=stdout, stderr=stderr
    )


def test_probe_forwards_noplaylist_false_for_hybrid_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hybrid `?v=X&list=PL...` URL must reach the subprocess with
    noplaylist=False so yt-dlp expands the playlist."""
    import subprocess as _subprocess

    seen: dict = {}

    def fake_run(cmd, *, timeout, capture_output, text):
        # argv[3] is the JSON args blob (sys.executable, -m, module, json).
        import json as _json

        seen["argv"] = cmd
        seen["timeout"] = timeout
        seen["args_blob"] = _json.loads(cmd[3])
        return _fake_completed(stdout='{"_type": "playlist", "entries": []}')

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    probe("https://www.youtube.com/watch?v=X&list=PLxyz", socket_timeout=30)
    assert seen["args_blob"]["opts"]["noplaylist"] is False
    # extract_flat must stay set so the worker returns flat entry stubs
    # (not per-video fetches) for upfront listing.
    assert seen["args_blob"]["opts"]["extract_flat"] == "in_playlist"


def test_probe_forwards_noplaylist_true_for_plain_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Plain `?v=X` URLs must keep noplaylist=True so yt-dlp's multifeed
    handling doesn't expand the video into a feed-as-playlist."""
    import subprocess as _subprocess

    seen: dict = {}

    def fake_run(cmd, *, timeout, capture_output, text):
        import json as _json

        seen["args_blob"] = _json.loads(cmd[3])
        return _fake_completed(stdout='{"_type": "video"}')

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    probe("https://www.youtube.com/watch?v=abc")
    assert seen["args_blob"]["opts"]["noplaylist"] is True


def test_probe_subprocess_timeout_reraised_as_timeout_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """subprocess.TimeoutExpired must surface as TimeoutError so the
    route's existing `except TimeoutError` returns 504 unchanged."""
    import subprocess as _subprocess

    def fake_run(cmd, *, timeout, capture_output, text):
        raise _subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    with pytest.raises(TimeoutError) as exc_info:
        probe("https://yt/x", socket_timeout=2)
    # Surface the configured deadline so logs make the layering obvious
    # (socket_timeout + grace).
    assert "timed out" in str(exc_info.value)


def test_probe_nonzero_exit_propagates_structured_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the worker exits non-zero, the JSON error payload on STDOUT
    must be unpacked and re-raised as RuntimeError. stdout (not stderr)
    so yt-dlp's own ERROR / WARNING text on stderr can't corrupt the
    payload."""
    import subprocess as _subprocess

    def fake_run(cmd, *, timeout, capture_output, text):
        return _fake_completed(
            returncode=1,
            stdout='{"error": "Video unavailable", "type": "yt_dlp_error"}',
            # Mirror what yt-dlp does on real failure: free-form text on
            # stderr alongside our structured payload on stdout. The
            # caller must not be confused by this mixture.
            stderr="ERROR: [youtube] xxx: Video unavailable\n",
        )

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    with pytest.raises(RuntimeError) as exc_info:
        probe("https://yt/x")
    # The structured payload's clean message should surface — NOT a mix
    # of yt-dlp's stderr text and our JSON.
    assert str(exc_info.value) == "Video unavailable"


def test_probe_nonzero_exit_with_plain_stderr_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If stdout isn't valid JSON (e.g. interpreter crash before our
    error emitter ran), fall back to stderr text rather than silently
    swallowing the failure."""
    import subprocess as _subprocess

    def fake_run(cmd, *, timeout, capture_output, text):
        return _fake_completed(
            returncode=2,
            stdout="",  # nothing on stdout — crashed before _emit_error
            stderr="Traceback...\nKeyError: x\n",
        )

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    with pytest.raises(RuntimeError) as exc_info:
        probe("https://yt/x")
    assert "KeyError" in str(exc_info.value)


def test_probe_subprocess_timeout_is_socket_timeout_plus_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The subprocess.run timeout must sit ABOVE yt-dlp's socket_timeout
    so the inner deadline gets a chance to fire first (cleaner error
    messages). The +5s grace is the documented layering."""
    import subprocess as _subprocess

    seen: dict = {}

    def fake_run(cmd, *, timeout, capture_output, text):
        seen["timeout"] = timeout
        return _fake_completed(stdout="{}")

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    probe("https://yt/x", socket_timeout=12)
    assert seen["timeout"] == 17


def test_probe_one_forwards_noplaylist_true(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """probe_one() always passes noplaylist=True — even for hybrid URLs —
    so enrichment returns the single-video metadata for the entry the
    caller asked about, not the surrounding playlist."""
    import subprocess as _subprocess

    seen: dict = {}

    def fake_run(cmd, *, timeout, capture_output, text):
        import json as _json

        seen["args_blob"] = _json.loads(cmd[3])
        return _fake_completed(stdout='{"title": "x"}')

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe_one

    probe_one("https://www.youtube.com/watch?v=X&list=PL")
    assert seen["args_blob"]["opts"]["noplaylist"] is True
    # probe_one fetches full metadata, so extract_flat must NOT be set.
    assert "extract_flat" not in seen["args_blob"]["opts"]


def test_probe_passes_cookiesfrombrowser_as_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """JSON has no tuple type. Send the cookies arg as a list and let
    the worker coerce back to tuple before handing to yt-dlp."""
    import subprocess as _subprocess

    seen: dict = {}

    def fake_run(cmd, *, timeout, capture_output, text):
        import json as _json

        seen["args_blob"] = _json.loads(cmd[3])
        return _fake_completed(stdout="{}")

    monkeypatch.setattr(_subprocess, "run", fake_run)
    from ytdl.downloader import probe

    probe("https://yt/x", cookies_browser="chrome")
    assert seen["args_blob"]["opts"]["cookiesfrombrowser"] == ["chrome"]
