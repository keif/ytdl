from __future__ import annotations

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
