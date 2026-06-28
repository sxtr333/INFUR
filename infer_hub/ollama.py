from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field

import httpx

from infer_hub.models import JobRecord, JobStatus

logger = logging.getLogger(__name__)


@dataclass
class CircuitBreaker:
    failure_threshold: int = 5
    recovery_sec: float = 30.0
    failures: int = 0
    opened_at: float | None = None

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.recovery_sec:
            self.opened_at = None
            self.failures = 0
            return False
        return True


@dataclass
class OllamaClient:
    base_url: str
    timeout_sec: int = 120
    gpu_layers: int = 999
    breaker: CircuitBreaker = field(default_factory=CircuitBreaker)

    async def ping(self) -> bool:
        url = f"{self.base_url.rstrip('/')}/api/tags"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(url)
                return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def generate(self, job: JobRecord) -> tuple[str, int]:
        if self.breaker.is_open():
            raise RuntimeError("ollama circuit open — too many recent failures")

        started = time.perf_counter()
        payload = {
            "model": job.model,
            "prompt": job.prompt,
            "stream": False,
            "options": {
                "temperature": job.temperature,
                "num_predict": job.max_tokens,
                "num_gpu": self.gpu_layers,
            },
        }
        if job.system:
            payload["system"] = job.system

        url = f"{self.base_url.rstrip('/')}/api/generate"
        try:
            async with httpx.AsyncClient(timeout=float(self.timeout_sec)) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            self.breaker.record_failure()
            raise RuntimeError(f"ollama request failed: {exc}") from exc

        self.breaker.record_success()
        latency_ms = int((time.perf_counter() - started) * 1000)
        text = str(data.get("response", "")).strip()
        return text, latency_ms

    async def stream_generate(
        self,
        job: JobRecord,
        on_chunk: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[str, int]:
        if self.breaker.is_open():
            raise RuntimeError("ollama circuit open — too many recent failures")

        started = time.perf_counter()
        payload = {
            "model": job.model,
            "prompt": job.prompt,
            "stream": True,
            "options": {
                "temperature": job.temperature,
                "num_predict": job.max_tokens,
                "num_gpu": self.gpu_layers,
            },
        }
        if job.system:
            payload["system"] = job.system

        url = f"{self.base_url.rstrip('/')}/api/generate"
        parts: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=float(self.timeout_sec)) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        chunk = str(data.get("response", ""))
                        if chunk:
                            parts.append(chunk)
                            if on_chunk:
                                await on_chunk(chunk)
                        if data.get("done"):
                            break
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            self.breaker.record_failure()
            raise RuntimeError(f"ollama stream failed: {exc}") from exc

        self.breaker.record_success()
        latency_ms = int((time.perf_counter() - started) * 1000)
        return "".join(parts), latency_ms

    async def warm_model(self, model: str) -> None:
        url = f"{self.base_url.rstrip('/')}/api/generate"
        payload = {
            "model": model,
            "prompt": "ok",
            "stream": False,
            "options": {"num_predict": 1, "num_gpu": self.gpu_layers},
        }
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                await client.post(url, json=payload)
        except httpx.HTTPError as exc:
            logger.warning("model warm-up failed: %s", exc)


async def sse_job_stream(
    store,
    job_id: str,
    *,
    poll_sec: float = 0.25,
    timeout_sec: float = 180.0,
) -> AsyncIterator[str]:
    """Yield SSE chunks while job streams into DB."""
    deadline = time.monotonic() + timeout_sec
    last_len = 0
    while time.monotonic() < deadline:
        job = await store.get_job(job_id)
        text = job.result or ""
        if len(text) > last_len:
            yield text[last_len:]
            last_len = len(text)
        if job.status in {JobStatus.done, JobStatus.failed, JobStatus.cancelled}:
            break
        await asyncio.sleep(poll_sec)
