# Changelog

## 0.1.0 — 2026-06-16

Initial release.

- Python CLI (`ytdl get`, `ytdl queue`, `ytdl cookies`).
- FastAPI server with SQLite-backed queue, SSE progress stream, 2-worker pool.
- Vite/React web UI for submit + queue.
- Single-container Docker image with bundled ffmpeg.
- yt-dlp library integration with retry, rate-limit backoff, cancellation,
  and browser-cookie auth.
