"""Browser-cookie selection.

We don't persist cookies ourselves — yt-dlp reads them live from the browser's
cookie store on each job via `cookies_from_browser=(name,)`. All we do is
validate the name the user picked.
"""
from __future__ import annotations

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
