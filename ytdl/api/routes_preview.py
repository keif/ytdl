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

from ytdl.downloader import probe, probe_one

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


class PreviewEntry(BaseModel):
    url: str
    id: str | None = None
    title: str | None = None
    position: int | None = None


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


@router.post("/preview", response_model=PreviewResponse)
async def post_preview(payload: PreviewRequest, request: Request) -> PreviewResponse:
    cfg = request.app.state.config
    cookies = cfg.cookies_browser
    try:
        info = await asyncio.to_thread(probe, payload.url, cookies_browser=cookies)
    except BaseException as exc:
        raise HTTPException(status_code=400, detail=f"probe failed: {exc}") from exc

    kind = "playlist" if info.get("_type") == "playlist" else "video"
    title = info.get("title")
    raw_entries = info.get("entries") if kind == "playlist" else [info]
    entries: list[PreviewEntry] = []
    for idx, entry in enumerate(raw_entries or []):
        if not isinstance(entry, dict):
            continue
        entry_url = entry.get("webpage_url") or entry.get("url") or ""
        if not entry_url:
            continue
        entries.append(
            PreviewEntry(
                url=entry_url,
                id=entry.get("id"),
                title=entry.get("title"),
                position=entry.get("playlist_index") or (idx + 1),
            )
        )
    return PreviewResponse(kind=kind, title=title, entries=entries)


@router.post("/preview/enrich", response_model=EnrichResponse)
async def post_enrich(payload: EnrichRequest, request: Request) -> EnrichResponse:
    cfg = request.app.state.config
    cookies = cfg.cookies_browser

    sem = asyncio.Semaphore(_ENRICH_CONCURRENCY)

    async def fetch_one(url: str) -> EnrichedEntry:
        async with sem:
            try:
                info = await asyncio.to_thread(
                    probe_one, url, cookies_browser=cookies
                )
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
