# Changelog

## 0.2.0 — 2026-06-22

Substantial user-experience work on top of the v0.1.0 foundation. 32 PRs merged.

### Web UI

- **Preview on paste.** URL input debounces 500ms and fetches `/preview`. Single videos render an inline card with thumbnail / title / uploader / duration; playlists render the picker inline (no modal). Lazy enrichment streams per-entry details in batches. Confirmed by an explicit Download button — nothing enqueues silently.
- **Playlist picker.** Pick a subset before downloading. All entries checked by default; select-all / deselect-all. Tracks selection by row index so duplicate URLs are independently pickable. Resets selection when the preview entries change.
- **Retry button** on failed / canceled / done rows. Creates a fresh PENDING job from the original URL; original row stays for audit.
- **Speed + ETA on running rows.** Renders `5.2 MB/s · ETA 2m 05s` under the progress bar. Byte-prefix aware (B/s → KB/s → MB/s → GB/s).
- **Relative timestamps** per row (`finished 5m ago`, `started 12s ago`). Absolute timestamp on hover. Attempt count appended when `> 1`.
- **Clear N done jobs** button. Defaults to "older than 7 days." Failed and canceled rows stay so you can triage them. Server-side guard preserves playlist children whose parent is being retained.
- **Granular SSE state patches.** Progress events update the row in place from event data — no `/jobs` fetch. The bar moves as fast as the bus fires. Non-progress events route through a single `refresh()`.
- **Header chips** for runtime status: `cookies: chrome (auto)` / `deno: ✓` / `ffmpeg: ✓`. Each shows missing in amber / red with a hover tooltip explaining what the binary does.

### CLI

- **`ytdl preview <url>`** prints a numbered table of playlist entries.
- **`ytdl get <url> --pick 1,3,5-9`** downloads a subset. Same `--pick` syntax on `queue add`.
- **`ytdl queue retry <id>`** mirrors the UI retry button.
- **`ytdl queue clear [--older-than-days 7] [--yes]`** sweeps old DONE jobs.
- **`ytdl cookies status`** prints the browser the server will use (explicit vs. autodetected).
- **`ytdl queue ls`** gains a `progress` column matching the UI's running-row display.
- **`ytdl serve`** warns at startup if the web bundle is missing and prints the build command, instead of silently serving only the API.

### Backend

- **`POST /preview`** flat-extracts metadata; **`POST /preview/enrich`** does per-URL full probes in parallel (capped at 5 concurrent, 20 per batch).
- **`POST /jobs/{id}/retry`** + **`POST /jobs/clear`** + **`GET /jobs/clear/preview`** + **`GET /status`** endpoints.
- **`POST /jobs`** now accepts `{urls: [...]}` for playlist subsets.
- **SSE persisted events carry `id:`** so `EventSource` advances `Last-Event-ID` for proper reconnect replay.
- **Cascade cancel.** `DELETE /jobs/{id}` on a playlist parent flips children too. Cancel-aware retry sleeps (200ms poll instead of waiting for the full backoff). Reaper uses CAS to avoid TOCTOU races with concurrent cancels.
- **Playlist expansion in `BEGIN IMMEDIATE`** so sibling children become claimable atomically as a set.
- **Cookie browser auto-detect at startup** (chrome → brave → firefox → edge → safari → chromium → opera → vivaldi). Honors `XDG_CONFIG_HOME` on Linux. Handles Chromium's `Default/Network/Cookies` layout introduced in Chrome 96.
- **`/status`** also surfaces deno + ffmpeg presence so the UI can warn proactively.

### yt-dlp integration

- **EJS challenge solver opted in** (`remote_components=["ejs:github"]`). Without this, recent yt-dlp versions log `n challenge solving failed` and YouTube returns no usable formats.
- **`noplaylist=True`** so URLs like `?v=X&list=RD...` (radio mix shares) download just the single video instead of expanding the radio mix.
- **FORBIDDEN classification** for HTTP 403, "Requested format is not available", and "No video formats found." Job error includes the actionable hint pointing at deno install + cookies setup.
- **yt-dlp bumped** from `>=2024.12.13` to `>=2026.6.9`.

### Misc

- **Dependabot configured** for pip / npm / docker / github-actions.
- **Dependency bumps:** fastapi 0.115→0.137, uvicorn 0.32→0.49, typer 0.13→0.26, httpx 0.27→0.28, rich 13→15, react 18→19.
- **Dockerfile** switched from corepack to direct `npm install -g pnpm@10.15.0` so the next Node base bump won't break the build.
- **Library route** uses resolved paths so files on symlinked output_dirs (e.g., macOS `/tmp`) list correctly.
- **Tests:** 197 backend + 2 skipped, 44 frontend, ruff clean.

## 0.1.0 — 2026-06-16

Initial release.

- Python CLI (`ytdl get`, `ytdl queue`, `ytdl cookies`).
- FastAPI server with SQLite-backed queue, SSE progress stream, 2-worker pool.
- Vite/React web UI for submit + queue.
- Single-container Docker image with bundled ffmpeg.
- yt-dlp library integration with retry, rate-limit backoff, cancellation,
  and browser-cookie auth.
