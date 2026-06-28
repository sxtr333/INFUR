from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from infer_hub.models import JobCreate, JobRecord, JobStatus, JobSummary, MetricsSnapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    prompt TEXT NOT NULL,
    model TEXT NOT NULL,
    system TEXT,
    priority INTEGER NOT NULL,
    stream INTEGER NOT NULL,
    temperature REAL NOT NULL,
    max_tokens INTEGER NOT NULL,
    result TEXT,
    error TEXT,
    retries INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    latency_ms INTEGER
);
CREATE INDEX IF NOT EXISTS idx_jobs_status_prio ON jobs(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
"""


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    async def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def create_job(self, payload: JobCreate, *, model: str) -> JobRecord:
        job_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO jobs (
                    id, status, prompt, model, system, priority, stream,
                    temperature, max_tokens, retries, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    job_id,
                    JobStatus.pending.value,
                    payload.prompt,
                    payload.model or model,
                    payload.system,
                    payload.priority,
                    int(payload.stream),
                    payload.temperature,
                    payload.max_tokens,
                    now.isoformat(),
                ),
            )
            await db.commit()
        return await self.get_job(job_id)

    async def get_job(self, job_id: str) -> JobRecord:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
        if not row:
            raise KeyError(job_id)
        return _row_to_record(row)

    async def list_jobs(self, *, limit: int = 50, status: JobStatus | None = None) -> list[JobSummary]:
        query = "SELECT id, status, priority, model, created_at, latency_ms FROM jobs"
        params: list[object] = []
        if status:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
        return [
            JobSummary(
                id=r["id"],
                status=JobStatus(r["status"]),
                priority=r["priority"],
                model=r["model"],
                created_at=datetime.fromisoformat(r["created_at"]),
                latency_ms=r["latency_ms"],
            )
            for r in rows
        ]

    async def claim_next_batch(self, batch_size: int) -> list[JobRecord]:
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            cur = await db.execute(
                """
                SELECT id FROM jobs
                WHERE status = ?
                ORDER BY priority DESC, created_at ASC
                LIMIT ?
                """,
                (JobStatus.pending.value, batch_size),
            )
            ids = [r["id"] for r in await cur.fetchall()]
            if not ids:
                await db.commit()
                return []
            placeholders = ",".join("?" for _ in ids)
            await db.execute(
                f"""
                UPDATE jobs SET status = ?, started_at = ?
                WHERE id IN ({placeholders}) AND status = ?
                """,
                (JobStatus.running.value, now, *ids, JobStatus.pending.value),
            )
            cur = await db.execute(
                f"SELECT * FROM jobs WHERE id IN ({placeholders})",
                ids,
            )
            rows = await cur.fetchall()
            await db.commit()
        return [_row_to_record(r) for r in rows if r["status"] == JobStatus.running.value]

    async def complete_job(
        self,
        job_id: str,
        *,
        result: str | None = None,
        error: str | None = None,
        latency_ms: int | None = None,
    ) -> None:
        status = JobStatus.done if error is None else JobStatus.failed
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE jobs
                SET status = ?, result = ?, error = ?, finished_at = ?, latency_ms = ?
                WHERE id = ?
                """,
                (status.value, result, error, now, latency_ms, job_id),
            )
            await db.commit()

    async def retry_or_fail(self, job_id: str, error: str, *, max_retries: int) -> JobStatus:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT retries FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
            if not row:
                raise KeyError(job_id)
            retries = int(row["retries"]) + 1
            if retries >= max_retries:
                now = datetime.now(UTC).isoformat()
                await db.execute(
                    """
                    UPDATE jobs SET status = ?, error = ?, retries = ?, finished_at = ?
                    WHERE id = ?
                    """,
                    (JobStatus.failed.value, error, retries, now, job_id),
                )
                await db.commit()
                return JobStatus.failed
            await db.execute(
                """
                UPDATE jobs SET status = ?, error = ?, retries = ?, started_at = NULL
                WHERE id = ?
                """,
                (JobStatus.pending.value, error, retries, job_id),
            )
            await db.commit()
            return JobStatus.pending

    async def append_stream_chunk(self, job_id: str, chunk: str) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT result FROM jobs WHERE id = ?", (job_id,))
            row = await cur.fetchone()
            prev = row[0] if row and row[0] else ""
            await db.execute(
                "UPDATE jobs SET result = ? WHERE id = ?",
                (prev + chunk, job_id),
            )
            await db.commit()

    async def queue_depth(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT COUNT(*) FROM jobs WHERE status IN (?, ?)",
                (JobStatus.pending.value, JobStatus.running.value),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def metrics(self) -> MetricsSnapshot:
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
                    SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS running,
                    SUM(CASE WHEN status = 'done' THEN 1 ELSE 0 END) AS done,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    AVG(CASE WHEN latency_ms IS NOT NULL THEN latency_ms END) AS avg_lat
                FROM jobs
                """
            )
            row = await cur.fetchone()
        return MetricsSnapshot(
            jobs_total=int(row[0] or 0),
            jobs_pending=int(row[1] or 0),
            jobs_running=int(row[2] or 0),
            jobs_done=int(row[3] or 0),
            jobs_failed=int(row[4] or 0),
            avg_latency_ms=float(row[5]) if row[5] is not None else None,
        )

    async def purge_old(self, ttl_hours: int) -> int:
        cutoff = (datetime.now(UTC) - timedelta(hours=ttl_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "DELETE FROM jobs WHERE created_at < ? AND status IN ('done', 'failed', 'cancelled')",
                (cutoff,),
            )
            await db.commit()
            return cur.rowcount or 0

    async def cancel_job(self, job_id: str) -> JobRecord:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE jobs SET status = ? WHERE id = ? AND status = ?",
                (JobStatus.cancelled.value, job_id, JobStatus.pending.value),
            )
            await db.commit()
        return await self.get_job(job_id)


def _row_to_record(row: aiosqlite.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        status=JobStatus(row["status"]),
        prompt=row["prompt"],
        model=row["model"],
        system=row["system"],
        priority=row["priority"],
        stream=bool(row["stream"]),
        temperature=row["temperature"],
        max_tokens=row["max_tokens"],
        result=row["result"],
        error=row["error"],
        retries=row["retries"],
        created_at=datetime.fromisoformat(row["created_at"]),
        started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
        latency_ms=row["latency_ms"],
    )


def dump_job_event(job: JobRecord) -> str:
    return json.dumps(job.model_dump(mode="json"), ensure_ascii=False)
