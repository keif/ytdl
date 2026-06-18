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
