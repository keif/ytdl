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
