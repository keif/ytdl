# ytdl — Design Spec

**Status:** Draft
**Date:** 2026-06-16
**Owner:** keif

## Overview

A self-hosted YouTube (and general-purpose) video downloader for personal use. Accepts a single URL or a playlist URL and downloads at the highest quality the user is entitled to. Wraps `yt-dlp` end-to-end in Python with a thin FastAPI server, a Vite/React web UI, and a Typer CLI. Ships as a single Docker container for homelab self-hosting.

## Goals

- Download single videos and playlists from YouTube at the best available quality, including Premium-exclusive formats when the user's browser is signed in.
- Provide both a CLI (`ytdl get <url>`) and a local web UI (paste URL, see queue and progress).
- Persistent queue with 2–3 parallel workers; jobs survive process restarts.
- Support yt-dlp's full site catalog (~1800 sites) for non-YouTube URLs at the same surface, since it costs nothing.
- One process, one container, one SQLite file. No Redis, no broker.

## Non-Goals

- Multi-user accounts. Single-user, self-host. No login on the web UI itself; auth to YouTube is via the user's browser cookies (`yt-dlp --cookies-from-browser`).
- Storing YouTube passwords. We never see them.
- Horizontal scaling. Worker pool is in-process.
- Format conversion beyond what yt-dlp does in post-processing (merge audio/video, embed metadata/thumbnail). No re-encoding pipelines.
- A public/internet-facing deployment. Designed for LAN or single-host use.

## Form Factor

CLI + web UI, both backed by the same FastAPI process. The CLI can talk to a running server (`ytdl queue ls`) or run a one-shot download directly without a server (`ytdl get <url>`).

## Repo Layout

```
ytdl/
  pyproject.toml          # uv-managed; pins yt-dlp, fastapi, uvicorn, sqlmodel, typer
  ytdl/                   # Python package
    __init__.py
    cli.py                # Typer CLI: ytdl get, ytdl serve, ytdl queue, ytdl cookies
    config.py             # XDG config (~/.config/ytdl/config.toml)
    db.py                 # SQLite schema + migrations
    queue.py              # enqueue / claim / update / cancel
    downloader.py         # yt-dlp wrapper: format selection, progress hook, post-processing
    workers.py            # asyncio supervisor: N workers pulling from queue
    cookies.py            # cookies-from-browser helper
    api/
      __init__.py         # FastAPI app factory; lifespan starts worker supervisor
      routes_jobs.py      # POST /jobs, GET /jobs, GET /jobs/{id}, DELETE /jobs/{id}
      routes_events.py    # GET /events (SSE)
      routes_library.py   # GET /library (browse downloaded files)
    web/                  # built frontend served as static
  web/                    # frontend source (Vite + React + Tailwind)
    src/...
    package.json
  docker/
    Dockerfile
    docker-compose.yml
  dev.sh
  README.md
  tests/
    unit/
    integration/
    e2e/
```

Each unit has one job:
- `downloader` knows yt-dlp; nothing else.
- `queue` is pure SQLite I/O.
- `workers` orchestrates queue → downloader.
- `api` is HTTP-only and delegates to queue.
- `cli` is HTTP-free for direct mode; talks to the API for queue inspection.

## Stack

- **Python 3.12+**, managed with `uv`.
- **FastAPI + Uvicorn** for HTTP and SSE.
- **SQLModel** (SQLAlchemy 2.x core under the hood) for the data layer.
- **yt-dlp** as a library (not a subprocess).
- **Typer** for the CLI.
- **Vite + React + Tailwind** for the web UI (matches the workspace pattern in `image-optimizer`).
- **ffmpeg** is a runtime dep (bundled in the Docker image, required for merging audio/video tracks).

## Auth Scope

Public videos, age-restricted videos, and Premium-quality formats (1080p+, AV1, members-only). All of it via `--cookies-from-browser`, which reads the cookie store from the user's local browser. One-time setup with `ytdl cookies use chrome` (also supports firefox, brave, edge). No password storage anywhere. Cookies are read at job-start time, not cached by us.

## Output Layout

Flat configurable folder, default `~/Videos/ytdl/`. Inside it:

```
~/Videos/ytdl/
  Some Title [dQw4w9WgXcQ].mp4
  Some Title [dQw4w9WgXcQ].info.json   (yt-dlp sidecar, optional, off by default)
  <Playlist Name>/
    01 - First Video [vid1].mp4
    02 - Second Video [vid2].mp4
```

- Titles run through yt-dlp's `restrict_filenames` to strip path-traversal and shell-hostile characters.
- `[video_id]` suffix makes deduplication on re-runs reliable.
- Playlist children land in a subfolder named after the playlist, prefixed with the playlist index for natural sort order.

## Data Model

SQLite at `~/.local/share/ytdl/ytdl.db` (host) or `/data/ytdl.db` (container).

