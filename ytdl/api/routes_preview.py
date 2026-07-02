"""Playlist preview + lazy enrichment endpoints.

POST /preview        -> flat probe; returns kind + title + entries
                        (url, id, title, position).
POST /preview/enrich -> per-URL full probe in parallel; returns duration,
                        uploader, thumbnail.

The split lets the web UI render the picker instantly after a flat probe and
fetch richer per-entry metadata in the background, batched and capped so a
huge playlist doesn't fan out hundreds of yt-dlp calls at once.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from ytdl.db import connect, migrate
from ytdl.downloader import probe, probe_one
from ytdl.library import lookup_by_video_id

router = APIRouter(tags=["preview"])

_MAX_URL_LEN = 4096
_ENRICH_CONCURRENCY = 5  # parallel per-entry probes
_ENRICH_BATCH_MAX = 20  # max URLs per enrich request


class PreviewRequest(BaseModel):
    url: str = Field(min_length=1, max_length=_MAX_URL_LEN)

    @field_validator("url")
    @classmethod
    def http_only(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url must use http or https")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        return v


class DuplicateInfo(BaseModel):
    """Metadata about an existing file that already covers this video.

    Emitted on ``PreviewEntry.already_downloaded`` when the entry's
    ``id`` is in the library index. The UI renders a banner from this so
    the user can decide whether to force a re-download or skip.
    """

    path: str
    title: str | None = None


class PreviewEntry(BaseModel):
    url: str
    id: str | None = None
    title: str | None = None
    position: int | None = None
    # Populated by the /preview route when the entry's video_id is in
    # the library index (either from a previous ytdl run or a manual copy
    # sitting under one of the scan dirs). Absent when no duplicate is
    # detected — the UI treats missing and null identically.
    already_downloaded: DuplicateInfo | None = None


class PreviewResponse(BaseModel):
    kind: str  # "video" or "playlist"
    title: str | None
    entries: list[PreviewEntry]


class EnrichRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=_ENRICH_BATCH_MAX)

    @field_validator("urls")
    @classmethod
    def http_only(cls, v: list[str]) -> list[str]:
        for u in v:
            if not u or len(u) > _MAX_URL_LEN:
                raise ValueError("each url must be 1..4096 chars")
            parsed = urlparse(u)
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                raise ValueError("each url must be http(s) with a host")
        return v


class EnrichedEntry(BaseModel):
    url: str
    title: str | None = None
    duration_s: int | None = None
    uploader: str | None = None
    thumbnail_url: str | None = None
    error: str | None = None  # populated when this single URL's probe failed


class EnrichResponse(BaseModel):
    entries: list[EnrichedEntry]


_PROBE_TIMEOUT_DETAIL = (
    "probe timed out — the site may be slow, blocking us, or require "
    "cookies. Try waiting or check `ytdl cookies status`."
)


@router.post("/preview", response_model=PreviewResponse)
async def post_preview(payload: PreviewRequest, request: Request) -> PreviewResponse:
    cfg = request.app.state.config
    cookies = cfg.cookies_browser
    probe_timeout = cfg.probe_timeout_s
    # probe() shells out to a subprocess (see ytdl._probe_worker), so
    # subprocess.run's own timeout = socket_timeout + 5 reliably OS-kills
    # a hung yt-dlp. The asyncio.wait_for here is belt-and-suspenders for
    # the narrow window between asyncio.to_thread dispatch and subprocess
    # startup; set it +10s so it always fires AFTER the subprocess timeout.
    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(
                probe,
                payload.url,
                cookies_browser=cookies,
                socket_timeout=probe_timeout,
            ),
            timeout=probe_timeout + 10,
        )
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=_PROBE_TIMEOUT_DETAIL) from exc
    except BaseException as exc:
        raise HTTPException(status_code=400, detail=f"probe failed: {exc}") from exc

    kind = "playlist" if info.get("_type") == "playlist" else "video"
    title = info.get("title")
    raw_entries = info.get("entries") if kind == "playlist" else [info]

    # Open one connection to the library index for the whole preview.
    # Skipped when dedup is disabled OR when the URL is a playlist with no
    # entries (walking the list would just be zero lookups anyway).
    dedup_enabled = getattr(cfg, "dedup_enabled", True)
    lib_conn = None
    if dedup_enabled:
        try:
            lib_conn = connect(cfg.db_path)
            migrate(lib_conn)
        except BaseException as exc:
            log_err = exc
            lib_conn = None
            # Non-fatal — preview still works without dedup annotations.
            # A failed DB open here shouldn't take down the whole endpoint.
            import logging as _logging

            _logging.getLogger(__name__).warning(
                "preview: skipping dedup lookup, DB open failed: %s", log_err
            )
    try:
        entries: list[PreviewEntry] = []
        for idx, entry in enumerate(raw_entries or []):
            if not isinstance(entry, dict):
                continue
            entry_url = entry.get("webpage_url") or entry.get("url") or ""
            if not entry_url:
                continue
            entry_id = entry.get("id")
            already: DuplicateInfo | None = None
            if lib_conn is not None and entry_id:
                hit = lookup_by_video_id(lib_conn, entry_id)
                if hit is not None:
                    already = DuplicateInfo(
                        path=hit["path"], title=hit.get("title")
                    )
            entries.append(
                PreviewEntry(
                    url=entry_url,
                    id=entry_id,
                    title=entry.get("title"),
                    position=entry.get("playlist_index") or (idx + 1),
                    already_downloaded=already,
                )
            )
    finally:
        if lib_conn is not None:
            lib_conn.close()
    return PreviewResponse(kind=kind, title=title, entries=entries)


@router.post("/preview/enrich", response_model=EnrichResponse)
async def post_enrich(payload: EnrichRequest, request: Request) -> EnrichResponse:
    cfg = request.app.state.config
    cookies = cfg.cookies_browser
    probe_timeout = cfg.probe_timeout_s

    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def fetch_one(url: str) -> EnrichedEntry:
        # Per-URL timeout means one slow video doesn't block the whole
        # batch — the other entries still resolve. probe_one() shells
        # out to a subprocess, so the timeout actually kills the work
        # (no leaked executor threads). See post_preview for the wait_for
        # vs subprocess-timeout layering.
        async with sem:
            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(
                        probe_one,
                        url,
                        cookies_browser=cookies,
                        socket_timeout=probe_timeout,
                    ),
                    timeout=probe_timeout + 10,
                )
            except TimeoutError:
                return EnrichedEntry(url=url, error="probe timeout")
            except BaseException as exc:
                return EnrichedEntry(url=url, error=str(exc))
        return EnrichedEntry(
            url=url,
            title=info.get("title"),
            duration_s=int(info["duration"]) if info.get("duration") else None,
            uploader=info.get("uploader") or info.get("channel"),
            thumbnail_url=info.get("thumbnail"),
        )

    results = await asyncio.gather(*[fetch_one(u) for u in payload.urls])
    return EnrichResponse(entries=list(results))
