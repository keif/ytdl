from __future__ import annotations

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator, model_validator

_MAX_URL_LEN = 4096
_MAX_PICK_URLS = 500


def _validate_http_url(v: str) -> str:
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must use http or https")
    if not parsed.netloc:
        raise ValueError("url must include a host")
    return v


class JobMeta(BaseModel):
    """Preview-derived metadata for a URL, so the queue row can show the
    video's image + title instead of a bare URL. All optional — the frontend
    fills what the preview probe returned; the worker refreshes it on finish.
    """

    title: str | None = Field(default=None, max_length=1024)
    uploader: str | None = Field(default=None, max_length=512)
    duration_s: int | None = Field(default=None, ge=0)
    thumbnail_url: str | None = Field(default=None, max_length=_MAX_URL_LEN)

    @field_validator("thumbnail_url")
    @classmethod
    def thumbnail_must_be_http(cls, v: str | None) -> str | None:
        # Rendered directly as an <img src> in the browser, so restrict it to
        # http(s) — reject javascript:/data:/file: schemes as defense-in-depth.
        if v is None or v == "":
            return None
        return _validate_http_url(v)


class JobCreate(BaseModel):
    """Create one job from a single URL, or N jobs from a picked subset.

    Exactly one of ``url`` or ``urls`` must be provided. ``urls`` is used by
    the playlist picker after the user has narrowed down which entries to
    download — each URL is enqueued as a standalone video job.
    """

    url: str | None = Field(default=None, min_length=1, max_length=_MAX_URL_LEN)
    urls: list[str] | None = Field(
        default=None, min_length=1, max_length=_MAX_PICK_URLS
    )
    # Preview metadata keyed by URL. Applies to whichever URLs are enqueued
    # (single ``url`` or each of ``urls``); a URL with no entry just gets null
    # metadata. Optional so CLI / older clients keep working unchanged.
    metadata: dict[str, JobMeta] | None = Field(default=None, max_length=_MAX_PICK_URLS)
    format_pref: str | None = None
    # None falls back to the server's `subtitles_default` config; an explicit
    # bool overrides it. Lets the UI checkbox show a tri-state default
    # ("use config preference") without forcing a value on every POST.
    subtitles: bool | None = None
    # Per-job destination override. None falls back to `cfg.output_dir`.
    # Tilde expansion happens server-side. Validated for writability before
    # the job is enqueued — see routes_jobs.post_job.
    output_dir: str | None = Field(default=None, min_length=1, max_length=4096)
    # When True, bypass the duplicate-detection check that would otherwise
    # return 409 for a URL whose video_id is already in the library. Also
    # propagates to the job row so yt-dlp overwrites an existing output
    # file on disk (same knob the /redownload endpoint uses).
    force_overwrite: bool = False

    @field_validator("url")
    @classmethod
    def url_must_be_http(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return _validate_http_url(v)

    @field_validator("urls")
    @classmethod
    def each_url_must_be_http(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        for u in v:
            if not u or len(u) > _MAX_URL_LEN:
                raise ValueError(
                    f"each url must be 1..{_MAX_URL_LEN} chars"
                )
            _validate_http_url(u)
        return v

    @model_validator(mode="after")
    def exactly_one_source(self) -> JobCreate:
        if (self.url is None) == (self.urls is None):
            raise ValueError("provide exactly one of 'url' or 'urls'")
        return self


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
    thumbnail_url: str | None
    filesize_bytes: int | None
    bytes_done: int | None
    speed_bps: int | None
    eta_s: int | None
    error: str | None
    force_overwrite: bool
    subtitles: bool
    attempts: int
    created_at: int
    started_at: int | None
    finished_at: int | None


class JobList(BaseModel):
    jobs: list[JobOut]
    total: int
