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
    # Path to a Netscape-format cookies.txt handed to yt-dlp's `cookiefile`.
    # This is the authentication path for environments with no reachable
    # browser cookie store — chiefly Docker, where `cookies_browser` /
    # autodetect find nothing because the container has no host browser
    # profile. Tilde-expanded at load time. Env: YTDL_COOKIES_FILE.
    # TOML: cookies_file = "/path/to/cookies.txt". Independent of
    # `cookies_browser`; both may be set and yt-dlp merges them.
    cookies_file: str | None = None
    # Base URL of a bgutil PO token provider (the sidecar's HTTP server). When
    # set, yt-dlp is wired to mint Proof-of-Origin tokens through it — required
    # to get past YouTube's anti-bot gate on hosts where cookies alone return
    # LOGIN_REQUIRED. Unset means no PO token provider (prior behavior).
    # Env: YTDL_POT_PROVIDER_URL. TOML: pot_provider_url.
    pot_provider_url: str | None = None
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
    # Upper bound on a single yt-dlp probe (POST /preview, POST /preview/enrich).
    # Enforced at three layers, each a backstop for the next:
    #   1. yt-dlp's `socket_timeout` aborts a hung HTTP read.
    #   2. subprocess.run's timeout (= socket_timeout + 5) OS-kills the worker
    #      if yt-dlp ignores socket_timeout on some code path.
    #   3. asyncio.wait_for around to_thread (= socket_timeout + 10) covers the
    #      subprocess startup window.
    # A normal probe takes 1-3s; 30s is the "something is very wrong" cliff.
    # See downloader.probe / routes_preview / ytdl._probe_worker.
    probe_timeout_s: int = 30
    # Directories that ytdl.library.scan_directories walks to build the
    # duplicate-detection index. Empty tuple = fall back to (output_dir,) so
    # a fresh install with no explicit config still gets duplicate detection
    # against the queue's own downloads. Users with media on separate mounts
    # (e.g., an NFS-mounted Plex library) can list additional roots here.
    # Tilde expansion happens server-side at load time.
    # Env: YTDL_LIBRARY_SCAN_DIRS (comma-separated).
    # TOML: library_scan_dirs = ["/mnt/media/videos", "..."]
    library_scan_dirs: tuple[str, ...] = ()
    # Feature flag for duplicate detection. When False, the /library/rescan
    # endpoint still works but the /preview and /jobs paths skip lookup /
    # 409-on-duplicate behavior. Lets a user who doesn't want the flow opt
    # out globally without emptying library_scan_dirs.
    dedup_enabled: bool = True

    def resolve_library_scan_dirs(self) -> tuple[str, ...]:
        """Return the scan-dir list, falling back to (output_dir,) when unset.

        Empty tuple is the sentinel for "no explicit config", not "scan
        nothing" — that avoids a footgun where a fresh install would index
        zero files and never warn on duplicates.
        """
        if self.library_scan_dirs:
            return self.library_scan_dirs
        return (str(self.output_dir),)


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


def _autodetect_cookies_file(db_path: Path) -> str | None:
    """Find a cookies.txt in a conventional location when none is pinned.

    Checks the database directory first — in the Docker image the DB lives in
    the mounted ``/data`` volume, so dropping ``cookies.txt`` beside it is the
    zero-config path (no compose edits, no env var). Then falls back to the
    XDG config dir, the natural spot next to ``config.toml`` on a bare-metal
    install. First existing file wins; returns ``None`` if neither is present.
    """
    for path in (
        db_path.parent / "cookies.txt",
        _xdg_config_home() / "ytdl" / "cookies.txt",
    ):
        if path.is_file():
            return str(path)
    return None


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
    if v := os.environ.get("YTDL_COOKIES_FILE"):
        out["cookies_file"] = v
    if v := os.environ.get("YTDL_POT_PROVIDER_URL"):
        out["pot_provider_url"] = v
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
    if v := os.environ.get("YTDL_PROBE_TIMEOUT_S"):
        try:
            out["probe_timeout_s"] = int(v)
        except ValueError as exc:
            raise ValueError(
                f"invalid YTDL_PROBE_TIMEOUT_S={v!r}: must be an integer"
            ) from exc
    if v := os.environ.get("YTDL_LIBRARY_SCAN_DIRS"):
        parsed_dirs = _parse_library_scan_dirs_env(v)
        # Only override when there's at least one non-empty entry after
        # trimming — an env of just "," or " " shouldn't wipe the TOML.
        if parsed_dirs:
            out["library_scan_dirs"] = parsed_dirs
    if v := os.environ.get("YTDL_DEDUP_ENABLED"):
        out["dedup_enabled"] = _coerce_bool(v)
    return out


