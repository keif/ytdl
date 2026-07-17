#!/usr/bin/env bash
#
# Export YouTube cookies from a signed-in local browser into a cookies.txt
# that the Docker container auto-detects.
#
# yt-dlp reads the browser's cookie store directly (no extension, no manual
# export) and writes a Netscape-format cookies.txt. We drop it at
# docker/data/cookies.txt, which is bind-mounted to /data in the container —
# the app auto-detects a cookies.txt next to its database with no compose
# edits or env vars.
#
# Usage:
#   scripts/export-cookies.sh [browser] [output_path]
#
#   browser      chrome (default) | firefox | brave | edge | safari |
#                chromium | opera | vivaldi
#   output_path  where to write (default: docker/data/cookies.txt)
#
# Examples:
#   scripts/export-cookies.sh                 # Chrome -> docker/data/cookies.txt
#   scripts/export-cookies.sh firefox
#
# Notes:
# - macOS Chrome/Brave/Edge stores are Keychain-encrypted; the first read
#   pops a Keychain password prompt. Approve it. Firefox has no such prompt.
# - Close other tabs logged into YouTube first if you hit a rotation/logout:
#   YouTube can invalidate the session token when it's read while in use.
# - These cookies expire. Re-run this script when downloads start failing
#   again with "Sign in to confirm you're not a bot".
set -euo pipefail

BROWSER="${1:-chrome}"

# Resolve repo root from this script's location so it works from any CWD.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="${2:-$REPO_ROOT/docker/data/cookies.txt}"

# "Me at the zoo" — the first video ever uploaded to YouTube. Public and
# effectively permanent, so it's a safe target. We only need yt-dlp to load
# the browser cookies and write the jar; --skip-download fetches no media.
# Note: yt-dlp saves the --cookies file even if extraction of this URL fails
# (e.g. a transient "Video unavailable"), so success is judged by the written
# file below, NOT by yt-dlp's exit code.
PROBE_URL="https://www.youtube.com/watch?v=jNQXAC9IVRw"

if ! command -v uv >/dev/null 2>&1; then
  echo "error: 'uv' not found on PATH. Install it or run yt-dlp directly." >&2
  exit 1
fi

mkdir -p "$(dirname "$OUTPUT")"

echo "Exporting cookies from '$BROWSER' -> $OUTPUT"
echo "(a Keychain prompt may appear on macOS — approve it)"

# yt-dlp can exit non-zero on a transient extraction hiccup even after it has
# already written the cookie jar, so don't let `set -e` abort on it — capture
# the status and judge success by the output file instead.
YTDLP_ERR="$(
  uv run yt-dlp \
    --cookies-from-browser "$BROWSER" \
    --cookies "$OUTPUT" \
    --skip-download \
    --no-warnings \
    --quiet \
    "$PROBE_URL" 2>&1
)" && YTDLP_RC=0 || YTDLP_RC=$?

if [[ ! -s "$OUTPUT" ]]; then
  echo "error: cookies file was not written or is empty: $OUTPUT" >&2
  echo "Is '$BROWSER' installed and signed in to YouTube?" >&2
  [[ -n "$YTDLP_ERR" ]] && echo "yt-dlp said: $YTDLP_ERR" >&2
  exit 1
fi

# The jar was written. A non-zero yt-dlp exit here just means the probe video
# itself couldn't be extracted — irrelevant to the cookies we came for.
if [[ "$YTDLP_RC" -ne 0 ]]; then
  echo "note: yt-dlp reported '$YTDLP_ERR' on the probe video, but cookies" \
       "were exported successfully — that error does not affect them." >&2
fi

LINES="$(grep -cv '^#' "$OUTPUT" || true)"
echo "Wrote $OUTPUT ($LINES cookie entries)."
echo "Restart the container to pick it up:  cd docker && docker compose up -d"
echo
echo "If downloads still fail with LOGIN_REQUIRED, these cookies were likely"
echo "rotated by your live browser session. Re-export from an incognito window"
echo "and close it immediately — see 'Cookie rotation' in the README."
