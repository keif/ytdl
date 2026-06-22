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


def test_cli_queue_clear_rejects_negative_days(tmp_data_dir: Path) -> None:
    result = runner.invoke(app, ["queue", "clear", "--older-than-days", "-1", "--yes"])
    assert result.exit_code == 2
    assert "must be >= 0" in result.output


def test_warn_if_web_bundle_missing_prints_when_absent(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When ytdl/web/index.html doesn't exist (fresh clone, post-pnpm install),
    `_warn_if_web_bundle_missing()` should print the actionable hint."""
    # Monkeypatch the api module's __file__ to a tempdir whose parent has no
    # web/index.html sibling.
    import ytdl.api as api_pkg
    from ytdl import cli as cli_mod

    fake_pkg = tmp_data_dir / "fake_api_pkg"
    fake_pkg.mkdir()
    monkeypatch.setattr(api_pkg, "__file__", str(fake_pkg / "__init__.py"))

    cli_mod._warn_if_web_bundle_missing()
    captured = capsys.readouterr()
    assert "no built bundle" in captured.out
    assert "pnpm build" in captured.out


def test_warn_if_web_bundle_missing_silent_when_present(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """When the bundle exists, the helper should produce no output."""
    import ytdl.api as api_pkg
    from ytdl import cli as cli_mod

    fake_pkg = tmp_data_dir / "fake_api_pkg"
    fake_pkg.mkdir()
    web_dir = tmp_data_dir / "web"
    web_dir.mkdir()
    (web_dir / "index.html").write_text("<!doctype html>")
    monkeypatch.setattr(api_pkg, "__file__", str(fake_pkg / "__init__.py"))

    cli_mod._warn_if_web_bundle_missing()
    captured = capsys.readouterr()
    assert captured.out == ""


def test_format_bytes_per_second_handles_scale() -> None:
    from ytdl.cli import _format_bytes_per_second

    assert _format_bytes_per_second(None) == ""
    assert _format_bytes_per_second(0) == "0 B/s"
    assert _format_bytes_per_second(500) == "500 B/s"
    assert _format_bytes_per_second(512_000) == "500.0 KB/s"
    assert _format_bytes_per_second(5_242_880) == "5.0 MB/s"
    assert _format_bytes_per_second(2 * 1024**3) == "2.00 GB/s"


def test_format_eta_handles_units() -> None:
    from ytdl.cli import _format_eta

    assert _format_eta(None) == ""
    assert _format_eta(-1) == ""
    assert _format_eta(0) == "0s"
    assert _format_eta(45) == "45s"
    assert _format_eta(125) == "2m 05s"
    assert _format_eta(3_700) == "1h 01m"


def test_format_progress_blank_for_non_running() -> None:
    from ytdl.cli import _format_progress
    from ytdl.models import Job, JobKind, JobStatus

    job = Job(
        id="1", url="u", kind=JobKind.VIDEO, parent_job_id=None,
        status=JobStatus.DONE, format_pref="best", output_dir="/o",
        bytes_done=500, filesize_bytes=1000, speed_bps=1_000_000, eta_s=10,
    )
    assert _format_progress(job) == ""


def test_format_progress_combines_available_pieces() -> None:
    from ytdl.cli import _format_progress
    from ytdl.models import Job, JobKind, JobStatus

    job = Job(
        id="1", url="u", kind=JobKind.VIDEO, parent_job_id=None,
        status=JobStatus.RUNNING, format_pref="best", output_dir="/o",
        bytes_done=500_000, filesize_bytes=1_000_000,
        speed_bps=512_000, eta_s=45,
    )
    assert _format_progress(job) == "50% · 500.0 KB/s · ETA 45s"


def test_format_progress_running_with_no_data_yet_is_blank() -> None:
    from ytdl.cli import _format_progress
    from ytdl.models import Job, JobKind, JobStatus

    job = Job(
        id="1", url="u", kind=JobKind.VIDEO, parent_job_id=None,
        status=JobStatus.RUNNING, format_pref="best", output_dir="/o",
        bytes_done=None, filesize_bytes=None, speed_bps=None, eta_s=None,
    )
    assert _format_progress(job) == ""


def test_cli_queue_ls_includes_progress_column_for_running_jobs(
    tmp_data_dir: Path,
) -> None:
    """queue ls now renders a progress column for running jobs."""
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
        """
        UPDATE jobs SET status='running', bytes_done=?, filesize_bytes=?,
            speed_bps=?, eta_s=? WHERE id=?
        """,
        (500_000, 1_000_000, 512_000, 45, job_id),
    )
    conn.commit()
    conn.close()

    result = runner.invoke(app, ["queue", "ls"])
    assert result.exit_code == 0
    # Rich Tables can wrap long values across lines; check that the pieces
    # all show up somewhere in the output.
    out = result.stdout
    assert "progress" in out.lower(), "progress column header missing"
    assert "50%" in out
    assert "KB/s" in out
    assert "ETA" in out
