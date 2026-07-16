# ytdl

Self-hosted yt-dlp queue with a CLI, a local web UI, and a single Docker container for homelab self-host.

- Paste a YouTube URL or playlist URL, see a preview, and download what you want.
- Persistent SQLite-backed job queue with 2–3 parallel workers; jobs survive restarts.
- Auth via your browser's cookies (no passwords stored), so age-restricted and Premium-quality formats just work. The browser is auto-detected on startup.
- Retry failed downloads in one click; sweep old DONE rows from the queue when they pile up.
- Supports yt-dlp's full site catalog (~1,800 sites), not just YouTube.

> **First time?** See [docs/install.md](docs/install.md) for a walkthrough from
> prereqs to your first download, including cookies setup and troubleshooting.

## Quick start (local)

    uv sync
    uv run ytdl get "https://youtu.be/dQw4w9WgXcQ"

That downloads one URL synchronously to `~/Videos/ytdl/`.

To run the API + web UI:

    ./dev.sh
    # API at http://127.0.0.1:8766
    # UI  at http://127.0.0.1:5174 (Vite dev server proxies to the API)

The dev script starts uvicorn with HMR and Vite in parallel; Ctrl+C kills both.

## Quick start (Docker self-host)

    cd docker
    docker compose up -d --build
    # UI + API at http://localhost:8766

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
| Cookie file | `YTDL_COOKIES_FILE` | `cookies_file` | unset | Path to an exported `cookies.txt` (Netscape format). The authentication path for Docker, where no host browser is reachable. `~` is expanded. See **Authentication** below. |
| PO token provider | `YTDL_POT_PROVIDER_URL` | `pot_provider_url` | unset | Base URL of a bgutil PO token provider (e.g. `http://bgutil-provider:4416`). Required when YouTube demands a Proof-of-Origin token and cookies alone return `LOGIN_REQUIRED`. Wired automatically by the Docker compose file. See **PO tokens** below. |
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

### Cookies in Docker (`cookies.txt`)

The browser auto-detect above reads a cookie store from local disk. **Inside a container there is no host browser to read**, so `cookies_browser` can't help — `ytdl cookies status` will report `none detected`, and YouTube's anti-bot gate fails downloads with `Sign in to confirm you're not a bot`. Authenticate with an exported `cookies.txt` instead.

**Easiest path — one command.** If a browser on the host is signed in to YouTube, `yt-dlp` can read its cookie store directly. A helper script does this and writes the file to the location the container auto-detects:

    scripts/export-cookies.sh            # Chrome (default)
    scripts/export-cookies.sh firefox    # or another browser
    cd docker && docker compose up -d    # restart to pick it up

The script writes `docker/data/cookies.txt`. Because `docker/data` is already bind-mounted to `/data` (where the database lives), the app **auto-detects `cookies.txt` beside its database** — no compose edits, no env var. Confirm with `ytdl cookies status` (prints `file: /data/cookies.txt`). On macOS the first read of a Chromium-family store pops a Keychain prompt; approve it. Firefox has no prompt.

**Manual path.** If you'd rather export cookies yourself (browser extension, another machine), drop the file at `docker/data/cookies.txt` and it's auto-detected the same way. To use a path *other* than the auto-detected ones, set `YTDL_COOKIES_FILE` explicitly — `docker/docker-compose.yml` ships a mount + env var pre-wired behind comments for that case.

**Auto-detect locations** (used when `YTDL_COOKIES_FILE` / `cookies_file` is unset): a `cookies.txt` beside the database (the `/data` mount in Docker) is checked first, then `$XDG_CONFIG_HOME/ytdl/cookies.txt`. First one found wins.

`cookies_file` works outside Docker too — set `YTDL_COOKIES_FILE` or `cookies_file` in `config.toml`, or just drop a `cookies.txt` in one of the auto-detect locations. If both a browser and a file are configured, yt-dlp merges cookies from both sources.

YouTube's anti-bot cookies are short-lived — when downloads start failing again with the same "not a bot" message, re-export a fresh file.

#### Cookie rotation — export from an incognito window

`scripts/export-cookies.sh` reads your **live browser profile**, and that's fragile: YouTube continuously rotates the session token (`__Secure-1PSIDTS`) on any active session, so the copy you exported gets invalidated as soon as the browser keeps running. The symptom is downloads failing with **`LOGIN_REQUIRED`** on every player client even though `cookies.txt` is present and full of YouTube cookies — the cookies are there, YouTube just no longer honors them.

The reliable fix is to export from a **private/incognito window and close it immediately**, so nothing can rotate the session afterward:

1. Open a **private/incognito** window and log into YouTube.
2. Open a new tab in that window and go to `youtube.com` (ensures the cookies are set).
3. Export `cookies.txt` **from that incognito window** using a browser extension such as [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc). The `export-cookies.sh` script **cannot** do this — incognito cookies live in memory, not the on-disk store `--cookies-from-browser` reads.
4. **Close the incognito window immediately** (this is the key step — it stops the session from rotating and invalidating what you just exported).
5. Save the file as `docker/data/cookies.txt` and run `cd docker && docker compose up -d`.

