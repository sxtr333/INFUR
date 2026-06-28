from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_key: str = ""

    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "darkidol-russian:latest"
    ollama_gpu_layers: int = 999
    ollama_num_parallel: int = 1
    ollama_request_timeout_sec: int = 120

    worker_poll_sec: float = 0.5
    worker_batch_size: int = 4
    worker_max_retries: int = 3
    job_ttl_hours: int = 72

    cpu_limit_pct: float = 70.0
    cuda_device_id: int = 0

    queue_backend: str = "sqlite"
    redis_url: str = "redis://127.0.0.1:6379/0"

    db_path: Path = Path("data/infer_hub.db")
    rate_limit_rpm: int = 60

    def clamp_cpu(self) -> float:
        return min(float(self.cpu_limit_pct), 75.0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def apply_runtime_env(settings: Settings | None = None) -> None:
    cfg = settings or get_settings()
    os.environ["CUDA_VISIBLE_DEVICES"] = str(cfg.cuda_device_id)
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
    os.environ["OLLAMA_GPU"] = "1"
    os.environ["OLLAMA_NUM_GPU"] = str(cfg.ollama_gpu_layers)
    os.environ["OLLAMA_NUM_PARALLEL"] = str(cfg.ollama_num_parallel)
    os.environ["OLLAMA_MAX_LOADED_MODELS"] = "1"

    workers = max(1, int(os.cpu_count() or 4) * cfg.clamp_cpu() / 100)
    os.environ["OMP_NUM_THREADS"] = str(workers)
    os.environ["MKL_NUM_THREADS"] = str(workers)
    os.environ["OPENBLAS_NUM_THREADS"] = str(workers)
    os.environ["NUMEXPR_NUM_THREADS"] = str(workers)
