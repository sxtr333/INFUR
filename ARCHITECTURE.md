# Architecture — INFER_HUB

## Components

```
┌──────────┐     POST /v1/jobs      ┌─────────────┐
│  Client  │ ─────────────────────► │   FastAPI   │
└──────────┘                        │   (async)   │
     ▲                                └──────┬──────┘
     │ GET /v1/jobs/{id}                      │
     │ SSE /stream                            │ write
     │                                        ▼
     │                                 ┌─────────────┐
     │                                 │   SQLite    │
     │                                 │  jobs table │
     │                                 └──────┬──────┘
     │                                        │ claim batch
     │                                        ▼
     │                                 ┌─────────────┐
     └─────────────────────────────────│ GPU Worker  │
                                       └──────┬──────┘
                                              │ POST /api/generate
                                              ▼
                                       ┌─────────────┐
                                       │   Ollama    │
                                       │  (RTX GPU)  │
                                       └─────────────┘
```

## Job lifecycle

| Status | Meaning |
|--------|---------|
| `pending` | In queue, waiting for worker |
| `running` | Worker claimed, Ollama inference |
| `done` | Result in `result` column |
| `failed` | Error after max retries |
| `cancelled` | User cancelled while pending |

## Priority queue

Jobs sorted by `priority DESC, created_at ASC`. Higher number = processed first.

## Retry policy

On Ollama error: increment `retries`, set back to `pending` until `WORKER_MAX_RETRIES` (default 3), then `failed`.

## Resource limits

- `CPU_LIMIT_PCT` capped at 75%
- `taskset` affinity on worker (best-effort)
- `OLLAMA_GPU_LAYERS` / `num_gpu` for GPU inference
- `OMP_NUM_THREADS` etc. scaled to CPU cap

## Observability

- `GET /health` — ollama ping, queue depth, worker alive file
- `GET /metrics` — Prometheus (HTTP counters, queue gauge)
- `GET /v1/metrics` — job stats JSON (requires API key if set)

## Future production path

1. SQLite → PostgreSQL
2. In-process poll → Redis / ARQ
3. Stale `running` job recovery
4. Multiple workers with horizontal scale
5. Auth via JWT or mTLS
