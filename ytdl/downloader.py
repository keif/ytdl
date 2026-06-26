"""yt-dlp wrapper.

This module is split into two halves:
  - pure helpers (format selector, output template, error classifier,
    progress throttle) — tested in isolation
  - `download(job, ctx)` — calls into yt-dlp, wired in the next task
"""
from __future__ import annotations

import errno
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs, urlparse


def _url_targets_a_playlist(url: str) -> bool:
    """Return True if the URL carries a `list=` parameter — any list,
    real playlist or radio mix.

    Earlier versions of this function distinguished RD-prefixed (radio
    mix) lists from PL/OL/etc. (user-curated) lists, treating radio
    mixes as single-video downloads to protect against YouTube's
    auto-redirect from `?v=X` to `?v=X&list=RDX`. In practice, users
    who deliberately grab a radio URL want the mix, and the auto-
    redirect case is recoverable (paste the bare `?v=X` instead).
    Letting yt-dlp probe whichever list the URL targets is the
    less-surprising default; the picker shows the entries and the
    user picks what they want.

    Returns False for URLs with no `list=` parameter so plain video
    URLs (and multifeed/multicamera videos that yt-dlp would otherwise
    expand into a "playlist" of feeds) stay single-video.
    """
    try:
        qs = parse_qs(urlparse(url).query)
    except Exception:
        return False
    return bool(qs.get("list"))


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
        # Playlist children carry single-video URLs (the worker extracted
        # them via probe), so noplaylist=True is correct — yt-dlp must not
        # re-expand them. For top-level jobs, any URL with a `list=`
        # parameter expands (real playlist OR radio mix); URLs without a
        # list stay single-video so multifeed/multicamera videos don't
        # get treated as a playlist of feeds. Without this branch the
        # CLI's `ytdl get` (which bypasses the queue) would download
        # only the single video from a hybrid playlist URL.
        "noplaylist": (
            True
            if job.parent_job_id is not None
            else not _url_targets_a_playlist(job.url)
        ),
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


# Grace beyond yt-dlp's socket_timeout before subprocess.run pulls the
# trigger. Covers the case where yt-dlp ignores socket_timeout on some
# code path (e.g. a hang inside a C extension that doesn't honor the
# Python-level deadline). Five seconds is enough for graceful HTTP
# teardown without making genuine slow probes appear stuck.
_SUBPROCESS_GRACE_S = 5


def _build_probe_opts(
    url: str,
    cookies_browser: str | None,
    socket_timeout: int,
    *,
    flat: bool,
    use_playlist: bool,
) -> dict:
    """Construct the YoutubeDL opts dict shared by probe() and probe_one().

    ``flat`` selects extract_flat='in_playlist' (cheap entry stubs for
    upfront listing) versus the full per-video fetch used by enrichment.
    ``use_playlist`` is the inverse of yt-dlp's noplaylist knob.

    cookiesfrombrowser is a list here (not a tuple) so it survives JSON
    serialization to the subprocess; the worker coerces back to tuple
    before handing to yt-dlp.
    """
    opts: dict = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": not use_playlist,
        "remote_components": ["ejs:github"],
        "socket_timeout": socket_timeout,
    }
    if flat:
        opts["extract_flat"] = "in_playlist"
    if cookies_browser:
        opts["cookiesfrombrowser"] = [cookies_browser]
    return opts


def _run_probe_subprocess(url: str, opts: dict, socket_timeout: int) -> dict:
    """Run the probe worker as a subprocess and return the info dict.

    Layered timeouts:

    1. yt-dlp's ``socket_timeout`` aborts a hung HTTP read.
    2. ``subprocess.run(timeout=socket_timeout + grace)`` is the backstop —
       when this fires, the OS kills the child process, releasing the
       executor thread immediately (the bug that ``asyncio.wait_for``
       could not solve in-process).

    Re-raises ``subprocess.TimeoutExpired`` as ``TimeoutError`` so callers
    can keep using ``except TimeoutError`` symmetric with the asyncio.
    """
    args_blob = json.dumps({"url": url, "opts": opts})
    try:
        result = subprocess.run(
            [sys.executable, "-m", "ytdl._probe_worker", args_blob],
            timeout=socket_timeout + _SUBPROCESS_GRACE_S,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError(
            f"probe timed out after {socket_timeout + _SUBPROCESS_GRACE_S}s"
        ) from exc

    if result.returncode != 0:
        # The worker writes the structured JSON payload to STDOUT on the
        # error path (stderr stays free-form for yt-dlp's own ERROR /
        # WARNING text, which would otherwise corrupt the payload). Fall
        # back to stderr only when stdout is unparseable — for example
        # an interpreter crash that exited before _emit_error ran.
        message: str | None = None
        if result.stdout:
            try:
                payload = json.loads(result.stdout)
                if isinstance(payload, dict):
                    message = payload.get("error")
            except json.JSONDecodeError:
                pass
        if not message and result.stderr:
            message = result.stderr.strip()
        raise RuntimeError(message or "probe failed")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        # Worker exited zero but stdout wasn't JSON — should never happen
        # in practice, but raising explicitly beats letting the caller
        # crash on a missing key.
        raise RuntimeError(f"probe returned malformed JSON: {exc}") from exc


def probe(
    url: str,
    *,
    cookies_browser: str | None = None,
    socket_timeout: int = 30,
) -> dict:
    """Flat-extract metadata without downloading. Returns yt-dlp's info dict.

    Uses extract_flat='in_playlist' so playlist entries return as lightweight
    references rather than full per-entry metadata fetches.

    `noplaylist` is True by default — yt-dlp uses it as a discriminator
    for multifeed/multicamera videos that return as "playlists" of
    feeds, and for plain `?v=X` URLs we want to keep single-video. Any
    URL with a `list=` parameter (real playlist or radio mix) gets
    `noplaylist=False` so the probe returns the playlist info and the
    worker / UI offers the picker. See `_url_targets_a_playlist`.

    ``socket_timeout`` bounds yt-dlp's HTTP reads. The call runs in a
    subprocess (see ``ytdl._probe_worker``) so ``subprocess.run``'s own
    timeout — set to ``socket_timeout + 5`` — can OS-kill yt-dlp if it
    ignores the socket-level deadline. This is the fix for the leaked
    executor threads described in PR #46.
    """
    opts = _build_probe_opts(
        url,
        cookies_browser,
        socket_timeout,
        flat=True,
        use_playlist=_url_targets_a_playlist(url),
    )
    return _run_probe_subprocess(url, opts, socket_timeout)


def probe_one(
    url: str,
    *,
    cookies_browser: str | None = None,
    socket_timeout: int = 30,
) -> dict:
    """Full per-video metadata (title, duration, uploader, thumbnail).

    Slower than ``probe()`` — one HTTP fetch per call. Use for lazy
    enrichment after a flat preview, not for upfront playlist listing.

    Same subprocess + ``socket_timeout`` semantics as ``probe()``.
    """
    opts = _build_probe_opts(
        url,
        cookies_browser,
        socket_timeout,
        flat=False,
        use_playlist=False,
    )
    return _run_probe_subprocess(url, opts, socket_timeout)


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
