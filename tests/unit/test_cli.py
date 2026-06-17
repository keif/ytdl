from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ytdl.cli import app

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
