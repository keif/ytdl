"""Subprocess entry point for yt-dlp probes.

`probe()` and `probe_one()` in ``ytdl.downloader`` shell out to this module so
the OS can actually kill the work when a probe wedges. yt-dlp's network calls
land in C extensions that don't respond to signals, so an in-process probe
that hangs leaves a stuck thread the asyncio.wait_for backstop can't cancel.
Moving the work to a subprocess turns the timeout into an OS-level SIGKILL —
no executor leak, no stuck thread, no signal-handler reentrancy.

CLI contract:

    python -m ytdl._probe_worker '<json args>'

where the args blob is::

    {
        "url": "https://...",
        "opts": {
            "quiet": true,
            "skip_download": true,
            ...
            "cookiesfrombrowser": ["chrome"]
        }
    }

``cookiesfrombrowser`` is a list/tuple in JSON; we coerce to tuple before
handing to yt-dlp (which requires the tuple form).

Streams:

* ``stdout`` — structured JSON. On success, the info dict; on error,
  ``{"error": "<message>", "type": "<error_type>"}``. The exit code
  disambiguates which.
* ``stderr`` — free-form, left to yt-dlp's own ERROR / WARNING output
  and any Python traceback. Captured by the caller for log diagnostics
  but never parsed as JSON.

Exit codes:

* 0 — success. ``stdout`` contains the JSON-encoded info dict.
* 1 — yt-dlp raised. ``stdout`` contains the structured error.
* 2 — usage error (bad argv shape, JSON parse failure, missing fields).
       ``stdout`` contains the structured error.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _run_probe(args_blob: dict[str, Any]) -> dict[str, Any]:
    """Build a YoutubeDL with ``opts`` and extract info for ``url``.

    Factored out of ``main()`` so the happy path can be unit-tested in-process
    (monkeypatching ``yt_dlp.YoutubeDL`` doesn't cross subprocess boundaries).
    """
    from yt_dlp import YoutubeDL

    url = args_blob["url"]
    opts = dict(args_blob.get("opts") or {})
    # JSON has no tuple type; yt-dlp's cookiesfrombrowser knob requires one.
    cfb = opts.get("cookiesfrombrowser")
    if isinstance(cfb, list):
        opts["cookiesfrombrowser"] = tuple(cfb)

    with YoutubeDL(opts) as ydl:
        # process=True (yt-dlp's default) is required for `noplaylist=False`
        # to take effect on hybrid `?v=X&list=...` URLs. Mirrors the in-
        # process probe() behavior the subprocess replaced.
        return ydl.extract_info(url, download=False)


def _emit_error(error_type: str, message: str) -> None:
    """Write the structured error to STDOUT, not stderr.

    yt-dlp writes its own ERROR / WARNING text to stderr (for example
    'ERROR: [youtube] xxx: Video unavailable'). Mixing our structured
    JSON with yt-dlp's free-form output makes the caller's json.loads
    fail and the user sees a wall of mixed text instead of the clean
    error string. Keep the streams separate: stdout = structured
    payload (machine-readable), stderr = yt-dlp's noise (diagnostic
    capture for logs).
    """
    payload = {"error": message, "type": error_type}
    sys.stdout.write(json.dumps(payload))
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        _emit_error(
            "usage_error",
            "expected exactly one JSON argument: python -m ytdl._probe_worker '<json>'",
        )
        return 2
    try:
        args_blob = json.loads(argv[1])
    except json.JSONDecodeError as exc:
        _emit_error("usage_error", f"invalid JSON args: {exc}")
        return 2
    if not isinstance(args_blob, dict) or "url" not in args_blob:
        _emit_error("usage_error", "args must be a JSON object with a 'url' field")
        return 2

    try:
        info = _run_probe(args_blob)
    except BaseException as exc:
        _emit_error("yt_dlp_error", str(exc))
        return 1

    # default=str collapses datetimes / other non-JSON-native scalars that
    # yt-dlp occasionally bakes into the info dict.
    sys.stdout.write(json.dumps(info, default=str))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
