# ytdl

Self-hosted yt-dlp queue with a CLI, a local web UI, and a single Docker container for homelab self-host.

- Paste a YouTube URL or playlist URL, see a preview, and download what you want.
- Persistent SQLite-backed job queue with 2–3 parallel workers; jobs survive restarts.
- Auth via your browser's cookies (no passwords stored), so age-restricted and Premium-quality formats just work. The browser is auto-detected on startup.
- Retry failed downloads in one click; sweep old DONE rows from the queue when they pile up.
- Supports yt-dlp's full site catalog (~1,800 sites), not just YouTube.

## Quick start (local)

    uv sync
    uv run ytdl get "https://youtu.be/dQw4w9WgXcQ"

That downloads one URL synchronously to `~/Videos/ytdl/`.

To run the API + web UI:

    ./dev.sh
    # API at http://127.0.0.1:8765
    # UI  at http://127.0.0.1:5174 (Vite dev server proxies to the API)

The dev script starts uvicorn with HMR and Vite in parallel; Ctrl+C kills both.

## Quick start (Docker self-host)

    cd docker
    docker compose up -d --build
    # UI + API at http://localhost:8765

The compose file mounts `~/Videos/ytdl` for downloads and `./data/` for the SQLite database. `ffmpeg` is baked into the image.

## The web UI flow

1. Paste a URL into the input. After ~500ms the frontend hits `/preview`.
2. **Single video** → an inline card appears with thumbnail, title, uploader, duration. One click on **Download** enqueues it.
3. **Playlist** → an inline picker appears. All entries checked by default; thumbnails/duration/uploader stream in per-entry as the backend enriches. Use **Select all / Deselect all** or untick individual rows, then **Download N selected** enqueues only those.
4. The header chip shows the SSE connection state and the cookies source (e.g., `cookies: chrome (auto)`).
5. The queue lists each job with relative timestamps (`finished 5m ago`) and an attempt count when there's been more than one. Failed/canceled/done rows expose a **Retry** button; an old-DONE-job sweep button appears when there's something to clean up.

## Configuration

ytdl reads configuration from, in order of precedence:

1. Environment variables (`YTDL_*`)
2. `$XDG_CONFIG_HOME/ytdl/config.toml` (defaults to `~/.config/ytdl/config.toml`)
3. Built-in defaults

| Key | Env var | TOML key | Default | Notes |
|---|---|---|---|---|
| Output directory | `YTDL_OUTPUT_DIR` | `output_dir` | `~/Videos/ytdl` | Where files land. |
| SQLite path | `YTDL_DB_PATH` | `db_path` | `$XDG_DATA_HOME/ytdl/ytdl.db` | Job queue + event log. |
| Worker count | `YTDL_WORKERS` | `workers` | `2` | Concurrent downloads. |
| Cookie browser | `YTDL_COOKIES_BROWSER` | `cookies_browser` | auto-detected | Set explicitly to override. Supported: `chrome`, `firefox`, `brave`, `edge`, `safari`, `opera`, `vivaldi`, `chromium`. See **Authentication** below. |
| Default format | `YTDL_DEFAULT_FORMAT` | `default_format` | `best` | `best`, `1080p`, `720p`, `audio_only`, or any raw yt-dlp format string. |
| Log level | `YTDL_LOG_LEVEL` | `log_level` | `INFO` | Passed to uvicorn by `ytdl serve`. `dev.sh` and the Docker CMD invoke uvicorn directly and ignore this value. |

Example `~/.config/ytdl/config.toml`:

    output_dir = "/srv/media/ytdl"
    workers = 4
    cookies_browser = "firefox"
    default_format = "1080p"

## Authentication

ytdl never sees your YouTube password. It borrows your browser's signed-in session by reading the cookie store at job time. On server start:

1. If `YTDL_COOKIES_BROWSER` (or `cookies_browser` in the config) is set, that browser is used.
2. Otherwise ytdl scans for an installed browser cookie store in priority order: `chrome` → `brave` → `firefox` → `edge` → `safari` → `chromium` → `opera` → `vivaldi`. First hit wins.
3. If nothing is found, downloads run without cookies — most public videos still work; age-restricted, members-only, and Premium formats won't.

Check which browser is being used:

    ytdl cookies status

Override the auto-pick explicitly:

    ytdl cookies use chrome
    # or: firefox, brave, edge, safari, opera, vivaldi, chromium

