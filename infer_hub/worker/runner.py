from __future__ import annotations

import asyncio
import logging
import signal
import time

from infer_hub.config import Settings, apply_runtime_env, get_settings
from infer_hub.db import JobStore
from infer_hub.limits import apply_cpu_affinity
from infer_hub.models import JobStatus
from infer_hub.ollama import OllamaClient

logger = logging.getLogger(__name__)


class InferenceWorker:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        apply_runtime_env(self.settings)
        apply_cpu_affinity(self.settings.clamp_cpu())
        self.store = JobStore(self.settings.db_path)
        self.ollama = OllamaClient(
            base_url=self.settings.ollama_url,
            timeout_sec=self.settings.ollama_request_timeout_sec,
            gpu_layers=self.settings.ollama_gpu_layers,
        )
        self._stop = asyncio.Event()

    async def start(self) -> None:
        await self.store.init()
        await self.ollama.warm_model(self.settings.ollama_model)
        logger.info(
            "worker started (cpu cap %.0f%%, gpu layers %s, batch %s)",
            self.settings.clamp_cpu(),
            self.settings.ollama_gpu_layers,
            self.settings.worker_batch_size,
        )
        while not self._stop.is_set():
            jobs = await self.store.claim_next_batch(self.settings.worker_batch_size)
            if not jobs:
                await asyncio.sleep(self.settings.worker_poll_sec)
                continue
            for job in jobs:
                if self._stop.is_set():
                    break
                await self._run_job(job)

    async def _run_job(self, job) -> None:
        try:
            if job.stream:

                async def on_chunk(chunk: str) -> None:
                    await self.store.append_stream_chunk(job.id, chunk)

                text, latency_ms = await self.ollama.stream_generate(job, on_chunk=on_chunk)
            else:
                text, latency_ms = await self.ollama.generate(job)
            await self.store.complete_job(job.id, result=text, latency_ms=latency_ms)
            logger.info("job %s done in %sms", job.id[:8], latency_ms)
        except Exception as exc:
            logger.warning("job %s error: %s", job.id[:8], exc)
            status = await self.store.retry_or_fail(
                job.id,
                str(exc),
                max_retries=self.settings.worker_max_retries,
            )
            if status == JobStatus.failed:
                logger.error("job %s failed permanently", job.id[:8])

    def stop(self) -> None:
        self._stop.set()


async def _main_async() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    worker = InferenceWorker()
    loop = asyncio.get_running_loop()

    def _handle_sig(*_: object) -> None:
        logger.info("shutdown signal received")
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _handle_sig)

    purge_task = asyncio.create_task(_purge_loop(worker))
    try:
        await worker.start()
    finally:
        purge_task.cancel()


async def _purge_loop(worker: InferenceWorker) -> None:
    while True:
        await asyncio.sleep(3600)
        removed = await worker.store.purge_old(worker.settings.job_ttl_hours)
        if removed:
            logger.info("purged %s old jobs", removed)


def main() -> None:
    asyncio.run(_main_async())


if __name__ == "__main__":
    main()
