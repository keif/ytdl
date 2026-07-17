from __future__ import annotations

from pathlib import Path

import pytest

from ytdl.config import Config, load_config


def test_defaults_when_no_config_file(tmp_data_dir: Path) -> None:
    cfg = load_config()
    assert isinstance(cfg, Config)
    assert cfg.output_dir == Path.home() / "Videos" / "ytdl"
    assert cfg.db_path == tmp_data_dir / "data" / "ytdl" / "ytdl.db"
    assert cfg.workers == 2
    assert cfg.cookies_browser is None
    assert cfg.default_format == "best"


def test_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        '''
output_dir = "/tmp/grabs"
workers = 4
cookies_browser = "firefox"
default_format = "1080p"
'''
    )
    cfg = load_config()
    assert cfg.output_dir == Path("/tmp/grabs")
    assert cfg.workers == 4
    assert cfg.cookies_browser == "firefox"
    assert cfg.default_format == "1080p"


def test_env_overrides_toml(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YTDL_WORKERS", "8")
    monkeypatch.setenv("YTDL_OUTPUT_DIR", "/srv/grabs")
    cfg = load_config()
    assert cfg.workers == 8
    assert cfg.output_dir == Path("/srv/grabs")


def test_malformed_toml_raises(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("this is = not [valid toml")
    with pytest.raises(ValueError, match="invalid config"):
        load_config()


def test_workers_must_be_positive(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("workers = 0\n")
    with pytest.raises(ValueError, match="workers"):
        load_config()


def test_malformed_workers_env_raises_with_clear_message(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_WORKERS", "abc")
    with pytest.raises(ValueError, match="YTDL_WORKERS"):
        load_config()


def test_load_config_sets_cookies_source_explicit_when_toml_provided(
    tmp_data_dir: Path,
) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('cookies_browser = "firefox"\n')
    cfg = load_config()
    assert cfg.cookies_browser == "firefox"
    assert cfg.cookies_source == "explicit"


def test_cookies_file_defaults_to_none(tmp_data_dir: Path) -> None:
    cfg = load_config()
    assert cfg.cookies_file is None


def test_cookies_file_env_override(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_COOKIES_FILE", "/cookies.txt")
    cfg = load_config()
    assert cfg.cookies_file == "/cookies.txt"


def test_cookies_file_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('cookies_file = "/cookies.txt"\n')
    cfg = load_config()
    assert cfg.cookies_file == "/cookies.txt"


def test_cookies_file_env_expands_tilde(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators may write ~/cookies.txt on a bare-metal install; expand it
    server-side against HOME the same way library_scan_dirs does."""
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.setenv("YTDL_COOKIES_FILE", "~/cookies.txt")
    cfg = load_config()
    assert cfg.cookies_file == "/home/user/cookies.txt"


def test_cookies_file_autodetected_next_to_db(tmp_data_dir: Path) -> None:
    """With nothing pinned, a cookies.txt sitting next to the database is
    picked up automatically. This is the Docker case: the DB lives in the
    mounted /data dir, so dropping cookies.txt beside it needs no compose
    edits or env var."""
    db_dir = tmp_data_dir / "data" / "ytdl"
    db_dir.mkdir(parents=True)
    cookie = db_dir / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    cfg = load_config()
    assert cfg.cookies_file == str(cookie)


def test_cookies_file_autodetected_in_config_dir(tmp_data_dir: Path) -> None:
    """A cookies.txt next to config.toml is auto-detected too — the natural
    spot on a bare-metal install."""
    cfg_dir = tmp_data_dir / "config" / "ytdl"
    cfg_dir.mkdir(parents=True)
    cookie = cfg_dir / "cookies.txt"
    cookie.write_text("# Netscape HTTP Cookie File\n")
    cfg = load_config()
    assert cfg.cookies_file == str(cookie)


def test_cookies_file_autodetect_prefers_db_dir_over_config_dir(
    tmp_data_dir: Path,
) -> None:
    """When cookies.txt exists in both conventional locations, the DB dir
    (the Docker /data mount) wins — that's the primary deployment target."""
    db_dir = tmp_data_dir / "data" / "ytdl"
    db_dir.mkdir(parents=True)
    (db_dir / "cookies.txt").write_text("# db\n")
    cfg_dir = tmp_data_dir / "config" / "ytdl"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "cookies.txt").write_text("# config\n")
    cfg = load_config()
    assert cfg.cookies_file == str(db_dir / "cookies.txt")


def test_explicit_cookies_file_wins_over_autodetected(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit env/TOML value is never overridden by an auto-detected
    file, even when both exist."""
    db_dir = tmp_data_dir / "data" / "ytdl"
    db_dir.mkdir(parents=True)
    (db_dir / "cookies.txt").write_text("# auto\n")
    monkeypatch.setenv("YTDL_COOKIES_FILE", "/explicit/cookies.txt")
    cfg = load_config()
    assert cfg.cookies_file == "/explicit/cookies.txt"


def test_pot_provider_url_defaults_to_none(tmp_data_dir: Path) -> None:
    cfg = load_config()
    assert cfg.pot_provider_url is None


def test_pot_provider_url_env_override(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_POT_PROVIDER_URL", "http://bgutil-provider:4416")
    cfg = load_config()
    assert cfg.pot_provider_url == "http://bgutil-provider:4416"


def test_pot_provider_url_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text('pot_provider_url = "http://127.0.0.1:4416"\n')
    cfg = load_config()
    assert cfg.pot_provider_url == "http://127.0.0.1:4416"


def test_default_subtitle_langs_for_english_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ytdl.config import _default_subtitle_langs

    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    assert _default_subtitle_langs() == ["en"]


def test_default_subtitle_langs_for_spanish_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ytdl.config import _default_subtitle_langs

    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "es_ES.UTF-8")
    # English fallback is always appended even for non-EN locales.
    assert _default_subtitle_langs() == ["es", "en"]


def test_default_subtitle_langs_when_lang_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ytdl.config import _default_subtitle_langs

    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.delenv("LANG", raising=False)
    assert _default_subtitle_langs() == ["en"]


def test_default_subtitle_langs_treats_c_locale_as_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LANG=C.UTF-8 is the build/CI fallback locale; treat it as 'no
    locale info' and ship plain ['en']."""
    from ytdl.config import _default_subtitle_langs

    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "C.UTF-8")
    assert _default_subtitle_langs() == ["en"]


def test_subtitle_langs_env_override_wins(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_SUBTITLE_LANGS", "en, es, fr ")
    cfg = load_config()
    assert cfg.subtitle_langs == ("en", "es", "fr")


def test_subtitles_default_env_override(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_SUBTITLES_DEFAULT", "true")
    cfg = load_config()
    assert cfg.subtitles_default is True


def test_subtitles_default_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        '''
subtitles_default = true
subtitle_langs = ["en", "ja"]
'''
    )
    cfg = load_config()
    assert cfg.subtitles_default is True
    assert cfg.subtitle_langs == ("en", "ja")


def test_autosubmit_delay_defaults_to_five(tmp_data_dir: Path) -> None:
    """No env, no TOML — the dataclass default (5s) flows through."""
    cfg = load_config()
    assert cfg.autosubmit_delay_s == 5


def test_autosubmit_delay_env_override(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env wins over the default; integer values pass through unchanged."""
    monkeypatch.setenv("YTDL_AUTOSUBMIT_DELAY_S", "10")
    cfg = load_config()
    assert cfg.autosubmit_delay_s == 10


def test_autosubmit_delay_zero_disables(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0 is a valid disable-the-feature sentinel and must be preserved as-is
    (not coerced to the default)."""
    monkeypatch.setenv("YTDL_AUTOSUBMIT_DELAY_S", "0")
    cfg = load_config()
    assert cfg.autosubmit_delay_s == 0


def test_autosubmit_delay_negative_rejected(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative seconds aren't physically meaningful — fail fast at config
    load so a typo surfaces at startup rather than silently disabling the UI
    feature."""
    monkeypatch.setenv("YTDL_AUTOSUBMIT_DELAY_S", "-3")
    with pytest.raises(ValueError, match="autosubmit_delay_s"):
        load_config()


def test_autosubmit_delay_malformed_env_raises_clear_message(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-integer env values get a targeted error so the operator knows
    exactly which knob to fix."""
    monkeypatch.setenv("YTDL_AUTOSUBMIT_DELAY_S", "abc")
    with pytest.raises(ValueError, match="YTDL_AUTOSUBMIT_DELAY_S"):
        load_config()


def test_autosubmit_delay_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("autosubmit_delay_s = 7\n")
    cfg = load_config()
    assert cfg.autosubmit_delay_s == 7


def test_probe_timeout_defaults_to_sixty(tmp_data_dir: Path) -> None:
    """No env, no TOML — the dataclass default (60s) flows through. Bumped from
    30s: probes now do a live browser-cookie read (~5s via Keychain on macOS)
    and, with a PO token provider, token minting, so 30s was too tight."""
    cfg = load_config()
    assert cfg.probe_timeout_s == 60


def test_probe_timeout_env_override(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Env wins over the default; integer values pass through unchanged."""
    monkeypatch.setenv("YTDL_PROBE_TIMEOUT_S", "60")
    cfg = load_config()
    assert cfg.probe_timeout_s == 60


def test_probe_timeout_zero_rejected(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 0-second timeout would fail every probe instantly; reject so the
    operator notices the misconfiguration at startup."""
    monkeypatch.setenv("YTDL_PROBE_TIMEOUT_S", "0")
    with pytest.raises(ValueError, match="probe_timeout_s"):
        load_config()


def test_probe_timeout_negative_rejected(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative seconds aren't physically meaningful."""
    monkeypatch.setenv("YTDL_PROBE_TIMEOUT_S", "-5")
    with pytest.raises(ValueError, match="probe_timeout_s"):
        load_config()


def test_probe_timeout_malformed_env_raises_clear_message(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Non-integer env values get a targeted error."""
    monkeypatch.setenv("YTDL_PROBE_TIMEOUT_S", "abc")
    with pytest.raises(ValueError, match="YTDL_PROBE_TIMEOUT_S"):
        load_config()


def test_probe_timeout_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("probe_timeout_s = 45\n")
    cfg = load_config()
    assert cfg.probe_timeout_s == 45


def test_library_scan_dirs_defaults_to_output_dir(tmp_data_dir: Path) -> None:
    """When no env / TOML pin, the resolved scan dirs fall back to
    (output_dir,). The stored tuple stays empty (that's how we know the
    user didn't opt in) but the resolver expands it at read time."""
    cfg = load_config()
    assert cfg.library_scan_dirs == ()
    resolved = cfg.resolve_library_scan_dirs()
    assert resolved == (str(cfg.output_dir),)


def test_library_scan_dirs_env_override_parses_comma_separated(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_LIBRARY_SCAN_DIRS", "/a, /b/nested , /c")
    cfg = load_config()
    assert cfg.library_scan_dirs == ("/a", "/b/nested", "/c")
    # Resolver returns the explicit list verbatim (no fallback).
    assert cfg.resolve_library_scan_dirs() == ("/a", "/b/nested", "/c")


def test_library_scan_dirs_env_expands_tilde(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", "/home/user")
    monkeypatch.setenv("YTDL_LIBRARY_SCAN_DIRS", "~/Videos,/mnt/media")
    cfg = load_config()
    assert cfg.library_scan_dirs == ("/home/user/Videos", "/mnt/media")


def test_library_scan_dirs_env_drops_empty_entries(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_LIBRARY_SCAN_DIRS", "/a,,  ,/b")
    cfg = load_config()
    assert cfg.library_scan_dirs == ("/a", "/b")


def test_library_scan_dirs_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text(
        'library_scan_dirs = ["/mnt/plex", "/mnt/backup"]\n'
    )
    cfg = load_config()
    assert cfg.library_scan_dirs == ("/mnt/plex", "/mnt/backup")


def test_dedup_enabled_defaults_true(tmp_data_dir: Path) -> None:
    cfg = load_config()
    assert cfg.dedup_enabled is True


def test_dedup_enabled_env_false(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_DEDUP_ENABLED", "false")
    cfg = load_config()
    assert cfg.dedup_enabled is False


def test_dedup_enabled_env_true(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("YTDL_DEDUP_ENABLED", "true")
    cfg = load_config()
    assert cfg.dedup_enabled is True


def test_dedup_enabled_loads_from_toml(tmp_data_dir: Path) -> None:
    cfg_path = tmp_data_dir / "config" / "ytdl" / "config.toml"
    cfg_path.parent.mkdir(parents=True)
    cfg_path.write_text("dedup_enabled = false\n")
    cfg = load_config()
    assert cfg.dedup_enabled is False


def test_load_config_sets_cookies_source_autodetect_or_none(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No explicit config + tempdir HOME = either autodetect finds nothing
    # (typical CI image) or somehow trips on a real browser dir under the
    # tempdir (vanishingly unlikely). Either way the source must NOT be
    # "explicit".
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_data_dir))
    cfg = load_config()
    assert cfg.cookies_source in ("autodetect", "none")
    if cfg.cookies_browser is None:
        assert cfg.cookies_source == "none"
    else:
        assert cfg.cookies_source == "autodetect"