def _parse_library_scan_dirs_env(raw: str) -> list[str]:
    """Split a comma-separated env value into a list of directory paths.

    Strips whitespace, drops empties, expands ``~`` server-side so operators
    can write ``~/Videos`` in the env and have it resolve against HOME.
    Preserves order and dedupes so a typo'd duplicate doesn't double-scan.
    """
    seen: list[str] = []
    for token in raw.split(","):
        t = token.strip()
        if not t:
            continue
        expanded = str(Path(t).expanduser())
        if expanded not in seen:
            seen.append(expanded)
    return seen


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
    db_path = Path(raw.get("db_path", _default_db_path()))
    # Cookies file: an explicit env/TOML value wins (expand ~ so an operator
    # can write ~/cookies.txt). With nothing pinned, auto-detect a cookies.txt
    # in a conventional spot so a dropped file "just works" — the Docker /data
    # mount especially, which the container has no other way to reach.
    raw_cookies_file = raw.get("cookies_file")
    if raw_cookies_file:
        cookies_file: str | None = str(Path(str(raw_cookies_file)).expanduser())
    else:
        cookies_file = _autodetect_cookies_file(db_path)
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
    # Coerce probe_timeout_s carefully — TOML may hand us a string, env
    # parsing above already int-coerced. A 0/negative value would mean
    # "give up immediately"; that's never what the user wants, so reject.
    raw_probe = raw.get("probe_timeout_s", 30)
    try:
        probe_timeout_s = int(raw_probe)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"invalid probe_timeout_s={raw_probe!r}: must be an integer"
        ) from exc
    if probe_timeout_s < 1:
        raise ValueError("probe_timeout_s must be >= 1")
    # library_scan_dirs: TOML may be a list of strings; env parsing already
    # produced a list. Both go through the same normalizer so leading/
    # trailing whitespace, tilde expansion, and dedup behave identically no
    # matter how the config was supplied. An unset value stays as ``()`` so
    # ``Config.resolve_library_scan_dirs`` falls back to ``(output_dir,)``.
    raw_scan_dirs = raw.get("library_scan_dirs")
    library_scan_dirs: tuple[str, ...] = ()
    if isinstance(raw_scan_dirs, list):
        clean: list[str] = []
        for entry in raw_scan_dirs:
            if not isinstance(entry, str):
                continue
            t = entry.strip()
            if not t:
                continue
            expanded = str(Path(t).expanduser())
            if expanded not in clean:
                clean.append(expanded)
        library_scan_dirs = tuple(clean)
    dedup_enabled = bool(raw.get("dedup_enabled", True))
    return Config(
        output_dir=Path(raw.get("output_dir", _default_output_dir())),
        db_path=db_path,
        workers=workers,
        cookies_browser=cookies_browser,
        cookies_file=cookies_file,
        pot_provider_url=raw.get("pot_provider_url") or None,
        default_format=raw.get("default_format", "best"),
        log_level=raw.get("log_level", "INFO"),
        cookies_source=cookies_source,
        subtitles_default=subtitles_default,
        subtitle_langs=tuple(subtitle_langs),
        autosubmit_delay_s=autosubmit_delay_s,
        probe_timeout_s=probe_timeout_s,
        library_scan_dirs=library_scan_dirs,
        dedup_enabled=dedup_enabled,
    )
