#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

trap 'kill 0' EXIT

(uv run uvicorn ytdl.api:app_factory --factory --reload --host 127.0.0.1 --port 8766 2>&1 | sed 's/^/[api]  /') &
(cd web && pnpm dev 2>&1 | sed 's/^/[web]  /') &
wait
