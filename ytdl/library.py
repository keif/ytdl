"""Duplicate detection: scan directories, index by YouTube video ID.

The queue's default output template writes files as
``{title} [{video_id}].{ext}`` — same shape yt-dlp CLI users typically get.
We exploit that convention to catch duplicates without needing to hash
files or maintain a sidecar manifest.

Scans populate the ``library_files`` table; ``lookup_by_video_id`` is what
``/preview`` and ``/jobs`` call to detect duplicates before enqueueing.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

# The 11-char YouTube video ID lives in ``[<id>].<ext>`` right before the
# extension. Anchoring the ``]`` to ``.<ext>$`` (rather than accepting
# ``[id]`` anywhere in the name) avoids matching bracketed metadata that a
# user might have added mid-title. yt-dlp's own default template puts the
# id in this exact position.
_FILENAME_ID_RE = re.compile(r"\[([\w-]{11})\](?:\.[^.]+)$")


def extract_video_id_from_url(url: str) -> str | None:
    """Return the 11-char YouTube video ID from a canonical watch/short URL.

    Supports:
      - ``https://www.youtube.com/watch?v=<id>`` (with or without extra
        query params like ``list=``, ``t=``, ``feature=``)
      - ``https://youtu.be/<id>`` (short link — id is the first path
        segment)
      - ``https://www.youtube.com/shorts/<id>``
      - ``https://www.youtube.com/embed/<id>``

    Returns ``None`` when the URL doesn't match any of those shapes so
    callers can fall back to running a probe. We deliberately do NOT try
    to match every possible YouTube-adjacent format — better to bail and
    let the probe path pick up the id than guess wrong and false-positive
    on some cousin URL.
    """
    if not url:
        return None
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.netloc or "").lower()
    # Strip a leading ``www.`` / ``m.`` / ``music.`` so we hit the same
    # branches whether the user pasted the mobile or desktop URL.
    for prefix in ("www.", "m.", "music."):
        if host.startswith(prefix):
            host = host[len(prefix) :]
            break
    path = parsed.path or ""

    if host == "youtu.be":
        # Short link: first path segment is the id.
        segment = path.lstrip("/").split("/", 1)[0]
        return segment if _is_valid_video_id(segment) else None

    if host in ("youtube.com", "youtube-nocookie.com"):
        if path == "/watch" or path.startswith("/watch"):
            qs = parse_qs(parsed.query)
            v = (qs.get("v") or [None])[0]
            return v if v and _is_valid_video_id(v) else None
        # /shorts/<id> and /embed/<id> share the same "first path segment
        # after the prefix" shape.
        for prefix in ("/shorts/", "/embed/", "/live/"):
            if path.startswith(prefix):
                segment = path[len(prefix) :].split("/", 1)[0]
                return segment if _is_valid_video_id(segment) else None
    return None


def extract_video_id_from_filename(filename: str) -> str | None:
    """Return the video ID embedded in ``[<id>].<ext>`` at end of filename.

    Handles the whole path or just the basename — we run the regex against
    the string as-is because the anchor requires the ext to be at the end,
    which naturally strips directory noise.
    """
    if not filename:
        return None
    # Match on basename so a bracket-containing directory can't confuse us.
    base = Path(filename).name
    m = _FILENAME_ID_RE.search(base)
    return m.group(1) if m else None


def _is_valid_video_id(candidate: str) -> bool:
    """Fast shape check: YouTube video IDs are exactly 11 chars from
    [A-Za-z0-9_-]. Used as a guard in URL parsing so we don't emit a
    non-id string just because the shape looked promising.
    """
    if len(candidate) != 11:
        return False
    return all(c.isalnum() or c in ("_", "-") for c in candidate)


def scan_directories(
    conn: sqlite3.Connection, dirs: list[str]
) -> tuple[int, list[str], float]:
    """Walk ``dirs`` recursively and index every file matching the
    ``[<video_id>].<ext>`` pattern into ``library_files``.

    Returns ``(count, scanned_dirs_absolute, elapsed_seconds)``:
      - ``count``: number of unique video_ids present in the table AFTER
        this scan.
      - ``scanned_dirs_absolute``: the resolved absolute paths that were
        actually walked (dirs that don't exist are dropped and logged).
      - ``elapsed_seconds``: wall-clock time for the walk + inserts.

    Idempotent — an existing row's ``path``/``title``/``filesize_bytes``
    are updated if the file has moved between scans. ``scanned_at`` on
    every touched row is bumped to now so operators can tell fresh rows
    from stale ones during triage.

    Duplicate video IDs across dirs collapse onto a single row (the LAST
    one wins). This is the correct behavior for the dedup use case: the
    user only cares that SOME copy exists, not which mirror.
    """
    start = time.time()
    resolved: list[str] = []
    seen: dict[str, tuple[str, str | None, int | None]] = {}

    for raw in dirs:
        try:
            p = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            log.warning("library: skipping unresolvable dir %r", raw)
            continue
        if not p.exists() or not p.is_dir():
            log.info("library: skipping missing dir %s", p)
            continue
        resolved.append(str(p))
        # rglob("*") walks the entire tree. Symlinks: follow=False by
        # default, which is what we want — a symlink loop under output_dir
        # would hang the scan otherwise.
        for entry in p.rglob("*"):
            try:
                if not entry.is_file():
                    continue
            except OSError:
                # Broken symlinks etc. — skip quietly.
                continue
            vid = extract_video_id_from_filename(entry.name)
            if vid is None:
                continue
            # Title: everything before " [<id>]" — a best-effort recovery
            # of what yt-dlp originally wrote. Never used for anything
            # load-bearing, just surfaced in the UI banner.
            stem = entry.stem  # filename minus final extension
            bracket_idx = stem.rfind(f"[{vid}]")
            if bracket_idx > 0:
                # Strip trailing whitespace before the bracket ("Foo [id]"
                # -> "Foo"). Handles the " " that our default template
                # emits between title and bracket.
                title = stem[:bracket_idx].rstrip(" ")
            else:
                title = None
            try:
                size = entry.stat().st_size
            except OSError:
                size = None
            seen[vid] = (str(entry), title or None, size)

    now_ms = int(time.time() * 1000)
    # Wrap the writes so a mid-scan interrupt doesn't leave the table
    # half-populated. ``INSERT OR REPLACE`` idempotency covers rescans
    # after moves.
    conn.execute("BEGIN IMMEDIATE")
    try:
        for vid, (path, title, size) in seen.items():
            conn.execute(
                """
                INSERT INTO library_files(video_id, path, title, filesize_bytes, scanned_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    path = excluded.path,
                    title = excluded.title,
                    filesize_bytes = excluded.filesize_bytes,
                    scanned_at = excluded.scanned_at
                """,
                (vid, path, title, size, now_ms),
            )
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise

    row = conn.execute("SELECT COUNT(*) AS n FROM library_files").fetchone()
    count = int(row["n"] if isinstance(row, sqlite3.Row) else row[0])
    elapsed = time.time() - start
    return count, resolved, elapsed


def lookup_by_video_id(
    conn: sqlite3.Connection, video_id: str
) -> dict | None:
    """Return the stored metadata for a video ID, or None when not indexed.

    Response shape:
      ``{"path": str, "title": str | None, "filesize_bytes": int | None}``

    Callers should treat a hit as "an existing file plausibly matches this
    URL"; we don't re-stat the file here because the caller often just
    wants to warn the user, not re-verify. If the file has since been
    deleted, the next rescan (or job-completion write) will refresh the
    row.
    """
    row = conn.execute(
        "SELECT path, title, filesize_bytes FROM library_files WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    return {
        "path": row["path"],
        "title": row["title"],
        "filesize_bytes": row["filesize_bytes"],
    }


def record_downloaded(
    conn: sqlite3.Connection,
    video_id: str,
    path: str,
    title: str | None,
    filesize_bytes: int | None,
) -> None:
    """Insert-or-update the library row for a freshly downloaded file.

    Called from the worker when a job's terminal status becomes DONE. Keeps
    the dedup index warm without waiting for the next full rescan — a user
    who just downloaded X and immediately queues X again should get the
    409, not have to wait until server restart.
    """
    if not video_id:
        return
    conn.execute(
        """
        INSERT INTO library_files(video_id, path, title, filesize_bytes, scanned_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            path = excluded.path,
            title = COALESCE(excluded.title, title),
            filesize_bytes = COALESCE(excluded.filesize_bytes, filesize_bytes),
            scanned_at = excluded.scanned_at
        """,
        (video_id, path, title, filesize_bytes, int(time.time() * 1000)),
    )
