# ytdl

Self-hosted yt-dlp queue. CLI + local web UI, single Docker container.

## Quick start (local)

    uv sync
    uv run ytdl get "https://youtu.be/dQw4w9WgXcQ"

Or run the server + UI:

    ./dev.sh
    # API at http://127.0.0.1:8765
    # UI at  http://127.0.0.1:5174 (proxies to API)

## Quick start (self-host)

    cd docker
    docker compose up -d --build
    # UI/API at http://localhost:8765

Downloads land in `~/Videos/ytdl` by default. Configurable via `config.toml`
at `~/.config/ytdl/config.toml` or env (`YTDL_OUTPUT_DIR`, `YTDL_WORKERS`, ...).

## Authentication (Premium / age-restricted)

    ytdl cookies use chrome
    # or: firefox, brave, edge, safari, opera, vivaldi, chromium

Reads your browser's cookie store at job time — no passwords stored.

## Commands

    ytdl get <url>          # download one URL synchronously
    ytdl serve              # start API + web UI
    ytdl queue ls           # list queued jobs
    ytdl queue add <url>    # enqueue without serving
    ytdl cookies use <name> # pick a browser for auth

## License

MIT.
