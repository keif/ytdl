# ytdl

Self-hosted yt-dlp queue with a CLI, a local web UI, and a single Docker container for homelab self-host.

- Paste a YouTube URL or playlist URL, get the highest-quality download you can.
- Persistent SQLite-backed job queue with 2-3 parallel workers; jobs survive restarts.
- Auth via your browser's cookies (no passwords stored), so age-restricted and Premium-quality formats just work.
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
| Cookie browser | `YTDL_COOKIES_BROWSER` | `cookies_browser` | unset | `chrome`, `firefox`, `brave`, `edge`, `safari`, `opera`, `vivaldi`, `chromium`. |
| Default format | `YTDL_DEFAULT_FORMAT` | `default_format` | `best` | `best`, `1080p`, `720p`, `audio_only`, or any raw yt-dlp format string. |
| Log level | `YTDL_LOG_LEVEL` | `log_level` | `INFO` | Passed to uvicorn by `ytdl serve`. `dev.sh` and the Docker CMD invoke uvicorn directly and ignore this value. |

Example `~/.config/ytdl/config.toml`:

    output_dir = "/srv/media/ytdl"
    workers = 4
    cookies_browser = "firefox"
    default_format = "1080p"

## Authentication (age-restricted, Premium, members-only)

ytdl never sees your YouTube password. It borrows your browser's signed-in session by reading the cookie store at job time:

    ytdl cookies use chrome
    # or: firefox, brave, edge, safari, opera, vivaldi, chromium

That writes the chosen browser into `config.toml`. The CLI (`ytdl get`) picks up the change immediately. A running server (`ytdl serve` / `dev.sh` / Docker) reads the config at startup and keeps it for the process lifetime, so restart the server after `cookies use` for queued downloads to use the new browser.

If a download fails with `Sign in to confirm your age` or `Private video`, run `cookies use` to point at the browser where you're actually signed in.

## Note on yt-dlp + JavaScript runtimes

YouTube's newer extractors evaluate JavaScript that yt-dlp delegates to an external runtime (`deno` by default). Without one, downloads still work but some formats may be unavailable (e.g., specific premium codecs). Install deno once:

    brew install deno                                # macOS
    curl -fsSL https://deno.land/install.sh | sh     # Linux

yt-dlp picks it up automatically. No ytdl config change required.

## Commands

    ytdl get <url> [-f best|1080p|audio_only|...] [-o <dir>]
                            # Download one URL synchronously (no server needed).
    ytdl serve [--host 127.0.0.1] [--port 8765]
                            # Start the API + web UI.
    ytdl queue ls [--status pending|running|done|failed|canceling|canceled]
                            # List jobs in the queue.
    ytdl queue add <url> [-f best|1080p|audio_only|...]
                            # Enqueue without running the server (jobs sit until `serve` picks them up).
    ytdl cookies use <browser>
                            # Persist a browser choice for auth.

## HTTP API

When `ytdl serve` is running, the API surface is:

| Method | Path | Body / params | Purpose |
|---|---|---|---|
| `POST` | `/jobs` | `{url, format_pref?}` | Enqueue a video or playlist URL. Returns the new job row. |
| `GET` | `/jobs` | `?status=&limit=200&offset=0` | List jobs (DESC by `created_at`). |
| `GET` | `/jobs/{id}` | — | Single job. 404 if unknown. |
| `DELETE` | `/jobs/{id}` | — | Cancel a job. For a playlist parent, cascades to all children. Returns 204. |
| `GET` | `/events` | — | Server-Sent Events stream: snapshot, then live lifecycle + progress. Replays persisted events when a client supplies a `Last-Event-ID` header; live events don't carry IDs yet (see [#3](https://github.com/keif/ytdl/issues/3)). |
| `GET` | `/library` | `?subdir=...` | List files under `output_dir`. Path traversal returns 400. |
| `GET` | `/` | — | Built web UI (only present when `ytdl/web/` exists from a `pnpm build`). |

URL validation rejects non-`http(s)` schemes (`javascript:`, `file:`, etc.) with 422.

## Architecture

The whole thing runs in one Python process. Modules:

| File | Responsibility |
|---|---|
| `ytdl/cli.py` | Typer CLI entrypoint. |
| `ytdl/config.py` | Config resolution (env > TOML > defaults). |
| `ytdl/db.py` | SQLite schema + migrations (WAL, FK on). |
| `ytdl/queue.py` | Enqueue, atomic CAS claim, cancel-with-children, progress + metadata writes. |
| `ytdl/downloader.py` | yt-dlp wrapper: format selector, output template, error classifier, progress throttle, probe. |
| `ytdl/workers.py` | Asyncio supervisor: N workers, retry/rate-limit backoff, playlist enumeration, cancel-aware sleeps. |
| `ytdl/events_bus.py` | In-process pub/sub for SSE with thread-safe publish from worker threads. |
| `ytdl/api/` | FastAPI app factory + `/jobs`, `/events`, `/library` routers + static UI mount. |
| `web/` | Vite + React + TypeScript + Tailwind. Built bundle is copied into `ytdl/web/` for the API to serve. |

The queue uses an atomic `UPDATE … RETURNING` compare-and-swap so multiple workers can race for the oldest pending job without locks. Playlist expansion runs inside `BEGIN IMMEDIATE` so siblings become claimable as a set. SSE clients get a `snapshot` event on connect, then live events, and can resume via `Last-Event-ID` after a disconnect.

## Development

Requirements:

- Python 3.12+ via [uv](https://docs.astral.sh/uv/) (manages venv + lockfile)
- Node 22+ via [pnpm](https://pnpm.io/) (for the web UI)
- `ffmpeg` on PATH (yt-dlp uses it to merge separate audio/video streams)
- `deno` on PATH (optional; some YouTube extractors need it — see above)

Setup:

    git clone https://github.com/keif/ytdl
    cd ytdl
    uv sync                                   # Python deps + venv
    cd web && pnpm install && pnpm build      # Web UI bundle
    cd ..

Run the dev stack:

    ./dev.sh

That starts uvicorn (with `--reload`) and Vite (port 5174) in parallel. The Vite dev server proxies `/jobs`, `/events`, and `/library` to the API.

## Testing

Backend:

    uv run pytest              # full suite (unit + integration), no network
    RUN_E2E=1 uv run pytest    # also runs the opt-in real-YouTube test

Frontend:

    cd web && pnpm test        # Vitest component tests

A Playwright `test:e2e` script exists in `package.json` for future use, but there's no Playwright config or e2e spec yet — running it today will misfire on Vitest tests.

Lint:

    uv run ruff check .

The suite covers unit-level behavior (queue CAS, downloader format/error logic, cancel races, playlist enumeration) and integration roundtrips through the FastAPI app + worker supervisor. One opt-in E2E test actually downloads a small public-domain clip from YouTube and validates the file with `ffprobe`; it's skipped by default so CI stays green if YouTube changes.

## Known limitations / follow-ups

- SSE live events don't carry an `id:` field yet. `EventSource` only advances `Last-Event-ID` on events that have one, so after a disconnect mid-job the browser has no cursor to send and the replay path can't fill the gap. The frontend currently masks this by refreshing the full job list on every event. Tracked as [issue #3](https://github.com/keif/ytdl/issues/3).
- The web UI refreshes the full job list on every SSE event rather than patching state. Fine at single-user scale; would want granular updates for hundreds of in-flight jobs.
- `bytes_done` reflects the last throttle tick; for very short downloads the UI may show a partial value briefly before the success path snaps it to 100%.

## License

MIT.
