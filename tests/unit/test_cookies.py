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
        chrome = tmp_path / "Library/Application Support/Google/Chrome/Default/Network/Cookies"
        firefox = tmp_path / "Library/Application Support/Firefox/Profiles"
    elif _sys.platform.startswith("linux"):
        chrome = tmp_path / ".config/google-chrome/Default/Network/Cookies"
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


def test_autodetect_picks_chrome_at_new_network_cookies_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Chrome moved cookies to Default/Network/Cookies around v96 (2021).
    Make sure we detect that layout, not just the legacy one."""
    import sys as _sys

    from ytdl import cookies as ck

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if _sys.platform == "darwin":
        chrome_new = tmp_path / "Library/Application Support/Google/Chrome/Default/Network/Cookies"
    elif _sys.platform.startswith("linux"):
        chrome_new = tmp_path / ".config/google-chrome/Default/Network/Cookies"
    else:
        pytest.skip("unsupported platform for this test")
    chrome_new.parent.mkdir(parents=True, exist_ok=True)
    chrome_new.touch()
    assert ck.autodetect_browser() == "chrome"


def test_autodetect_picks_chrome_at_legacy_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pre-v96 Chrome installs still use Default/Cookies. Stay
    backward-compatible."""
    import sys as _sys

    from ytdl import cookies as ck

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    if _sys.platform == "darwin":
        chrome_legacy = tmp_path / "Library/Application Support/Google/Chrome/Default/Cookies"
    elif _sys.platform.startswith("linux"):
        chrome_legacy = tmp_path / ".config/google-chrome/Default/Cookies"
    else:
        pytest.skip("unsupported platform for this test")
    chrome_legacy.parent.mkdir(parents=True, exist_ok=True)
    chrome_legacy.touch()
    assert ck.autodetect_browser() == "chrome"


def test_autodetect_honors_xdg_config_home_on_linux(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Linux: when XDG_CONFIG_HOME is set, Chromium-family browsers live
    under it, not under ~/.config. Autodetect must follow."""
    import sys as _sys

    from ytdl import cookies as ck

    if not _sys.platform.startswith("linux"):
        pytest.skip("linux-only test")

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    xdg = tmp_path / "custom_xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    chrome = xdg / "google-chrome" / "Default" / "Network" / "Cookies"
    chrome.parent.mkdir(parents=True, exist_ok=True)
    chrome.touch()
    assert ck.autodetect_browser() == "chrome"
