"""Browser-cookie selection.

We don't persist cookies ourselves — yt-dlp reads them live from the browser's
cookie store on each job via `cookies_from_browser=(name,)`. All we do is
validate the name the user picked and, when no name was picked, take a best
guess by scanning standard cookie-store paths.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

SUPPORTED_BROWSERS = frozenset(
    {"chrome", "chromium", "firefox", "brave", "edge", "safari", "opera", "vivaldi"}
)


def normalize_browser(name: str) -> str:
    lower = name.strip().lower()
    if not lower:
        raise ValueError("browser name is empty")
    if lower not in SUPPORTED_BROWSERS:
        raise ValueError(
            f"unsupported browser {name!r}; choose one of "
            + ", ".join(sorted(SUPPORTED_BROWSERS))
        )
    return lower


# Priority order. First hit wins. Chrome leads because it is overwhelmingly
# the most common host browser; Safari sits mid-list because its
# .binarycookies store is read-only on modern macOS and yt-dlp's support is
# more limited than the Chromium-family stores.
_AUTODETECT_ORDER = (
    "chrome",
    "brave",
    "firefox",
    "edge",
    "safari",
    "chromium",
    "opera",
    "vivaldi",
)


def cookie_path_for(browser: str) -> Path | None:
    """Return the canonical cookie-store path for ``browser`` on the current
    platform, or ``None`` if we don't know the path for that combination.

    The returned path is NOT validated to exist — callers should
    ``Path.exists()`` themselves. yt-dlp ultimately decides whether the store
    is usable; this lookup just answers the "is the browser installed?"
    question cheaply.
    """
    home = Path.home()
    if sys.platform == "darwin":
        roots: dict[str, Path] = {
            "chrome": home / "Library/Application Support/Google/Chrome/Default/Cookies",
            "brave": home
            / "Library/Application Support/BraveSoftware/Brave-Browser/Default/Cookies",
            "chromium": home / "Library/Application Support/Chromium/Default/Cookies",
            "edge": home / "Library/Application Support/Microsoft Edge/Default/Cookies",
            "opera": home / "Library/Application Support/com.operasoftware.Opera/Cookies",
            "vivaldi": home / "Library/Application Support/Vivaldi/Default/Cookies",
            "safari": home / "Library/Cookies/Cookies.binarycookies",
            # Firefox cookies live inside profile dirs under Profiles/. We just
            # check that the profiles dir exists; yt-dlp picks the default.
            "firefox": home / "Library/Application Support/Firefox/Profiles",
        }
    elif sys.platform.startswith("linux"):
        roots = {
            "chrome": home / ".config/google-chrome/Default/Cookies",
            "brave": home / ".config/BraveSoftware/Brave-Browser/Default/Cookies",
            "chromium": home / ".config/chromium/Default/Cookies",
            "edge": home / ".config/microsoft-edge/Default/Cookies",
            "opera": home / ".config/opera/Cookies",
            "vivaldi": home / ".config/vivaldi/Default/Cookies",
            "firefox": home / ".mozilla/firefox",
        }
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
        roots = {
            "chrome": local / "Google/Chrome/User Data/Default/Network/Cookies",
            "brave": local
            / "BraveSoftware/Brave-Browser/User Data/Default/Network/Cookies",
            "chromium": local / "Chromium/User Data/Default/Network/Cookies",
            "edge": local / "Microsoft/Edge/User Data/Default/Network/Cookies",
            "opera": appdata / "Opera Software/Opera Stable/Network/Cookies",
            "vivaldi": local / "Vivaldi/User Data/Default/Network/Cookies",
            "firefox": appdata / "Mozilla/Firefox/Profiles",
        }
    else:
        return None
    return roots.get(browser.lower())


def autodetect_browser() -> str | None:
    """Pick the first browser in the priority order whose cookie store exists
    on this platform. Returns the browser name (lowercase) or ``None`` when
    nothing was found.

    Detection is intentionally shallow — "does the standard cookie file/dir
    exist for this browser." yt-dlp performs the real cookie read at job time
    and the existing FORBIDDEN hint surfaces if the auto-pick turns out not
    to work.
    """
    for name in _AUTODETECT_ORDER:
        path = cookie_path_for(name)
        if path is not None and path.exists():
            return name
    return None
