"""Domain types: job status, job kind, dataclasses for in-memory use."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELING = "canceling"
    CANCELED = "canceled"


TERMINAL_STATUSES = frozenset({JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELED})


class JobKind(StrEnum):
    VIDEO = "video"
    PLAYLIST = "playlist"


@dataclass
class Job:
    id: str
    url: str
    kind: JobKind
    parent_job_id: str | None
    status: JobStatus
    format_pref: str
    output_dir: str
    output_path: str | None = None
    title: str | None = None
    video_id: str | None = None
    uploader: str | None = None
    duration_s: int | None = None
    filesize_bytes: int | None = None
    bytes_done: int | None = None
    speed_bps: int | None = None
    eta_s: int | None = None
    error: str | None = None
    force_overwrite: bool = False
    subtitles: bool = False
    attempts: int = 0
    created_at: int = 0
    started_at: int | None = None
    finished_at: int | None = None


@dataclass
class Event:
    id: int
    job_id: str
    kind: str  # 'enqueued' | 'started' | 'finished' | 'failed' | 'canceled' | 'log'
    payload: dict
    created_at: int
