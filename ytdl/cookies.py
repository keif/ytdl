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


def _candidate_paths(browser: str) -> list[Path]:
    """Return one or more candidate cookie-store paths for ``browser`` on the
    current platform. Multiple paths cover layout changes across versions
    (e.g., Chromium's ``Default/Network/Cookies`` since Chrome 96 vs the
    legacy ``Default/Cookies``).
    """
    home = Path.home()
    name = browser.lower()

    if sys.platform == "darwin":
        chromium_family = {
            "chrome": "Library/Application Support/Google/Chrome",
            "brave": "Library/Application Support/BraveSoftware/Brave-Browser",
            "chromium": "Library/Application Support/Chromium",
            "edge": "Library/Application Support/Microsoft Edge",
            "vivaldi": "Library/Application Support/Vivaldi",
        }
        if name in chromium_family:
            base = home / chromium_family[name] / "Default"
            return [base / "Network/Cookies", base / "Cookies"]
        roots: dict[str, list[Path]] = {
            "opera": [home / "Library/Application Support/com.operasoftware.Opera/Cookies"],
            "safari": [home / "Library/Cookies/Cookies.binarycookies"],
            "firefox": [home / "Library/Application Support/Firefox/Profiles"],
        }
        return roots.get(name, [])

    if sys.platform.startswith("linux"):
        chromium_family = {
            "chrome": ".config/google-chrome",
            "brave": ".config/BraveSoftware/Brave-Browser",
            "chromium": ".config/chromium",
            "edge": ".config/microsoft-edge",
            "vivaldi": ".config/vivaldi",
        }
        if name in chromium_family:
            base = home / chromium_family[name] / "Default"
            return [base / "Network/Cookies", base / "Cookies"]
        roots = {
            "opera": [home / ".config/opera/Cookies"],
            "firefox": [home / ".mozilla/firefox"],
        }
        return roots.get(name, [])

    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData/Local")))
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData/Roaming")))
        chromium_family = {
            "chrome": local / "Google/Chrome/User Data/Default",
            "brave": local / "BraveSoftware/Brave-Browser/User Data/Default",
            "chromium": local / "Chromium/User Data/Default",
            "edge": local / "Microsoft/Edge/User Data/Default",
            "vivaldi": local / "Vivaldi/User Data/Default",
        }
        if name in chromium_family:
            base = chromium_family[name]
            return [base / "Network/Cookies", base / "Cookies"]
        roots = {
            "opera": [appdata / "Opera Software/Opera Stable/Network/Cookies"],
            "firefox": [appdata / "Mozilla/Firefox/Profiles"],
        }
        return roots.get(name, [])

    return []


def cookie_path_for(browser: str) -> Path | None:
    """Return the canonical cookie-store path for ``browser`` on the current
    platform, or ``None`` if we don't know the path for that combination.

    The returned path is NOT validated to exist — callers should
    ``Path.exists()`` themselves. yt-dlp ultimately decides whether the store
    is usable; this lookup just answers the "is the browser installed?"
    question cheaply.
    """
    paths = _candidate_paths(browser)
    return paths[0] if paths else None


def autodetect_browser() -> str | None:
    """Pick the first browser in the priority order whose cookie store exists
    on this platform. Returns the browser name (lowercase) or ``None`` when
    nothing was found.

    Checks every candidate path per browser to handle layout changes (e.g.,
    Chromium's ``Default/Network/Cookies`` since v96 vs legacy ``Default/Cookies``).

    Detection is intentionally shallow — "does the standard cookie file/dir
    exist for this browser." yt-dlp performs the real cookie read at job time
    and the existing FORBIDDEN hint surfaces if the auto-pick turns out not
    to work.
    """
    for name in _AUTODETECT_ORDER:
        for path in _candidate_paths(name):
            if path.exists():
                return name
    return None
