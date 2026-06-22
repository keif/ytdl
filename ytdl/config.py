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
    # How `cookies_browser` was resolved:
    #   "explicit"   — pinned via env var or TOML
    #   "autodetect" — picked by scanning standard cookie-store paths
    #   "none"       — no value pinned and nothing detected
    cookies_source: str = "none"
    # When True, every new job opts into subtitle download unless the
    # API/CLI explicitly passes False. The UI seeds its checkbox from this.
    subtitles_default: bool = False
    # Languages (yt-dlp codes) requested when subtitles is enabled. The
    # default helper reads $LANG and prepends the user's locale; English is
    # always included as a fallback because most uploads only carry 'en'.
    subtitle_langs: tuple[str, ...] = ("en",)
    # Seconds the UI waits after a single-video preview resolves before
    # auto-submitting the job. The countdown banner ticks this down and the
    # user can cancel at any time during the window. A value of 0 disables
    # the feature entirely — the user must click Download manually.
    autosubmit_delay_s: int = 5


def _default_output_dir() -> Path:
    return Path.home() / "Videos" / "ytdl"


def _default_db_path() -> Path:
    return _xdg_data_home() / "ytdl" / "ytdl.db"


def _default_subtitle_langs() -> list[str]:
    """Derive the subtitle language list from the user's locale env vars.

    Reads LANG / LC_ALL / LC_MESSAGES in that order, strips encoding and
    region (``en_US.UTF-8`` -> ``en``), and always appends ``en`` as a
    fallback because most uploads only ship English subs. Values like
    ``C`` / ``C.UTF-8`` / ``POSIX`` aren't real locales and are ignored.
    """
    raw = ""
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(key, "")
        if v:
            raw = v
            break
    code = raw.split(".", 1)[0].split("_", 1)[0].strip().lower()
    if not code or code in ("c", "posix"):
        return ["en"]
    if code == "en":
        return ["en"]
    return [code, "en"]


def _parse_subtitle_langs_env(raw: str) -> list[str]:
    """Split a comma-separated env value into a clean lang list.

    Strips whitespace, drops empties, preserves order and dedupes.
    """
    seen: list[str] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        if t not in seen:
            seen.append(t)
    return seen


def _coerce_bool(v: str) -> bool:
    """Parse a boolean-ish env var value."""
    return v.strip().lower() in ("1", "true", "yes", "on")


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
        try:
            out["workers"] = int(v)
        except ValueError as exc:
            raise ValueError(f"invalid YTDL_WORKERS={v!r}: must be an integer") from exc
    if v := os.environ.get("YTDL_COOKIES_BROWSER"):
        out["cookies_browser"] = v
    if v := os.environ.get("YTDL_DEFAULT_FORMAT"):
        out["default_format"] = v
    if v := os.environ.get("YTDL_LOG_LEVEL"):
        out["log_level"] = v
    if v := os.environ.get("YTDL_SUBTITLES_DEFAULT"):
        out["subtitles_default"] = _coerce_bool(v)
    if v := os.environ.get("YTDL_SUBTITLE_LANGS"):
        parsed = _parse_subtitle_langs_env(v)
        if parsed:
            out["subtitle_langs"] = parsed
    if v := os.environ.get("YTDL_AUTOSUBMIT_DELAY_S"):
        try:
            out["autosubmit_delay_s"] = int(v)
        except ValueError as exc:
            raise ValueError(
                f"invalid YTDL_AUTOSUBMIT_DELAY_S={v!r}: must be an integer"
            ) from exc
    return out


def load_config() -> Config:
    raw = {**_read_toml(), **_env_overrides()}
    workers = int(raw.get("workers", 2))
    if workers < 1:
        raise ValueError("workers must be >= 1")
    cookies_browser = raw.get("cookies_browser")
    cookies_source = "explicit"
    if not cookies_browser:
        # No env / TOML pin — fall back to scanning standard cookie-store
        # paths so a fresh install doesn't need any setup before the first
        # YouTube download.
        from ytdl.cookies import autodetect_browser

        cookies_browser = autodetect_browser()
        cookies_source = "autodetect" if cookies_browser else "none"
    subtitles_default = bool(raw.get("subtitles_default", False))
    raw_langs = raw.get("subtitle_langs")
    if isinstance(raw_langs, list) and raw_langs:
        # Defensive normalize: TOML allows arbitrary strings here; strip
        # whitespace and drop empties so a typo'd entry doesn't reach
        # yt-dlp as a blank lang code.
        subtitle_langs: list[str] = []
        for entry in raw_langs:
            if not isinstance(entry, str):
                continue
            t = entry.strip()
            if t and t not in subtitle_langs:
                subtitle_langs.append(t)
        if not subtitle_langs:
            subtitle_langs = _default_subtitle_langs()
    elif isinstance(raw_langs, str) and raw_langs.strip():
        # TOML could be a single string; treat like the env-var format.
        subtitle_langs = _parse_subtitle_langs_env(raw_langs)
        if not subtitle_langs:
            subtitle_langs = _default_subtitle_langs()
    else:
        subtitle_langs = _default_subtitle_langs()
    autosubmit_delay_s = int(raw.get("autosubmit_delay_s", 5))
    if autosubmit_delay_s < 0:
        raise ValueError("autosubmit_delay_s must be >= 0")
    return Config(
        output_dir=Path(raw.get("output_dir", _default_output_dir())),
        db_path=Path(raw.get("db_path", _default_db_path())),
        workers=workers,
        cookies_browser=cookies_browser,
        default_format=raw.get("default_format", "best"),
        log_level=raw.get("log_level", "INFO"),
        cookies_source=cookies_source,
        subtitles_default=subtitles_default,
        subtitle_langs=tuple(subtitle_langs),
        autosubmit_delay_s=autosubmit_delay_s,
    )
