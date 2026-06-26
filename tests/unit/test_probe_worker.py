"""Tests for ``ytdl._probe_worker``.

The worker runs as a separate process; monkeypatching can't cross the fork
boundary. We split the surface into two test families:

1. In-process tests for ``_run_probe(args_blob)`` and ``main(argv)`` —
   these patch ``yt_dlp.YoutubeDL`` and exercise the JSON / argv contract
   and error paths directly.
2. A subprocess smoke test that actually launches ``python -m
   ytdl._probe_worker`` with a deliberately malformed URL, asserting the
   pipeline (argv parsing, JSON-out-on-success, structured-error-on-fail,
   exit code mapping) wires up end-to-end.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

# ---- _run_probe (in-process) ----


def test_run_probe_builds_youtubedl_with_passed_opts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The opts dict from the args blob lands verbatim on YoutubeDL."""
    seen: dict = {}

    class FakeYDL:
        def __init__(self, opts: dict) -> None:
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict:
            seen["url"] = url
            seen["download"] = download
            return {"id": "x", "title": "ok"}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    from ytdl._probe_worker import _run_probe

    info = _run_probe(
        {
            "url": "https://yt/x",
            "opts": {"quiet": True, "noplaylist": True},
        }
    )
    assert info == {"id": "x", "title": "ok"}
    assert seen["opts"] == {"quiet": True, "noplaylist": True}
    assert seen["url"] == "https://yt/x"
    # Mirrors the in-process behavior pre-refactor: download=False.
    assert seen["download"] is False


def test_run_probe_coerces_cookies_list_to_tuple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """yt-dlp's cookiesfrombrowser knob requires a tuple. JSON only has
    lists, so the worker must coerce on the way in or yt-dlp rejects it."""
    seen: dict = {}

    class FakeYDL:
        def __init__(self, opts: dict) -> None:
            seen["opts"] = opts

        def __enter__(self):
            return self

        def __exit__(self, *_) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict:
            return {}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    from ytdl._probe_worker import _run_probe

    _run_probe(
        {
            "url": "https://yt/x",
            "opts": {"cookiesfrombrowser": ["chrome"]},
        }
    )
    assert seen["opts"]["cookiesfrombrowser"] == ("chrome",)


# ---- main(argv) ----


def test_main_writes_info_json_on_success(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Happy path: exit 0 with the info dict as JSON on stdout."""

    class FakeYDL:
        def __init__(self, opts: dict) -> None: ...
        def __enter__(self):
            return self

        def __exit__(self, *_) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict:
            return {"id": "abc", "title": "Hello"}

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    from ytdl._probe_worker import main

    args = json.dumps({"url": "https://yt/x", "opts": {}})
    rc = main(["ytdl._probe_worker", args])
    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out) == {"id": "abc", "title": "Hello"}
    assert captured.err == ""


def test_main_writes_yt_dlp_error_on_extract_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """When yt-dlp raises, exit 1 with a structured JSON error on STDOUT
    (stderr stays free-form for yt-dlp's own ERROR / WARNING text)."""

    class FakeYDL:
        def __init__(self, opts: dict) -> None: ...
        def __enter__(self):
            return self

        def __exit__(self, *_) -> bool:
            return False

        def extract_info(self, url: str, download: bool = True) -> dict:
            raise RuntimeError("Video unavailable")

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)

    from ytdl._probe_worker import main

    args = json.dumps({"url": "https://yt/dead", "opts": {}})
    rc = main(["ytdl._probe_worker", args])
    captured = capsys.readouterr()
    assert rc == 1
    payload = json.loads(captured.out)
    assert payload == {"error": "Video unavailable", "type": "yt_dlp_error"}


def test_main_rejects_missing_argv(capsys: pytest.CaptureFixture[str]) -> None:
    """Wrong argv shape -> exit 2 with usage_error."""
    from ytdl._probe_worker import main

    rc = main(["ytdl._probe_worker"])
    captured = capsys.readouterr()
    assert rc == 2
    # Structured payload is on stdout, not stderr — keeps yt-dlp's
    # free-form output on the error path from corrupting the JSON.
    payload = json.loads(captured.out)
    assert payload["type"] == "usage_error"


def test_main_rejects_bad_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Non-JSON argv -> exit 2 with usage_error and a helpful message."""
    from ytdl._probe_worker import main

    rc = main(["ytdl._probe_worker", "{not json"])
    captured = capsys.readouterr()
    assert rc == 2
    # Structured payload is on stdout, not stderr — keeps yt-dlp's
    # free-form output on the error path from corrupting the JSON.
    payload = json.loads(captured.out)
    assert payload["type"] == "usage_error"
    assert "invalid JSON" in payload["error"]


def test_main_rejects_args_missing_url(capsys: pytest.CaptureFixture[str]) -> None:
    """Args blob without a `url` field -> exit 2."""
    from ytdl._probe_worker import main

    rc = main(["ytdl._probe_worker", json.dumps({"opts": {}})])
    captured = capsys.readouterr()
    assert rc == 2
    # Structured payload is on stdout, not stderr — keeps yt-dlp's
    # free-form output on the error path from corrupting the JSON.
    payload = json.loads(captured.out)
    assert payload["type"] == "usage_error"


# ---- subprocess smoke tests ----


def test_subprocess_pipeline_round_trip_success() -> None:
    """End-to-end: launch the real subprocess against a `data:` URL.
    yt-dlp's generic extractor accepts these without touching the network
    and returns a real info dict, so we can exercise the success path of
    the full pipeline (argv parsing -> yt-dlp -> JSON-out-on-stdout ->
    exit 0).
    """
    args = json.dumps(
        {
            "url": "data:text/plain;base64,QQ==",
            "opts": {"quiet": True, "skip_download": True, "socket_timeout": 5},
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "ytdl._probe_worker", args],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, (
        f"expected exit 0, got rc={result.returncode}\n"
        f"stdout={result.stdout!r}\nstderr={result.stderr!r}"
    )
    info = json.loads(result.stdout)
    # Generic extractor returns at least an id/url for any data: URL.
    assert info.get("url") == "data:text/plain;base64,QQ=="


def test_subprocess_pipeline_usage_error_on_bad_argv() -> None:
    """Calling the worker with no JSON argument must hit the usage_error
    path and exit 2 — exercises the argv contract end-to-end. The
    structured payload goes to stdout (not stderr) so it can't be
    corrupted by yt-dlp's own ERROR / WARNING text on the failure path.
    """
    result = subprocess.run(
        [sys.executable, "-m", "ytdl._probe_worker"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout)
    assert payload["type"] == "usage_error"
