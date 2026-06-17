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
from dataclasses import dataclass
from enum import StrEnum


class Classification(StrEnum):
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AUTH_REQUIRED = "auth_required"
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
_GEO_PATTERNS = (
    re.compile(r"geo restricted", re.I),
    re.compile(r"not available in your country", re.I),
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
