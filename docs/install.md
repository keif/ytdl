# Installing and Using ytdl

A walkthrough from zero to "I just downloaded a video." If you want
reference-style documentation (every flag, every endpoint), see the
[README](../README.md). This document is the story.

> **Before you start:** `ytdl` is a single-operator tool. Run it on your own
> machine, your LAN, or a VPS behind Tailscale / WireGuard. It is **not**
> safe to host publicly. See [SECURITY.md](../SECURITY.md) for the legal
> and architectural reasons.

## What you'll have at the end

A small web app running at `http://localhost:5174` where you paste a YouTube
URL and a few seconds later get an `.mp4` (or `.mp3`, or a whole playlist) on
disk. A CLI is also available for headless / scripted use.

## Prerequisites

You need three things on PATH:

1. **Python 3.12 or newer**
2. **[uv](https://github.com/astral-sh/uv)** — fast Python package manager
3. **ffmpeg** — yt-dlp uses it to merge audio + video streams

Optional but recommended:

4. **deno** — yt-dlp uses it as a JavaScript runtime for YouTube's "n" challenge.
   Without it you'll see signature-extraction errors on most videos.

### macOS install

```bash
brew install uv ffmpeg deno
```

### Linux (Debian / Ubuntu) install

```bash
# uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# ffmpeg + deno
sudo apt install -y ffmpeg
curl -fsSL https://deno.land/install.sh | sh
```

You also need **Node 22+** and **pnpm** for the web frontend's build step:

```bash
# via corepack (ships with Node)
corepack enable && corepack prepare pnpm@latest --activate
```

## Install ytdl

```bash
git clone https://github.com/keif/ytdl.git
cd ytdl
uv sync                              # installs Python deps
cd web && pnpm install --frozen-lockfile && cd ..
```

That's it. No global install, no system service.

## First run

```bash
./dev.sh
```

This spins up:

- The FastAPI backend on `http://127.0.0.1:8766`
- The Vite dev server on `http://127.0.0.1:5174`

Open `http://127.0.0.1:5174` in your browser. The UI is one column: a URL
input at the top, a queue below.

### Paste a URL

Paste any YouTube link — a single video URL, a playlist URL, or a search
result. A preview card appears within a second showing the title and
duration. If it's a single video, a **5-second countdown banner** appears
just above the card: *"Downloading in 5s — Cancel."* If you don't touch
anything, the download starts automatically. If you change your mind,
click **Cancel** or paste a different URL.

Watch the row in the queue: it moves through `pending → running → done`.
The file lands in `~/Downloads/ytdl/` by default.

### Playlists

If the URL is a playlist, no auto-submit. Instead you get a picker: a list
of entries with checkboxes. Pick the subset you want, hit **Download N**,
and each picked entry queues as its own job.

## Configuring cookies (for sign-in / age-gated content)

YouTube blocks downloading age-restricted videos, members-only content, and
some music videos unless you're signed in. `ytdl` reads cookies directly
from your local browser session — no manual export needed.

If Chrome is your default browser and you're signed in to YouTube, **you
don't need to do anything**. The startup auto-detect picks Chrome and
hands the path to yt-dlp.

Verify with:

```bash
uv run ytdl cookies status
```

You should see something like:

```
browser: chrome (autodetected)
path:    ~/Library/Application Support/Google/Chrome/Default/Cookies
```

To use a different browser, set `YTDL_COOKIES_BROWSER`:

```bash
export YTDL_COOKIES_BROWSER=firefox
./dev.sh
```

Supported values: `chrome`, `firefox`, `safari`, `brave`, `edge`, `chromium`,
`opera`, `vivaldi`.

**macOS Keychain prompt:** the first time Chrome's cookies are read, macOS
asks for your password. This is normal — yt-dlp needs to decrypt the cookie
store. Approve once and it remembers.

## Choosing format and output

Three controls in the UI, just under the URL input:

- **Format dropdown** — `best`, `1080p`, `720p`, etc. Defaults to `best`.
- **Audio only** checkbox — overrides the dropdown. Downloads only the
  audio track as `.m4a` / `.mp3` (whatever yt-dlp picks as best). Perfect
  for podcasts, music, lectures.
- **Subtitles** checkbox — downloads real subtitles (not auto-generated
  CC) in your locale plus English fallback. Embeds them in the MP4 AND
  saves sidecar `.vtt` files for Plex / Jellyfin libraries.

Click **Advanced** below the form to expose:

- **Save to** — a per-job output directory override. Empty means "use
  the config default" (shown as placeholder text). Tilde expansion works:
  type `~/Music` and it lands there. The server validates the path is
  writable before queuing.

The audio-only and Save-to choices reset between URLs — they're
*per-paste intent*. The Subtitles checkbox persists (it mirrors a server
config default, so it's a real preference).

## Config file

For settings you want permanent, drop a `config.toml` at the project root:

```toml
output_dir = "~/Music/Downloads"     # where files land by default
workers = 2                          # parallel downloads
subtitles_default = true             # check the subtitles box on every paste
subtitle_langs = ["en", "es"]        # languages to fetch
autosubmit_delay_s = 5               # countdown seconds; 0 disables the feature
cookies_browser = "firefox"          # override the autodetect
```

All keys also work as env vars: `YTDL_OUTPUT_DIR`, `YTDL_WORKERS`,
`YTDL_SUBTITLES_DEFAULT`, etc.

## CLI usage

Same downloader, no browser:

```bash
# Single video
uv run ytdl get "https://www.youtube.com/watch?v=..." -o ~/Downloads/ytdl

# Audio only
uv run ytdl get "https://..." --format-pref audio_only

# Subtitles
uv run ytdl get "https://..." --subs

# Queue a URL (for the background worker the web UI uses)
uv run ytdl queue add "https://..."
uv run ytdl queue list
uv run ytdl queue redownload <job-id>
```

`ytdl --help` lists all subcommands.

## Updating

```bash
git pull
uv sync                              # refresh Python deps
cd web && pnpm install --frozen-lockfile && cd ..
```

If you see a "schema migration" message on next startup, that's the SQLite
queue auto-upgrading. No action needed.

## Troubleshooting

**"Preview stays at 'Fetching preview…' forever."** Most likely the API
server isn't responding. Check that port 8766 is up: `curl
http://127.0.0.1:8766/status`. If that hangs too, the FastAPI loop wedged
on a yt-dlp probe. Hit `Ctrl-C` on `dev.sh` and restart.

**"ERROR: unable to download video data: HTTP Error 403: Forbidden."**
Two common causes:
- Missing the JS challenge solver. Install `deno` (see prerequisites)
  and restart.
- Stale cookies. Sign out and back in to YouTube in your browser, then
  retry. Or set `YTDL_COOKIES_BROWSER` explicitly.

**"Sign in to confirm your age."** The video is age-gated. Make sure
you're signed in to a YouTube account that has age confirmation, and
that the cookies path is correct.

**"403 from a video that worked yesterday."** YouTube rotates the signature
algorithm constantly. Update yt-dlp:

```bash
uv sync --upgrade-package yt-dlp
```

**"Playlist treated as a single video."** Some URLs include `&list=RD...`
(radio mix) parameters. `ytdl` deliberately treats `?v=X&list=RDX` as a
single video, not a 25-track playlist. To download the whole playlist,
use the bare playlist URL (`https://www.youtube.com/playlist?list=...`).

## Limits

The honest list:

- Designed for **single-operator** use. No accounts, no per-user
  isolation, no rate limits. Run it where you trust everyone with
  access.
- The cookies feature reads YOUR browser. Any download you start runs
  with YOUR YouTube identity attached.
- YouTube's terms prohibit downloading. You assume any risk.

See [SECURITY.md](../SECURITY.md) for the full posture.

---

That's it. If something didn't work, open an issue with the URL that
failed and the last 20 lines of server log. PRs welcome.
