"""Configuration loading.

Resolution order, highest priority first:
  1. Environment variables (YTDL_*)
  2. ~/.config/ytdl/config.toml (or $XDG_CONFIG_HOME/ytdl/config.toml)
  3. Built-in defaults
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))


def _xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))


@dataclass(frozen=True)
class Config:
    output_dir: Path
    db_path: Path
    workers: int
    cookies_browser: str | None
    default_format: str
    log_level: str = "INFO"


def _default_output_dir() -> Path:
    return Path.home() / "Videos" / "ytdl"


def _default_db_path() -> Path:
    return _xdg_data_home() / "ytdl" / "ytdl.db"


def _read_toml() -> dict:
    path = _xdg_config_home() / "ytdl" / "config.toml"
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"invalid config at {path}: {exc}") from exc


def _env_overrides() -> dict:
    out: dict = {}
    if v := os.environ.get("YTDL_OUTPUT_DIR"):
        out["output_dir"] = v
    if v := os.environ.get("YTDL_DB_PATH"):
        out["db_path"] = v
    if v := os.environ.get("YTDL_WORKERS"):
        out["workers"] = int(v)
    if v := os.environ.get("YTDL_COOKIES_BROWSER"):
        out["cookies_browser"] = v
    if v := os.environ.get("YTDL_DEFAULT_FORMAT"):
        out["default_format"] = v
    if v := os.environ.get("YTDL_LOG_LEVEL"):
        out["log_level"] = v
    return out


def load_config() -> Config:
    raw = {**_read_toml(), **_env_overrides()}
    workers = int(raw.get("workers", 2))
    if workers < 1:
        raise ValueError("workers must be >= 1")
    return Config(
        output_dir=Path(raw.get("output_dir", _default_output_dir())),
        db_path=Path(raw.get("db_path", _default_db_path())),
        workers=workers,
        cookies_browser=raw.get("cookies_browser"),
        default_format=raw.get("default_format", "best"),
        log_level=raw.get("log_level", "INFO"),
    )
