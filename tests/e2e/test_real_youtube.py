"""Real-network E2E. Opt-in: RUN_E2E=1.

Downloads a small public Creative Commons clip and asserts the file looks like
a valid container. Skipped by default so CI stays green when YouTube changes.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="RUN_E2E=1 not set",
)


def test_download_short_cc_clip(tmp_path: Path) -> None:
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH")

    from ytdl.downloader import DownloadContext, download
    from ytdl.models import Job, JobKind, JobStatus
    from ytdl.ulid import new_ulid
    from yt_dlp import YoutubeDL

    job = Job(
        id=new_ulid(),
        url="https://www.youtube.com/watch?v=jNQXAC9IVRw",  # the very first YouTube video; CC by author
        kind=JobKind.VIDEO,
        parent_job_id=None,
        status=JobStatus.RUNNING,
        format_pref="best",
        output_dir=str(tmp_path),
    )
    ctx = DownloadContext(
        ydl_cls=YoutubeDL,
        cookies_browser=None,
        on_progress=lambda d: None,
        cancel_flag=lambda: False,
        throttle_interval_s=0.0,
    )
    result = download(job, ctx)
    assert Path(result.output_path).exists()
    assert Path(result.output_path).stat().st_size > 0

    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", result.output_path],
        text=True,
    )
    assert "codec_type=video" in out
