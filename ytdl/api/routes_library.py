"""GET /library — list downloaded files under the configured output_dir.

Optional `subdir` query parameter scopes to a subfolder, but must not escape
output_dir (path-traversal rejected at 400).

POST /library/rescan — walk the configured ``library_scan_dirs`` and refresh
the duplicate-detection index.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from ytdl.db import connect, migrate
from ytdl.library import scan_directories

router = APIRouter(tags=["library"])


class LibraryEntry(BaseModel):
    relpath: str
    size_bytes: int
    mtime_ms: int


class LibraryList(BaseModel):
    entries: list[LibraryEntry]


class RescanResponse(BaseModel):
    count: int
    scanned_dirs: list[str]
    elapsed_s: float


@router.get("/library", response_model=LibraryList)
def library(request: Request, subdir: str = "") -> LibraryList:
    root: Path = request.app.state.config.output_dir
    root_resolved = root.resolve()
    target = (root / subdir).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path traversal not allowed") from exc
    if not target.exists():
        return LibraryList(entries=[])

    out: list[LibraryEntry] = []
    for p in sorted(target.rglob("*")):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(root_resolved)
        except ValueError:
            continue
        stat = p.stat()
        out.append(
            LibraryEntry(
                relpath=str(rel),
                size_bytes=stat.st_size,
                mtime_ms=int(stat.st_mtime * 1000),
            )
        )
    return LibraryList(entries=out)


@router.post("/library/rescan", response_model=RescanResponse)
async def rescan(request: Request) -> RescanResponse:
    """Re-walk the configured scan dirs and refresh the dedup index.

    Synchronous walk wrapped in ``asyncio.to_thread`` so a large library
    doesn't block the event loop. Returns the post-scan row count so the
    caller can render a "Indexed N files in Xs" toast.
    """
    cfg = request.app.state.config
    dirs = list(cfg.resolve_library_scan_dirs())
    db_path = cfg.db_path

    def _run() -> tuple[int, list[str], float]:
        conn = connect(db_path)
        try:
            migrate(conn)
            return scan_directories(conn, dirs)
        finally:
            conn.close()

    count, scanned_dirs, elapsed = await asyncio.to_thread(_run)
    return RescanResponse(
        count=count, scanned_dirs=scanned_dirs, elapsed_s=elapsed
    )
