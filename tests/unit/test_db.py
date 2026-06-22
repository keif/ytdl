from __future__ import annotations

from pathlib import Path

from ytdl.db import connect, migrate


def test_migrate_creates_tables(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    migrate(conn)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    names = {r[0] for r in rows}
    assert "jobs" in names
    assert "events" in names
    assert "schema_version" in names


def test_migrate_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    migrate(conn)
    migrate(conn)  # should not raise


def test_indexes_exist(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    migrate(conn)
    idx = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ).fetchall()}
    assert "jobs_status_created" in idx
    assert "jobs_parent" in idx
    assert "events_job_id" in idx


def test_pragmas_set(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_migrate_adds_force_overwrite_column(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    migrate(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
    assert "force_overwrite" in cols


def test_migrate_v1_to_v2_preserves_existing_rows(tmp_path: Path) -> None:
    """A DB at schema v1 with rows must migrate to v2 without losing them.
    The new column should default to 0 (False)."""
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    # Set up at v1 by hand — recreate the v1-shaped table and pin the
    # version, then call migrate() to walk forward.
    conn.execute("DROP TABLE IF EXISTS jobs")
    conn.execute(
        """
        CREATE TABLE jobs (
            id TEXT PRIMARY KEY, url TEXT NOT NULL, kind TEXT NOT NULL,
            parent_job_id TEXT, status TEXT NOT NULL, format_pref TEXT NOT NULL,
            output_dir TEXT NOT NULL, output_path TEXT, title TEXT,
            video_id TEXT, uploader TEXT, duration_s INTEGER,
            filesize_bytes INTEGER, bytes_done INTEGER, speed_bps INTEGER,
            eta_s INTEGER, error TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER
        )
        """
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    conn.execute("DELETE FROM schema_version")
    conn.execute("INSERT INTO schema_version(version) VALUES (1)")
    conn.execute(
        "INSERT INTO jobs(id, url, kind, status, format_pref, output_dir, created_at) "
        "VALUES ('x', 'u', 'video', 'done', 'best', '/o', 1)"
    )
    migrate(conn)
    row = conn.execute(
        "SELECT id, force_overwrite FROM jobs WHERE id='x'"
    ).fetchone()
    assert row["id"] == "x"
    assert row["force_overwrite"] == 0
