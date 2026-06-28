from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


class JobCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=32000)
    model: str | None = None
    system: str | None = None
    priority: int = Field(default=5, ge=1, le=10)
    stream: bool = False
    temperature: float = Field(default=0.4, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=8192)


class JobBatchCreate(BaseModel):
    jobs: list[JobCreate] = Field(min_length=1, max_length=32)


class JobRecord(BaseModel):
    id: str
    status: JobStatus
    prompt: str
    model: str
    system: str | None = None
    priority: int
    stream: bool
    temperature: float
    max_tokens: int
    result: str | None = None
    error: str | None = None
    retries: int = 0
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    latency_ms: int | None = None


class JobSummary(BaseModel):
    id: str
    status: JobStatus
    priority: int
    model: str
    created_at: datetime
    latency_ms: int | None = None


class HealthResponse(BaseModel):
    ok: bool
    ollama: bool
    queue_depth: int
    worker_active: bool
    version: str


class MetricsSnapshot(BaseModel):
    jobs_total: int
    jobs_pending: int
    jobs_running: int
    jobs_done: int
    jobs_failed: int
    avg_latency_ms: float | None


class StreamChunk(BaseModel):
    job_id: str
    delta: str
    done: bool = False


class ErrorResponse(BaseModel):
    detail: str
    code: str | None = None
    extra: dict[str, Any] | None = None
