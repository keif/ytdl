"""Runtime probes for binaries we care about.

ytdl shells out to / runs alongside a couple of tools that aren't Python
packages: ``deno`` (yt-dlp's JS runtime for the YouTube n-challenge) and
``ffmpeg`` (used to merge separate audio/video streams). When either is
missing on the host, downloads start failing with confusing errors. The
``/status`` endpoint surfaces this presence info so the web UI can render a
proactive diagnostic chip and the user can install what's missing before
they trip the failure.

Detection is intentionally shallow: ``shutil.which`` only. Don't probe
versions or features — yt-dlp is the source of truth at job time, this is
just "did we find the binary on PATH?".
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class BinaryStatus:
    """Presence info for a runtime binary."""

    name: str
    present: bool
    path: str | None


def probe_binary(name: str) -> BinaryStatus:
    """Look up ``name`` on PATH and report whether it's present."""
    located = shutil.which(name)
    return BinaryStatus(name=name, present=located is not None, path=located)


def probe_deno() -> BinaryStatus:
    """yt-dlp uses deno to evaluate YouTube's challenge-solver script. Without
    it, the n-challenge fails and downloads come back with no usable formats.
    """
    return probe_binary("deno")


def probe_ffmpeg() -> BinaryStatus:
    """yt-dlp uses ffmpeg to merge separate audio + video streams into one
    MP4. Without it, format selectors that yield separate streams fail at
    merge time.
    """
    return probe_binary("ffmpeg")