A throwaway/secondary Google account is worth using here — YouTube can flag or rate-limit accounts used for automated downloading, and you don't want that to be your primary account. See the yt-dlp [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp) and [exporting YouTube cookies](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

> **Note on rate-limiting (HTTP 429).** Repeated failed attempts from one IP get that IP temporarily throttled, which then causes `LOGIN_REQUIRED`/bot errors regardless of cookie validity and muddies diagnosis. If you hit 429, stop and wait (30+ minutes) before retrying with fresh cookies.

## PO tokens (`bgutil` provider)

As of 2026, YouTube requires a **Proof-of-Origin (PO) token** for most videos *in addition to* cookies. There are two independent gates, and both must pass:

1. **Session auth** — your cookies must be accepted. If they're rejected (stale/rotated), every player client returns `LOGIN_REQUIRED` at the *playability* stage and yt-dlp never even reaches the streaming step. Fix: valid cookies (see [Cookie rotation](#cookie-rotation--export-from-an-incognito-window) above).
2. **Proof-of-Origin** — the *streaming* URLs require a PO token. Without a provider, a verbose probe shows `PO Token Providers: none` and downloads fail with "Sign in to confirm you're not a bot" even when auth passed. Fix: the bgutil provider below.

Because both surface as `LOGIN_REQUIRED` / "not a bot", tell them apart with a verbose probe (`yt-dlp -v ...`): `playability status: LOGIN_REQUIRED` on the clients points at **cookies**; reaching formats but failing with `PO Token Providers: none` points at the **PO token**.

The fix is the [bgutil PO token provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider): a small HTTP server that mints tokens, plus a yt-dlp plugin that consumes them. Both are already wired into this project:

- The plugin (`bgutil-ytdlp-pot-provider`) ships as an app dependency, so yt-dlp auto-discovers it.
- `docker/docker-compose.yml` runs the provider as a `bgutil-provider` sidecar and sets `YTDL_POT_PROVIDER_URL=http://bgutil-provider:4416` on the app container.

So with the Docker setup there's **nothing to configure** — `docker compose up -d --build` brings up both services and downloads mint PO tokens automatically. The header shows a `pot: ✓` chip when a provider is configured. Pair it with a cookies file (above) for the most reliable combination.

**Version alignment:** keep the sidecar image tag in `docker-compose.yml` and the plugin pin in `pyproject.toml` on the same major/minor (both `1.3.x` today).

**Running outside Docker?** Start the provider yourself (`docker run --name bgutil-provider -d --init -p 4416:4416 brainicism/bgutil-ytdlp-pot-provider:1.3.1`) and set `YTDL_POT_PROVIDER_URL=http://127.0.0.1:4416`.

## yt-dlp's JS runtime (recommended)

YouTube sometimes wraps format URLs with an obfuscated JavaScript `n` parameter that yt-dlp has to solve to get a usable URL. ytdl opts into yt-dlp's `ejs:github` remote components, so on first run the EJS solver script is fetched from GitHub and cached — no manual installation needed, but you do need a JS runtime for yt-dlp to execute the solver.

Install deno once:

    brew install deno                                # macOS
    curl -fsSL https://deno.land/install.sh | sh     # Linux

Confirm it's on `PATH`:

    deno --version

yt-dlp picks it up automatically. Without a runtime you'll see `n challenge solving failed: Some formats may be missing` and the job may fail with `Requested format is not available`. See yt-dlp's [EJS wiki page](https://github.com/yt-dlp/yt-dlp/wiki/EJS) for background.

**If a download fails with `[forbidden] ... Requested format is not available`**, the n-challenge didn't solve. Almost always: install deno and restart serve. Less often: the chosen browser isn't signed in (`ytdl cookies use <browser>`). The error message in the UI suggests both.

**If a download fails with `[forbidden] ... Sign in to confirm you're not a bot`**, YouTube served its anti-bot gate and the request was unauthenticated. On a local install, point ytdl at a signed-in browser (`ytdl cookies use <browser>`). In Docker there's no host browser to read — supply an exported `cookies.txt` instead (see [Cookies in Docker](#cookies-in-docker-cookiestxt)).

## Commands

    ytdl get <url> [-f best|1080p|audio_only|...] [-o <dir>] [--pick 1,3,5-9]
                            # Download one URL synchronously (no server needed).
                            # With --pick, treats the URL as a playlist, probes it,
                            # downloads only the listed 1-based indices/ranges.
    ytdl preview <url>      # Probe a URL and print a numbered table of entries.
                            # Pair with `get --pick` or `queue add --pick`.
    ytdl serve [--host 127.0.0.1] [--port 8766]
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
    ytdl cookies status     # Print the cookie sources ytdl will use — the
                            # browser (auto-detected or explicit) and/or the
                            # configured cookies.txt file.
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
| `GET` | `/status` | — | Returns `{cookies_browser, cookies_source, cookies_file, pot_provider_url, ...}` (`source` is `"explicit"`, `"autodetect"`, or `"none"`; `cookies_file` / `pot_provider_url` are the configured values or `null`). |
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
