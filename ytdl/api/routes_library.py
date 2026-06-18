"""GET /library — list downloaded files under the configured output_dir.

Optional `subdir` query parameter scopes to a subfolder, but must not escape
output_dir (path-traversal rejected at 400).
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(tags=["library"])


class LibraryEntry(BaseModel):
    relpath: str
    size_bytes: int
    mtime_ms: int


class LibraryList(BaseModel):
    entries: list[LibraryEntry]


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