`cookies use` writes `cookies_browser` into `config.toml`. The CLI (`ytdl get`) picks up the change immediately. A running server reads the config at startup and keeps it for the process lifetime, so restart the server after `cookies use` for queued downloads to use the new browser.

If a download fails with `Sign in to confirm your age` or `Private video`, the chosen browser isn't signed in to YouTube. Switch to one that is.

## yt-dlp's JS runtime (recommended)

YouTube sometimes wraps format URLs with an obfuscated JavaScript `n` parameter that yt-dlp has to solve to get a usable URL. ytdl opts into yt-dlp's `ejs:github` remote components, so on first run the EJS solver script is fetched from GitHub and cached — no manual installation needed, but you do need a JS runtime for yt-dlp to execute the solver.

Install deno once:

    brew install deno                                # macOS
    curl -fsSL https://deno.land/install.sh | sh     # Linux

Confirm it's on `PATH`:

    deno --version

yt-dlp picks it up automatically. Without a runtime you'll see `n challenge solving failed: Some formats may be missing` and the job may fail with `Requested format is not available`. See yt-dlp's [EJS wiki page](https://github.com/yt-dlp/yt-dlp/wiki/EJS) for background.

**If a download fails with `[forbidden] ... Requested format is not available`**, the n-challenge didn't solve. Almost always: install deno and restart serve. Less often: the chosen browser isn't signed in (`ytdl cookies use <browser>`). The error message in the UI suggests both.

## Commands

    ytdl get <url> [-f best|1080p|audio_only|...] [-o <dir>] [--pick 1,3,5-9]
                            # Download one URL synchronously (no server needed).
                            # With --pick, treats the URL as a playlist, probes it,
                            # downloads only the listed 1-based indices/ranges.
    ytdl preview <url>      # Probe a URL and print a numbered table of entries.
                            # Pair with `get --pick` or `queue add --pick`.
    ytdl serve [--host 127.0.0.1] [--port 8765]
                            # Start the API + web UI. Banner prints the
                            # cookies source (auto-detect or explicit).
    ytdl queue ls [--status pending|running|done|failed|canceling|canceled]
                            # List jobs in the queue.
    ytdl queue add <url> [-f ...] [--pick 1,3,5-9]
                            # Enqueue without running the server.
    ytdl queue retry <id>   # Re-enqueue a failed/canceled/done job as a fresh
                            # pending job. Original row stays for audit.
    ytdl queue clear [-d 7] [--yes]
                            # Delete DONE jobs older than --older-than-days
                            # (default 7). Failed/canceled rows stay so you
                            # can triage them.
    ytdl cookies status     # Print the browser ytdl will use (auto-detected
                            # or explicit).
    ytdl cookies use <browser>
                            # Persist a browser choice for auth.

## HTTP API

When `ytdl serve` is running, the API surface is:

| Method | Path | Body / params | Purpose |
|---|---|---|---|
| `POST` | `/jobs` | `{url, format_pref?}` OR `{urls: [...], format_pref?}` | Enqueue a single URL or an array of URLs from a playlist subset. With `urls`, each URL becomes a standalone VIDEO job (no synthetic playlist parent). Returns the new job row. |
| `GET` | `/jobs` | `?status=&limit=200&offset=0` | List jobs (DESC by `created_at`). |
| `GET` | `/jobs/{id}` | — | Single job. 404 if unknown. |
| `DELETE` | `/jobs/{id}` | — | Cancel a job. For a playlist parent, cascades to all children. Returns 204. |
| `POST` | `/jobs/{id}/retry` | — | Create a new PENDING job from a failed/canceled/done one. 400 if not in a retryable state. |
| `GET` | `/jobs/clear/preview` | `?older_than_days=7` | Count of DONE jobs that would be deleted. |
| `POST` | `/jobs/clear` | `?older_than_days=7` | Delete DONE jobs older than the threshold. Failed/canceled stay; children of retained parents stay. Returns `{deleted}`. |
| `POST` | `/preview` | `{url}` | Flat probe: returns `{kind, title, entries}`. `kind` is `"video"` or `"playlist"`. |
| `POST` | `/preview/enrich` | `{urls: [...]}` | Per-URL full probe in parallel (capped at 20 URLs per call, 5 concurrent). Returns `{entries: [{title, duration_s, uploader, thumbnail_url}]}`. |
| `GET` | `/events` | — | Server-Sent Events stream: snapshot, then live lifecycle + progress. Persisted events carry an `id:` so `Last-Event-ID` reconnect replay works. Progress frames are unindexed (they aren't persisted; the next snapshot recovers state). |
| `GET` | `/library` | `?subdir=...` | List files under `output_dir`. Path traversal returns 400. |
| `GET` | `/status` | — | Returns `{cookies_browser, cookies_source}` (`source` is `"explicit"`, `"autodetect"`, or `"none"`). |
| `GET` | `/` | — | Built web UI (only present when `ytdl/web/` exists from a `pnpm build`). |

URL validation rejects non-`http(s)` schemes (`javascript:`, `file:`, etc.) with 422.

## Architecture

The whole thing runs in one Python process. Modules:

| File | Responsibility |
|---|---|
| `ytdl/cli.py` | Typer CLI entrypoint. |
| `ytdl/config.py` | Config resolution (env > TOML > defaults), plus cookies auto-detect fallback. |
| `ytdl/cookies.py` | Browser cookie validation and platform-aware auto-detection. |
| `ytdl/db.py` | SQLite schema + migrations (WAL, FK on). |
| `ytdl/queue.py` | Enqueue, atomic CAS claim, cancel-with-children, progress + metadata writes, retry and sweep. |
| `ytdl/downloader.py` | yt-dlp wrapper: format selector, output template, error classifier, progress throttle, flat probe + full probe. |
| `ytdl/workers.py` | Asyncio supervisor: N workers, retry/rate-limit backoff, playlist enumeration, cancel-aware sleeps. |
| `ytdl/events_bus.py` | In-process pub/sub for SSE with thread-safe publish from worker threads. |
| `ytdl/api/` | FastAPI app factory + routers (`routes_jobs`, `routes_events`, `routes_library`, `routes_preview`) + static UI mount. |
| `web/` | Vite + React + TypeScript + Tailwind. Built bundle is copied into `ytdl/web/` for the API to serve. |

The queue uses an atomic `UPDATE … RETURNING` compare-and-swap so multiple workers can race for the oldest pending job without locks. Playlist expansion runs inside `BEGIN IMMEDIATE` so siblings become claimable as a set. SSE clients get a `snapshot` event on connect followed by live lifecycle and progress events; persisted lifecycle events carry an `id:` so `Last-Event-ID` reconnect replay works.

## Development

Requirements:

- Python 3.12+ via [uv](https://docs.astral.sh/uv/) (manages venv + lockfile)
- Node 22+ via [pnpm](https://pnpm.io/) (for the web UI)
- `ffmpeg` on PATH (yt-dlp uses it to merge separate audio/video streams)
- `deno` on PATH (recommended — yt-dlp uses it to solve YouTube's n-challenge when it appears; see above)

Setup:

    git clone https://github.com/keif/ytdl
    cd ytdl
    uv sync                                   # Python deps + venv
    cd web && pnpm install && pnpm build      # Web UI bundle
    cd ..

Run the dev stack:

    ./dev.sh

That starts uvicorn (with `--reload`) and Vite (port 5174) in parallel. The Vite dev server proxies `/jobs`, `/events`, `/library`, `/preview`, and `/status` to the API.

## Testing

Backend:

    uv run pytest              # full suite (unit + integration), no network
    RUN_E2E=1 uv run pytest    # also runs the opt-in real-YouTube test

Frontend:

    cd web && pnpm test        # Vitest component tests

A Playwright `test:e2e` script exists in `package.json` for future use, but there's no Playwright config or e2e spec yet — running it today will misfire on Vitest tests.

Lint:

    uv run ruff check .

The suite covers unit-level behavior (queue CAS, downloader format/error logic, cancel races, playlist enumeration, cookies auto-detect, retry, clear sweep) and integration roundtrips through the FastAPI app + worker supervisor. One opt-in E2E test actually downloads a small public-domain clip from YouTube and validates the file with `ffprobe`; it's skipped by default so CI stays green if YouTube changes.

## Known limitations / follow-ups

- `bytes_done` reflects the last throttle tick; for very short downloads the UI may briefly show a partial value before the success path snaps it to 100%.
- macOS Chrome cookies are encrypted via the Keychain. yt-dlp's `cookies_from_browser` may pop a Keychain dialog on first read; if you refuse, downloads fail with an auth error.

## License

MIT.
