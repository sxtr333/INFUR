from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from infer_hub import __version__
from infer_hub.api.rate_limit import RateLimiter
from infer_hub.config import Settings, apply_runtime_env, get_settings
from infer_hub.db import JobStore
from infer_hub.models import (
    HealthResponse,
    JobBatchCreate,
    JobCreate,
    JobRecord,
    JobStatus,
    JobSummary,
    MetricsSnapshot,
)
from infer_hub.ollama import OllamaClient, sse_job_stream

logger = logging.getLogger(__name__)

REQUESTS = Counter("infer_hub_http_requests_total", "HTTP requests", ["route", "method", "status"])
JOB_LATENCY = Histogram("infer_hub_job_latency_ms", "Completed job latency", buckets=[100, 500, 1000, 3000, 10000, 60000])
QUEUE_DEPTH = Gauge("infer_hub_queue_depth", "Pending + running jobs")


class AppState:
    store: JobStore
    ollama: OllamaClient
    settings: Settings
    limiter: RateLimiter
    worker_alive: bool = False


state = AppState()
state.limiter = RateLimiter(60)


def require_api_key(
    settings: Settings = Depends(get_settings),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    if settings.api_key and x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    apply_runtime_env(settings)
    state.settings = settings
    state.store = JobStore(settings.db_path)
    await state.store.init()
    state.ollama = OllamaClient(
        base_url=settings.ollama_url,
        timeout_sec=settings.ollama_request_timeout_sec,
        gpu_layers=settings.ollama_gpu_layers,
    )
    state.limiter = RateLimiter(settings.rate_limit_rpm)
    logger.info("INFER_HUB API started on %s:%s", settings.api_host, settings.api_port)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="INFER_HUB",
        description="Local GPU inference platform with priority job queue",
        version=__version__,
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        if not state.limiter.allow(client_key(request)):
            REQUESTS.labels(request.url.path, request.method, 429).inc()
            return JSONResponse(status_code=429, content={"detail": "rate limit exceeded"})
        response = await call_next(request)
        REQUESTS.labels(request.url.path, request.method, response.status_code).inc()
        return response

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        ollama_ok = await state.ollama.ping()
        depth = await state.store.queue_depth()
        QUEUE_DEPTH.set(depth)
        return HealthResponse(
            ok=ollama_ok,
            ollama=ollama_ok,
            queue_depth=depth,
            worker_active=Path(state.settings.db_path).parent.joinpath("worker.alive").exists(),
            version=__version__,
        )

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        depth = await state.store.queue_depth()
        QUEUE_DEPTH.set(depth)
        return PlainTextResponse(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.get("/v1/metrics", response_model=MetricsSnapshot)
    async def job_metrics(_: None = Depends(require_api_key)) -> MetricsSnapshot:
        return await state.store.metrics()

    @app.post("/v1/jobs", response_model=JobRecord, status_code=202)
    async def create_job(payload: JobCreate, _: None = Depends(require_api_key)) -> JobRecord:
        model = payload.model or state.settings.ollama_model
        job = await state.store.create_job(payload, model=model)
        return job

    @app.post("/v1/jobs/batch", response_model=list[JobRecord], status_code=202)
    async def create_batch(payload: JobBatchCreate, _: None = Depends(require_api_key)) -> list[JobRecord]:
        model_default = state.settings.ollama_model
        jobs: list[JobRecord] = []
        for item in payload.jobs:
            jobs.append(await state.store.create_job(item, model=item.model or model_default))
        return jobs

    @app.get("/v1/jobs", response_model=list[JobSummary])
    async def list_jobs(
        limit: int = Query(default=50, ge=1, le=200),
        status: JobStatus | None = None,
        _: None = Depends(require_api_key),
    ) -> list[JobSummary]:
        return await state.store.list_jobs(limit=limit, status=status)

    @app.get("/v1/jobs/{job_id}", response_model=JobRecord)
    async def get_job(job_id: str, _: None = Depends(require_api_key)) -> JobRecord:
        try:
            return await state.store.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    @app.delete("/v1/jobs/{job_id}", response_model=JobRecord)
    async def cancel_job(job_id: str, _: None = Depends(require_api_key)) -> JobRecord:
        try:
            job = await state.store.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc
        if job.status != JobStatus.pending:
            raise HTTPException(status_code=409, detail=f"cannot cancel job in status {job.status}")
        return await state.store.cancel_job(job_id)

    @app.get("/v1/jobs/{job_id}/stream")
    async def stream_job(job_id: str, _: None = Depends(require_api_key)):
        try:
            job = await state.store.get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

        async def event_generator():
            if job.result:
                yield f"data: {job.result}\n\n"
            async for chunk in sse_job_stream(state.store, job_id):
                yield f"data: {chunk}\n\n"
            final = await state.store.get_job(job_id)
            yield f"event: done\ndata: {final.status.value}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    return app