```
jobs
  id              TEXT PK   (ULID — sortable, URL-safe)
  url             TEXT
  kind            TEXT      ('video' | 'playlist')
  parent_job_id   TEXT NULL (set on playlist children, FK → jobs.id)
  status          TEXT      ('pending' | 'running' | 'done' | 'failed' | 'canceled' | 'canceling')
  format_pref     TEXT      ('best' | '1080p' | '720p' | 'audio_only' | ...)
  output_dir      TEXT      (resolved at enqueue time)
  output_path     TEXT NULL (final file path on completion)
  title           TEXT NULL
  video_id        TEXT NULL
  uploader        TEXT NULL
  duration_s      INTEGER NULL
  filesize_bytes  INTEGER NULL
  bytes_done      INTEGER NULL  (live, throttled to ~1 Hz)
  speed_bps       INTEGER NULL  (live)
  eta_s           INTEGER NULL  (live)
  error           TEXT NULL
  attempts        INTEGER NOT NULL DEFAULT 0
  created_at      INTEGER NOT NULL  (unix ms)
  started_at      INTEGER NULL
  finished_at     INTEGER NULL

INDEX jobs_status_created (status, created_at)
INDEX jobs_parent (parent_job_id)

events
  id              INTEGER PK AUTOINCREMENT
  job_id          TEXT NOT NULL
  kind            TEXT       ('enqueued' | 'started' | 'finished' | 'failed' | 'canceled' | 'log')
  payload_json    TEXT
  created_at      INTEGER NOT NULL
```

Design choices worth flagging:

- **Playlists become a parent job + N child jobs.** The parent completes when all children reach a terminal state. Per-video retry, partial-failure UX, and progress aggregation all fall out naturally. Enumeration uses `extract_flat='in_playlist'` for fast metadata only.
- **Progress is written to the `jobs` row, not to `events`.** The `events` table is append-only and tracks state transitions only (one row per change). Live progress is throttled to ~1 Hz on the row and pushed straight to SSE without a DB write fanout.

## Data Flow

### Submitting a single video

