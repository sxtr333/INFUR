import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from infer_hub.api.rate_limit import RateLimiter
from infer_hub.api.app import create_app, state
from infer_hub.config import Settings
from infer_hub.db import JobStore
from infer_hub.models import JobCreate, JobStatus
from infer_hub.ollama import CircuitBreaker, OllamaClient


@pytest.fixture
def settings(tmp_path):
    return Settings(
        db_path=tmp_path / "test.db",
        api_key="",
        rate_limit_rpm=1000,
        ollama_url="http://127.0.0.1:9",
    )


@pytest_asyncio.fixture
async def app_client(settings, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(settings.db_path))
    get_settings = __import__("infer_hub.config", fromlist=["get_settings"]).get_settings
    get_settings.cache_clear()
    app = create_app()
    from infer_hub.config import get_settings as gs
    cfg = gs()
    state.settings = cfg
    state.store = JobStore(cfg.db_path)
    await state.store.init()
    state.limiter = RateLimiter(1000)
    state.ollama = OllamaClient(base_url=cfg.ollama_url)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_health(app_client):
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
    assert data["version"]


@pytest.mark.asyncio
async def test_create_and_get_job(app_client):
    resp = await app_client.post("/v1/jobs", json={"prompt": "hello test"})
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "pending"
    got = await app_client.get(f"/v1/jobs/{job['id']}")
    assert got.status_code == 200
    assert got.json()["prompt"] == "hello test"


@pytest.mark.asyncio
async def test_batch_create(app_client):
    resp = await app_client.post(
        "/v1/jobs/batch",
        json={"jobs": [{"prompt": "a"}, {"prompt": "b", "priority": 9}]},
    )
    assert resp.status_code == 202
    jobs = resp.json()
    assert len(jobs) == 2
    assert jobs[1]["priority"] == 9


@pytest.mark.asyncio
async def test_cancel_pending_job(app_client):
    created = await app_client.post("/v1/jobs", json={"prompt": "cancel me"})
    job_id = created.json()["id"]
    resp = await app_client.delete(f"/v1/jobs/{job_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_store_claim_batch(settings):
    store = JobStore(settings.db_path)
    await store.init()
    await store.create_job(JobCreate(prompt="one", priority=3), model="test-model")
    await store.create_job(JobCreate(prompt="two", priority=8), model="test-model")
    batch = await store.claim_next_batch(1)
    assert len(batch) == 1
    assert batch[0].priority == 8


def test_circuit_breaker_opens():
    breaker = CircuitBreaker(failure_threshold=2, recovery_sec=60)
    breaker.record_failure()
    assert not breaker.is_open()
    breaker.record_failure()
    assert breaker.is_open()


@pytest.mark.asyncio
async def test_metrics_endpoint(app_client):
    await app_client.post("/v1/jobs", json={"prompt": "metrics"})
    resp = await app_client.get("/v1/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["jobs_total"] >= 1


@pytest.mark.asyncio
async def test_api_key_required(monkeypatch, tmp_path):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "auth.db"))
    get_settings = __import__("infer_hub.config", fromlist=["get_settings"]).get_settings
    get_settings.cache_clear()
    app = create_app()
    from infer_hub.config import get_settings as gs
    cfg = gs()
    state.settings = cfg
    state.store = JobStore(cfg.db_path)
    await state.store.init()
    state.limiter = RateLimiter(1000)
    state.ollama = OllamaClient(base_url=cfg.ollama_url)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/v1/jobs", json={"prompt": "nope"})
        assert resp.status_code == 401
        resp = await client.post(
            "/v1/jobs",
            json={"prompt": "ok"},
            headers={"X-API-Key": "secret"},
        )
        assert resp.status_code == 202
    get_settings.cache_clear()
