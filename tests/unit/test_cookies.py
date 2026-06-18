from __future__ import annotations

from pathlib import Path

import pytest

from ytdl.cookies import SUPPORTED_BROWSERS, normalize_browser


def test_supported_browsers_includes_majors() -> None:
    assert "chrome" in SUPPORTED_BROWSERS
    assert "firefox" in SUPPORTED_BROWSERS
    assert "brave" in SUPPORTED_BROWSERS
    assert "edge" in SUPPORTED_BROWSERS


def test_normalize_browser_lowercases() -> None:
    assert normalize_browser("Chrome") == "chrome"
    assert normalize_browser("FIREFOX") == "firefox"


def test_normalize_browser_rejects_unknown() -> None:
    with pytest.raises(ValueError, match="unsupported browser"):
        normalize_browser("internet-explorer-6")


def test_normalize_browser_rejects_empty() -> None:
    with pytest.raises(ValueError):
        normalize_browser("")


def test_autodetect_returns_none_when_no_browsers_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ytdl import cookies as ck

    # Point HOME at a tempdir with no browser dirs.
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert ck.autodetect_browser() is None


def test_autodetect_picks_chrome_first_when_multiple_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys as _sys

    from ytdl import cookies as ck

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if _sys.platform == "darwin":
        chrome = tmp_path / "Library/Application Support/Google/Chrome/Default/Cookies"
        firefox = tmp_path / "Library/Application Support/Firefox/Profiles"
    elif _sys.platform.startswith("linux"):
        chrome = tmp_path / ".config/google-chrome/Default/Cookies"
        firefox = tmp_path / ".mozilla/firefox"
    else:
        pytest.skip("unsupported platform for this test")
    chrome.parent.mkdir(parents=True, exist_ok=True)
    chrome.touch()
    firefox.mkdir(parents=True, exist_ok=True)
    assert ck.autodetect_browser() == "chrome"


def test_autodetect_falls_through_to_firefox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import sys as _sys

    from ytdl import cookies as ck

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if _sys.platform == "darwin":
        firefox = tmp_path / "Library/Application Support/Firefox/Profiles"
    elif _sys.platform.startswith("linux"):
        firefox = tmp_path / ".mozilla/firefox"
    else:
        pytest.skip("unsupported platform for this test")
    firefox.mkdir(parents=True, exist_ok=True)
    assert ck.autodetect_browser() == "firefox"