1. `POST /jobs {url, format_pref?}` (or `ytdl get <url>` directly).
2. `queue.enqueue()` writes a `jobs` row with `status='pending'`, emits an `enqueued` event, returns `job_id`.
3. The worker supervisor (started by FastAPI's lifespan, also runnable from the CLI) holds N=2 asyncio workers. Each worker claims a job with compare-and-swap:
   ```sql
   UPDATE jobs SET status='running', started_at=? WHERE id=? AND status='pending'
   ```
   to allow safe concurrent claims without explicit locking.
4. Worker calls `downloader.download(job)`:
   - Builds yt-dlp `YoutubeDL` options (format, output template, cookies, post-processors).
   - Runs `ydl.extract_info(url, download=True)` inside `asyncio.to_thread`.
   - yt-dlp's `progress_hooks` callback updates `bytes_done/speed/eta` on the row (throttled) and pushes an SSE message.
5. On success: `status='done'`, `output_path`, `finished_at` set; `finished` event emitted.
6. On failure: see Error Handling.

### Submitting a playlist

1. `POST /jobs` creates a parent job with `kind='playlist'`, `status='pending'`.
2. A worker claims the parent and calls `extract_info(url, download=False, process=False)` with `extract_flat='in_playlist'`.
3. For each entry, enqueue a child job with `parent_job_id` set. The parent's `format_pref` propagates to every child; each child's `output_dir` is set to a subfolder named after the playlist (created lazily on first child completion).
4. The parent stays `running` until all children reach a terminal state, then transitions to `done`. Failed children do *not* fail the parent; the parent surfaces a count of failures in the UI.

### SSE stream

- `GET /events` opens one stream multiplexed for all jobs. Clients filter by `job_id` if interested in a subset.
- On connect, the server sends a `snapshot` event summarizing all non-terminal jobs.
- Reconnect: client sends `Last-Event-ID`; server replays from the `events` table since that ID. Progress updates are *not* replayed (they aren't in `events`); state is recovered from the `jobs` snapshot.
- Per-connection backpressure: messages older than 30s are dropped rather than buffered unbounded.

### Cancellation

`DELETE /jobs/{id}` transitions the row to `canceling`. The downloader checks a per-job cancel flag inside the progress hook and raises a `DownloadCancelled` exception, which yt-dlp converts to a clean abort. Partial files (`.part`) are removed; row transitions to `canceled`.

### Cookies

`ytdl cookies use <browser>` invokes yt-dlp's browser-cookie reader, validates it can produce a valid cookie jar, and writes the chosen browser into config. The downloader passes `cookies_from_browser=(browser,)` on every job. Nothing is persisted by us beyond the chosen browser name.

## Error Handling

| yt-dlp situation | Detection | Our response |
|---|---|---|
| Transient network (timeout, conn reset, HTTP 5xx) | `DownloadError` subclass / message match | Retry ≤ 2× with backoff (2s, 8s), then `failed`. |
| HTTP 429 (rate-limited) | `DownloadError` matching 429 | Backoff 60s, then 1 retry. UI: "rate-limited — will retry". |
| Age-gated, no cookies set | `ExtractorError` "Sign in to confirm your age" | `failed`, no retry. Message: "Set up cookies: `ytdl cookies use chrome`". |
| Private / members-only | `ExtractorError` "Private video" / "Members-only" | `failed`, no retry. Message: "Not accessible to current cookies." |
| Geo-blocked | `GeoRestrictedError` | `failed`, no retry. Include country list when yt-dlp provides one. |
| Video removed / unavailable | `ExtractorError` "Video unavailable" | `failed`, no retry. In playlists: parent continues. |
| Disk full | `OSError` `ENOSPC` | `failed` with clear message; **pause the worker pool**; surface a UI banner. |
| Disk write permission | `PermissionError` | `failed` with the offending path. No retry. |
| Requested format unavailable | Format selector returns nothing | Auto-fallback to `best`. Log a `log` event. Not a failure. |
| Worker crash | Supervisor catches task exception | Requeue (`pending`, keep `attempts`); remove `.part` file. |
| Process crash / restart | Startup sweep: `running` rows → `pending` if `attempts < max`, else `failed`. Clean stale `.part`. | Same as worker crash. |
| Cancellation during download | Cancel flag → `DownloadCancelled` | Remove partials, `status='canceled'`. |

Other edge-case rules:

- **Output collisions:** if `Title [video_id].mp4` exists in `output_dir`, skip and mark `done` with a `log` event ("already downloaded").
- **Playlist mid-flight changes:** enumeration is a snapshot. Re-submitting a playlist URL creates a new parent and re-enumerates.
- **Filename safety:** yt-dlp `restrict_filenames=True` strips path-traversal and shell metacharacters.
- **URL validation:** at `POST /jobs` we require `http(s)://`. We don't restrict by host — yt-dlp supports the rest of the catalog for free.
- **XSS into UI:** all job fields are rendered as React text nodes (never raw HTML). API responses additionally strip control characters from titles.

## Testing

Pyramid: heavy unit, focused integration, opt-in E2E smoke.

### Unit (`tests/unit/`) — every commit, no network

| Target | Coverage |
|---|---|
| `queue.py` | enqueue → claim → finish; CAS claim under simulated concurrency; cancel transitions; revive-on-startup for orphaned `running` jobs |
| `downloader.py` | format-string resolution; output template with `restrict_filenames`; error classification (transient / permanent / auth / disk) — table-driven against fixture exceptions; progress-hook throttling |
| `db.py` | schema migrations up/down on tempfile; indexes present; ULID monotonic |
| `cookies.py` | browser detection; fallback when browser absent; path validation |
| `config.py` | XDG resolution; defaults; env overrides; malformed-TOML rejection |
| `api/routes_jobs.py` | reject `javascript:`, `file:`, bare strings; empty playlists; pagination |
| Edge cases | NULL/empty URL, 10kB URL string, unicode titles, RTL filenames, `../` and `%2e%2e` in titles, HTML/JS in titles, SQL-injection-y URLs (verifies parameterization) |

### Integration (`tests/integration/`) — every commit, no network

- Downloader against a **fixture HTTP server** serving canned manifest + segment files mimicking YouTube. Verifies the full library call path without YouTube contact.
- Round-trip `POST /jobs → worker pickup → SSE → finished` against an in-process `httpx.AsyncClient`, downloader monkey-patched to a fake that writes bytes and exits.
- Crash recovery: cancel mid-job, restart supervisor, assert row revived and `.part` cleaned.

### E2E (`tests/e2e/`) — opt-in via `RUN_E2E=1`

- One real public CC-licensed YouTube video to tempdir. Assert file exists, size > 0, `ffprobe` reports a valid container. Skipped by default — keeps CI green when YouTube changes.

### Frontend (`web/tests/`)

- Component-level (Vitest + Testing Library): job-row, queue, SSE-driven progress hook with mocked `EventSource`.
- One Playwright smoke: load page, submit a fake URL (API mocked), confirm progress bar advances.

### Explicitly skipped

- Load tests. Personal-scale tool; 2–3 concurrent jobs.
- Cross-browser frontend tests beyond the Playwright smoke.

## Distribution

- **Local dev:** `./dev.sh` starts uvicorn + Vite dev server with HMR.
- **CLI install:** `uv tool install ytdl` (or `pipx install ytdl`) from the local repo for now.
- **Self-host:** `docker compose up -d`. Single service. Mounts `~/Videos/ytdl` for output, `~/.local/share/ytdl` for the SQLite file. `ffmpeg` baked into the image.

## Open Questions

None blocking implementation. Items to revisit after first usage:

- Whether `audio_only` (extract native audio container — M4A or Opus, no re-encode) is common enough to elevate from a `format_pref` value to a top-level UI toggle.
- Whether we want per-job output-dir override in the web UI (currently CLI-only via `-o`).
- Whether to add a "re-download" action that bypasses the dedupe-by-filename skip.
