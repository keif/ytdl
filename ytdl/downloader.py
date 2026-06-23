"""yt-dlp wrapper.

This module is split into two halves:
  - pure helpers (format selector, output template, error classifier,
    progress throttle) — tested in isolation
  - `download(job, ctx)` — calls into yt-dlp, wired in the next task
"""
from __future__ import annotations

import errno
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class Classification(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
    FORBIDDEN = "forbidden"
    GEO_BLOCKED = "geo_blocked"
    UNAVAILABLE = "unavailable"
    DISK_FULL = "disk_full"
    DISK_PERMISSION = "disk_permission"
    PERMANENT = "permanent"


def build_format_selector(pref: str) -> str:
    """Translate the user-facing format preference into a yt-dlp format string."""
    if pref == "best":
        return "bv*+ba/best"
    if pref == "audio_only":
        return "bestaudio/best"
    if m := re.fullmatch(r"(\d+)p", pref):
        h = m.group(1)
        return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/best"
    # Treat anything else as a raw yt-dlp format string.
    return pref


def build_output_template(output_dir: str, *, is_playlist_child: bool) -> str:
    if is_playlist_child:
        return f"{output_dir}/%(playlist_index)02d - %(title)s [%(id)s].%(ext)s"
    return f"{output_dir}/%(title)s [%(id)s].%(ext)s"


_TRANSIENT_PATTERNS = (
    re.compile(r"HTTP Error 5\d\d", re.I),
    re.compile(r"connection reset", re.I),
    re.compile(r"timed out", re.I),
    re.compile(r"temporary failure in name resolution", re.I),
)
_RATE_LIMIT_PATTERNS = (re.compile(r"HTTP Error 429", re.I),)
_AUTH_PATTERNS = (
    re.compile(r"sign in to confirm your age", re.I),
    re.compile(r"private video", re.I),
    re.compile(r"members[- ]only", re.I),
    re.compile(r"login required", re.I),
)
# Anti-bot / cookie-needed signals. yt-dlp surfaces these when YouTube serves a
# 403 page or refuses to hand out a playable manifest to an unauthenticated
# client. The fix is almost always `ytdl cookies use <browser>` + restart, so
# we classify these separately from PERMANENT and let workers.py prepend an
# actionable hint to the saved error string.
_FORBIDDEN_PATTERNS = (
    re.compile(r"HTTP Error 403", re.I),
    re.compile(r"requested format is not available", re.I),
    re.compile(r"no video formats found", re.I),
)
_GEO_PATTERNS = (
    re.compile(r"geo restricted", re.I),
    re.compile(r"not available in your country", re.I),
    re.compile(r"this video is unavailable in your country", re.I),
)
_UNAVAILABLE_PATTERNS = (
    re.compile(r"video unavailable", re.I),
    re.compile(r"this video has been removed", re.I),
)


def classify_error(exc: BaseException) -> Classification:
    if isinstance(exc, PermissionError):
        return Classification.DISK_PERMISSION
    if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
        return Classification.DISK_FULL
    msg = str(exc)
    for p in _TRANSIENT_PATTERNS:
        if p.search(msg):
            return Classification.TRANSIENT
    for p in _RATE_LIMIT_PATTERNS:
        if p.search(msg):
            return Classification.RATE_LIMITED
    for p in _AUTH_PATTERNS:
        if p.search(msg):
            return Classification.AUTH_REQUIRED
    # Forbidden runs AFTER auth so explicit "private video" / "members only"
    # still classify as AUTH_REQUIRED — those messages are clearer signals.
    # But forbidden runs BEFORE geo/unavailable because YouTube's anti-bot
    # 403 sometimes ships with a misleading "country" string.
    for p in _FORBIDDEN_PATTERNS:
        if p.search(msg):
            return Classification.FORBIDDEN
    for p in _GEO_PATTERNS:
        if p.search(msg):
            return Classification.GEO_BLOCKED
    for p in _UNAVAILABLE_PATTERNS:
        if p.search(msg):
            return Classification.UNAVAILABLE
    return Classification.PERMANENT


@dataclass
class ProgressThrottle:
    interval_s: float = 1.0
    _last: float = 0.0

    def should_emit(self) -> bool:
        now = time.monotonic()
        if now - self._last >= self.interval_s:
            self._last = now
            return True
        return False


class DownloadCancelled(Exception):
    """Raised inside the progress hook when the worker observes the cancel flag."""


@dataclass
class DownloadContext:
    ydl_cls: Any  # yt_dlp.YoutubeDL or test double
    cookies_browser: str | None
    on_progress: Callable[[dict[str, Any]], None]
    cancel_flag: Callable[[], bool]
    throttle_interval_s: float = 1.0
    # Languages to request when a job opts into subtitles. Threaded through
    # DownloadContext (rather than _build_ydl_options' signature) so adding
    # more knobs later doesn't keep growing the function parameter list.
    subtitle_langs: tuple[str, ...] | list[str] = ("en",)
    # Bounds yt-dlp's HTTP socket reads so a wedged server can't park a
    # worker thread forever. Mirrors the same knob applied to probe() —
    # the actual download path benefits too, since a hung mid-fetch socket
    # would otherwise pin the executor until the connection RST'd.
    probe_timeout_s: int = 30


@dataclass
class DownloadResult:
    output_path: str
    title: str | None
    video_id: str | None
    uploader: str | None
    duration_s: int | None
    filesize_bytes: int | None


def _build_ydl_options(job, ctx: DownloadContext, throttle: ProgressThrottle) -> dict:
    def hook(d: dict) -> None:
        if ctx.cancel_flag():
            raise DownloadCancelled()
        if d.get("status") == "finished" or throttle.should_emit():
            ctx.on_progress(d)

    opts: dict = {
        "format": build_format_selector(job.format_pref),
        "outtmpl": build_output_template(
            job.output_dir, is_playlist_child=job.parent_job_id is not None
        ),
        "restrictfilenames": True,
        "noprogress": True,  # we use the hook, not the bar
        "quiet": True,
        "progress_hooks": [hook],
        "merge_output_format": "mp4",
        "concurrent_fragment_downloads": 4,
        # Treat ?v=X&list=Y as a single video with playlist context, not the
        # playlist itself. Pure playlist URLs (no ?v=) are unaffected.
        "noplaylist": True,
        # yt-dlp 2026.x ships challenge solver scripts (EJS) as opt-in remote
        # components. Without this, YouTube's n-challenge fails and no
        # video formats are returned ("Requested format is not available").
        # See https://github.com/yt-dlp/yt-dlp/wiki/EJS.
        "remote_components": ["ejs:github"],
        # Bound HTTP reads so a wedged socket can't park the worker thread
        # forever. yt-dlp's default leaves this unset, which is how a single
        # bad URL would otherwise eventually fill the executor pool.
        "socket_timeout": ctx.probe_timeout_s,
    }
    if ctx.cookies_browser:
        opts["cookiesfrombrowser"] = (ctx.cookies_browser,)
    # yt-dlp defaults to nooverwrites=True, so a retry of a DONE job whose
    # file is still on disk silently no-ops. The "Re-download" action sets
    # force_overwrite so yt-dlp re-fetches and replaces the file.
    #
    # Matches what `yt-dlp --force-overwrites` does at the CLI: both flip
    # `overwrites=True` AND `continuedl=False`. Without continuedl=False,
    # yt-dlp's "treat the complete file as already downloaded" path still
    # short-circuits the fetch even when overwrites is on.
    if getattr(job, "force_overwrite", False):
        opts["overwrites"] = True
        opts["continuedl"] = False
    if getattr(job, "subtitles", False):
        # Fetch real subtitles only — writeautomaticsub=True would pull the
        # machine-generated CC track, which is markedly lower quality.
        #
        # FFmpegEmbedSubtitle embeds the downloaded .vtt into the MP4 and,
        # by default, deletes the sidecar after embedding. The UI promises
        # both an embedded track AND a sidecar .vtt for Plex/Jellyfin
        # libraries, so set already_have_subtitle=True to keep the file
        # on disk (matches yt-dlp's `--embed-subs` CLI behavior when
        # subtitles are explicitly written).
        # Requires ffmpeg on PATH (which we already require for merging).
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = False
        opts["subtitleslangs"] = list(ctx.subtitle_langs) or ["en"]
        opts["postprocessors"] = [
            *opts.get("postprocessors", []),
            {"key": "FFmpegEmbedSubtitle", "already_have_subtitle": True},
        ]
    return opts


def probe(
    url: str,
    *,
    cookies_browser: str | None = None,
    socket_timeout: int = 30,
) -> dict:
    """Flat-extract metadata without downloading. Returns yt-dlp's info dict.

    Uses extract_flat='in_playlist' so playlist entries return as lightweight
    references rather than full per-entry metadata fetches.

    noplaylist=True follows yt-dlp's convention: a URL like ?v=X&list=Y is
    treated as a single video with playlist context, not as the playlist
    itself. Pure playlist URLs (?list=PLxxx with no ?v=) are still detected
    as playlists because there's no video to anchor to.

    ``socket_timeout`` bounds yt-dlp's HTTP reads. Without it, certain
    YouTube URLs (e.g. an unavailable video or an anti-bot challenge gone
    sideways) can hang the worker thread indefinitely. The caller wraps
    asyncio.to_thread in wait_for as a backstop in case yt-dlp ignores
    this knob on some code path.
    """
    from yt_dlp import YoutubeDL

    opts: dict = {
        "quiet": True,
        "extract_flat": "in_playlist",
        "skip_download": True,
        "noplaylist": True,
        "remote_components": ["ejs:github"],
        "socket_timeout": socket_timeout,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False, process=False)


def probe_one(
    url: str,
    *,
    cookies_browser: str | None = None,
    socket_timeout: int = 30,
) -> dict:
    """Full per-video metadata (title, duration, uploader, thumbnail).

    Slower than ``probe()`` — one HTTP fetch per call. Use for lazy
    enrichment after a flat preview, not for upfront playlist listing.

    Same ``socket_timeout`` semantics as ``probe()``.
    """
    from yt_dlp import YoutubeDL

    opts: dict = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "remote_components": ["ejs:github"],
        "socket_timeout": socket_timeout,
    }
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    with YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False, process=False)


def download(job, ctx: DownloadContext) -> DownloadResult:
    throttle = ProgressThrottle(interval_s=ctx.throttle_interval_s)
    opts = _build_ydl_options(job, ctx, throttle)
    with ctx.ydl_cls(opts) as ydl:
        info = ydl.extract_info(job.url, download=True)

    requested = info.get("requested_downloads") or []
    output_path = requested[0]["filepath"] if requested else info.get("filepath", "")
    return DownloadResult(
        output_path=output_path,
        title=info.get("title"),
        video_id=info.get("id"),
        uploader=info.get("uploader"),
        duration_s=int(info["duration"]) if info.get("duration") else None,
        filesize_bytes=info.get("filesize") or info.get("filesize_approx"),
    )
