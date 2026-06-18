# syntax=docker/dockerfile:1.7
FROM node:26-alpine AS web-build
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml* ./
RUN corepack enable && corepack prepare pnpm@10.15.0 --activate \
    && (pnpm install --frozen-lockfile || pnpm install)
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project || uv sync --no-dev --no-install-project
COPY ytdl/ ./ytdl/
COPY --from=web-build /ytdl/web /app/ytdl/web
RUN uv sync --frozen --no-dev || uv sync --no-dev

ENV YTDL_DB_PATH=/data/ytdl.db
ENV YTDL_OUTPUT_DIR=/downloads
VOLUME ["/data", "/downloads"]
EXPOSE 8765
CMD ["/app/.venv/bin/uvicorn", "ytdl.api:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8765"]
