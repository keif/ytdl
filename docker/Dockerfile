# syntax=docker/dockerfile:1.7
# deno supplies the JS runtime yt-dlp uses to solve YouTube's `n` challenge
# (EJS remote components). Without it, downloads can fail with "Requested
# format is not available" even when cookies are valid. Pull the standalone
# binary from deno's official bin image as a named stage — the version has to
# interpolate in a FROM line (an ARG inside `COPY --from=` doesn't expand).
# Bump DENO_VERSION to upgrade; tags: https://hub.docker.com/r/denoland/deno/tags
ARG DENO_VERSION=2.4.0
FROM denoland/deno:bin-${DENO_VERSION} AS deno

FROM node:22-alpine AS web-build
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml* ./
# node:26-alpine dropped corepack from the default install, so install
# pnpm directly via npm instead. Pin to the same version the lockfile
# was generated against to keep installs deterministic.
RUN npm install -g pnpm@10.15.0 \
    && (pnpm install --frozen-lockfile || pnpm install)
COPY web/ ./
RUN pnpm build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
# JS runtime for yt-dlp's n-challenge — see the deno stage at the top.
COPY --from=deno /deno /usr/local/bin/deno
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project || uv sync --no-dev --no-install-project
COPY ytdl/ ./ytdl/
COPY --from=web-build /ytdl/web /app/ytdl/web
RUN uv sync --frozen --no-dev || uv sync --no-dev

ENV YTDL_DB_PATH=/data/ytdl.db
ENV YTDL_OUTPUT_DIR=/downloads
VOLUME ["/data", "/downloads"]
EXPOSE 8766
CMD ["/app/.venv/bin/uvicorn", "ytdl.api:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8766"]
