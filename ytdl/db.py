"""SQLite connection + schema migrations.

We use stdlib `sqlite3` directly (not SQLModel) for the queue — the queries are
simple and we want full control of transactions and the CAS UPDATE pattern.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 4

_MIGRATIONS: dict[int, list[str]] = {
    1: [
        """
        CREATE TABLE IF NOT EXISTS jobs (
            id              TEXT PRIMARY KEY,
            url             TEXT NOT NULL,
            kind            TEXT NOT NULL,
            parent_job_id   TEXT REFERENCES jobs(id) ON DELETE SET NULL,
            status          TEXT NOT NULL,
            format_pref     TEXT NOT NULL,
            output_dir      TEXT NOT NULL,
            output_path     TEXT,
            title           TEXT,
            video_id        TEXT,
            uploader        TEXT,
            duration_s      INTEGER,
            filesize_bytes  INTEGER,
            bytes_done      INTEGER,
            speed_bps       INTEGER,
            eta_s           INTEGER,
            error           TEXT,
            attempts        INTEGER NOT NULL DEFAULT 0,
            created_at      INTEGER NOT NULL,
            started_at      INTEGER,
            finished_at     INTEGER
        )
        """,
        "CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at)",
        "CREATE INDEX IF NOT EXISTS jobs_parent ON jobs(parent_job_id)",
        """
        CREATE TABLE IF NOT EXISTS events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id          TEXT NOT NULL,
            kind            TEXT NOT NULL,
            payload_json    TEXT NOT NULL,
            created_at      INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS events_job_id ON events(job_id)",
    ],
    2: [
        # Flag to force yt-dlp to overwrite an existing output file. The
        # default behavior (nooverwrites=True) silently skips re-downloads
        # when the file is already on disk — useful for resumes, surprising
        # when the previous file was corrupt. The "Re-download" action sets
        # this on the cloned job.
        "ALTER TABLE jobs ADD COLUMN force_overwrite INTEGER NOT NULL DEFAULT 0",
    ],
    3: [
        # Per-job opt-in subtitle download. When set, the worker tells yt-dlp
        # to fetch real subtitles (writesubtitles=True; writeautomaticsub=False
        # so we don't pull the low-quality auto-CC track), embed them in the
        # MP4, and also save a sidecar .vtt the user's media library can pick
        # up. Defaults to 0 so existing rows stay opt-out.
        "ALTER TABLE jobs ADD COLUMN subtitles INTEGER NOT NULL DEFAULT 0",
    ],
    4: [
        # Duplicate-detection index. Rows here point at files already on
        # disk that carry a yt-dlp-style ``[<video_id>].<ext>`` suffix in
        # their filename. Populated on startup + when jobs finish + via
        # POST /library/rescan. The /preview + /jobs endpoints consult it
        # to warn the user before queuing something they already have.
        #
        # video_id is PRIMARY KEY: the same file (moved between scans) is
        # idempotently updated on rescan rather than duplicated. The
        # separate index is redundant with the PK but keeps future queries
        # that don't hit the PK path fast.
        """
        CREATE TABLE IF NOT EXISTS library_files (
            video_id        TEXT PRIMARY KEY,
            path            TEXT NOT NULL,
            title           TEXT,
            filesize_bytes  INTEGER,
            scanned_at      INTEGER NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS library_files_video_id ON library_files(video_id)",
    ],
}


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,  # autocommit; we manage txns explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _current_version(conn: sqlite3.Connection) -> int:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
    return int(row[0]) if row else 0


def _set_version(conn: sqlite3.Connection, v: int) -> None:
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version(version) VALUES (?)", (v,))


def migrate(conn: sqlite3.Connection) -> None:
    current = _current_version(conn)
    for v in sorted(_MIGRATIONS):
        if v <= current:
            continue
        for stmt in _MIGRATIONS[v]:
            conn.execute(stmt)
        _set_version(conn, v)
