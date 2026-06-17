from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

_MAX_URL_LEN = 4096


class JobCreate(BaseModel):
    url: str = Field(min_length=1, max_length=_MAX_URL_LEN)
    format_pref: str | None = None

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("url must use http or https")
        if not parsed.netloc:
            raise ValueError("url must include a host")
        return v


class JobOut(BaseModel):
    id: str
    url: str
    kind: str
    parent_job_id: str | None
    status: str
    format_pref: str
    output_dir: str
    output_path: str | None
    title: str | None
    video_id: str | None
    uploader: str | None
    duration_s: int | None
    filesize_bytes: int | None
    bytes_done: int | None
    speed_bps: int | None
    eta_s: int | None
    error: str | None
    attempts: int
    created_at: int
    started_at: int | None
    finished_at: int | None


class JobList(BaseModel):
    jobs: list[JobOut]
    total: int
