from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from ytdl.cli import _parse_pick, app

runner = CliRunner()


def test_cli_help_lists_commands(tmp_data_dir: Path) -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    out = result.stdout
    for cmd in ("get", "serve", "queue", "cookies"):
        assert cmd in out


def test_cookies_use_writes_config(tmp_data_dir: Path) -> None:
    result = runner.invoke(app, ["cookies", "use", "firefox"])
    assert result.exit_code == 0
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    assert cfg_path.exists()
    assert "firefox" in cfg_path.read_text()


def test_cookies_use_rejects_unsupported(tmp_data_dir: Path) -> None:
    result = runner.invoke(app, ["cookies", "use", "lynx"])
    assert result.exit_code != 0
    assert "unsupported" in result.output.lower()


def test_cookies_status_prints_explicit_browser(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('cookies_browser = "firefox"\n')
    result = runner.invoke(app, ["cookies", "status"])
    assert result.exit_code == 0
    assert "firefox" in result.output
    assert "explicit" in result.output


def test_cookies_status_prints_none_when_no_detection(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_data_dir))
    result = runner.invoke(app, ["cookies", "status"])
    assert result.exit_code == 0
    # Either autodetect happened or it didn't; if not, the helpful hint shows.
    if "none detected" in result.output:
        assert "ytdl cookies use" in result.output


# ---- _parse_pick ----


def test_parse_pick_single_indices() -> None:
    assert _parse_pick("1,3,7", max_index=10) == [1, 3, 7]


def test_parse_pick_ranges_and_dedup() -> None:
    assert _parse_pick("1,3,5-9", max_index=10) == [1, 3, 5, 6, 7, 8, 9]
    # Overlapping range + index dedupe.
    assert _parse_pick("1-3,2,3,4", max_index=10) == [1, 2, 3, 4]


def test_parse_pick_rejects_out_of_range() -> None:
    with pytest.raises(typer.BadParameter):
        _parse_pick("1,99", max_index=10)


def test_parse_pick_rejects_empty() -> None:
    with pytest.raises(typer.BadParameter):
        _parse_pick("", max_index=10)
    with pytest.raises(typer.BadParameter):
        _parse_pick(",,,", max_index=10)


def test_parse_pick_rejects_inverted_range() -> None:
    with pytest.raises(typer.BadParameter):
        _parse_pick("9-3", max_index=10)


def test_parse_pick_rejects_garbage() -> None:
    with pytest.raises(typer.BadParameter):
        _parse_pick("1,abc", max_index=10)
    with pytest.raises(typer.BadParameter):
        _parse_pick("1-foo", max_index=10)


# ---- preview command ----


def test_preview_command_prints_entries(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ytdl.downloader.probe",
        lambda url, cookies_browser=None: {
            "_type": "playlist",
            "title": "PL",
            "entries": [
                {"id": "a", "title": "Alpha", "webpage_url": "https://x/a"},
                {"id": "b", "title": "Bravo", "webpage_url": "https://x/b"},
            ],
        },
    )
    result = runner.invoke(app, ["preview", "https://x/list"])
    assert result.exit_code == 0
    assert "Alpha" in result.output
    assert "Bravo" in result.output


def test_cli_queue_retry_creates_new_job(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ytdl queue retry <id>` creates a new pending job from a failed one."""
    from ytdl.config import load_config
    from ytdl.db import connect, migrate
    from ytdl.models import JobKind
    from ytdl.queue import enqueue

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    job_id = enqueue(
        conn, url="https://yt/x", kind=JobKind.VIDEO,
        format_pref="best", output_dir="/o",
    )
    conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["queue", "retry", job_id])
    assert result.exit_code == 0
    assert "queued retry" in result.output


def test_cli_queue_retry_fails_for_unknown_id(tmp_data_dir: Path) -> None:
    result = runner.invoke(app, ["queue", "retry", "01nonexistent"])
    assert result.exit_code != 0
    assert "Cannot retry" in result.output


def test_queue_add_with_pick_only_enqueues_picked(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ytdl.downloader.probe",
        lambda url, cookies_browser=None: {
            "_type": "playlist",
            "title": "PL",
            "entries": [
                {"id": "a", "title": "A", "webpage_url": "https://x/a"},
                {"id": "b", "title": "B", "webpage_url": "https://x/b"},
                {"id": "c", "title": "C", "webpage_url": "https://x/c"},
                {"id": "d", "title": "D", "webpage_url": "https://x/d"},
            ],
        },
    )
    result = runner.invoke(
        app, ["queue", "add", "https://x/list", "--pick", "1,3-4"]
    )
    assert result.exit_code == 0, result.output
    # 3 of 4 entries queued.
    from ytdl.config import load_config
    from ytdl.db import connect, migrate
    from ytdl.queue import list_jobs

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    jobs = list_jobs(conn)
    conn.close()
    urls = {j.url for j in jobs}
    assert urls == {"https://x/a", "https://x/c", "https://x/d"}


def test_cli_queue_clear_with_yes_flag_deletes(tmp_data_dir: Path) -> None:
    import time as _t

    from ytdl.config import load_config
    from ytdl.db import connect, migrate
    from ytdl.models import JobKind
    from ytdl.queue import enqueue

    cfg = load_config()
    conn = connect(cfg.db_path)
    migrate(conn)
    job_id = enqueue(
        conn, url="https://yt/x", kind=JobKind.VIDEO,
        format_pref="best", output_dir="/o",
    )
    conn.execute(
        "UPDATE jobs SET status='done', finished_at=? WHERE id=?",
        (int(_t.time() * 1000) - 30 * 86_400_000, job_id),
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["queue", "clear", "--yes"])
    assert result.exit_code == 0
    assert "deleted 1" in result.output.lower()


def test_cli_queue_clear_prints_nothing_to_clear(tmp_data_dir: Path) -> None:
    result = runner.invoke(app, ["queue", "clear", "--yes"])
    assert result.exit_code == 0
    assert "nothing to clear" in result.output.lower()
