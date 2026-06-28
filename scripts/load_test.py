#!/usr/bin/env python3
"""Submit N jobs and wait — lightweight load test."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx

BASE = os.getenv("INFER_HUB_URL", "http://127.0.0.1:8080")
KEY = os.getenv("INFER_HUB_API_KEY", "")


async def main(n: int) -> None:
    headers = {"X-API-Key": KEY} if KEY else {}
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:

        async def submit(i: int) -> str:
            r = await client.post(
                f"{BASE}/v1/jobs",
                json={"prompt": f"Reply OK and number {i}", "priority": 5, "max_tokens": 16},
                headers=headers,
            )
            r.raise_for_status()
            return r.json()["id"]

        async def wait_done(job_id: str) -> str:
            for _ in range(120):
                r = await client.get(f"{BASE}/v1/jobs/{job_id}", headers=headers)
                r.raise_for_status()
                status = r.json()["status"]
                if status in {"done", "failed"}:
                    return status
                await asyncio.sleep(1)
            return "timeout"

        ids = await asyncio.gather(*[submit(i) for i in range(n)])
        results = await asyncio.gather(*[wait_done(jid) for jid in ids])

    elapsed = time.perf_counter() - started
    done = sum(1 for x in results if x == "done")
    failed = sum(1 for x in results if x == "failed")
    print(f"jobs={n} done={done} failed={failed} timeout={n - done - failed} elapsed={elapsed:.1f}s")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1] if len(sys.argv) > 1 else 10)))
