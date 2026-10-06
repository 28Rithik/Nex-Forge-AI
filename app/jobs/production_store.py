from __future__ import annotations

from datetime import datetime, timezone
import json
import threading
from typing import Any
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from app.jobs.queue import default_pipeline_progress
from app.jobs.worker import JobProcessor


class PostgresJobStore:
    """PostgreSQL-backed JobStore-compatible implementation for production workers."""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._lock = threading.RLock()
        self._connection = psycopg.connect(database_url, row_factory=dict_row)
        self._processor = JobProcessor()
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    run_after TIMESTAMPTZ NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_retries INTEGER NOT NULL,
                    payload JSONB NOT NULL,
                    result JSONB,
                    error TEXT,
                    progress JSONB
                )
                """
            )
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, run_after, created_at)")
        self._connection.commit()

    def create(self, payload: dict[str, Any], *, max_retries: int | None = None) -> str:
        job_id = str(uuid4())
        now = datetime.now(timezone.utc)
        retries = int(max_retries if max_retries is not None else payload.get("max_retries", 3) or 3)
        with self._connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO jobs VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (job_id, "queued", now, now, now, 0, retries, json.dumps(payload), None, None, json.dumps(default_pipeline_progress())),
            )
        self._connection.commit()
        return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
            row = cursor.fetchone()
        if row is None:
            return None
        result = dict(row)
        return result

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT %s", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._connection.transaction():
            with self._connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' AND run_after <= NOW() ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                cursor.execute("UPDATE jobs SET status='running', attempts=attempts+1, updated_at=NOW() WHERE job_id=%s", (row["job_id"],))
                row["status"] = "running"
                row["attempts"] += 1
                return dict(row)

    def update(self, job_id: str, *, status: str | None = None, result: dict | None = None, error: str | None = None, progress: dict | None = None, **_: Any) -> None:
        current = self.get(job_id)
        if current is None:
            return
        with self._connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jobs SET status=COALESCE(%s,status), updated_at=NOW(), result=COALESCE(%s,result), error=%s, progress=COALESCE(%s,progress) WHERE job_id=%s",
                (status, json.dumps(result) if result is not None else None, error, json.dumps(progress) if progress is not None else None, job_id),
            )
        self._connection.commit()

    def execute_job_with_progress(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self._processor.process(payload)
        self.update(job_id, status=str(result.get("status", "completed")), result=result, error=None)
        return result

    def run_next_job(self) -> bool:
        job = self.claim_next_job()
        if job is None:
            return False
        try:
            self.execute_job_with_progress(job["job_id"], job["payload"])
        except Exception as exc:
            self.update(job["job_id"], status="failed", error=str(exc))
        return True

    def cancel(self, job_id: str) -> bool:
        current = self.get(job_id)
        if current is None or current.get("status") not in {"queued", "running"}:
            return False
        self.update(job_id, status="cancelled", error="Cancelled by operator")
        return True

    def recover_stale_jobs(self, timeout_seconds: int = 900) -> int:
        with self._connection.cursor() as cursor:
            cursor.execute("UPDATE jobs SET status='queued', updated_at=NOW(), error='Recovered stale job' WHERE status='running' AND updated_at < NOW() - make_interval(secs => %s)", (timeout_seconds,))
            count = cursor.rowcount
        self._connection.commit()
        return count

    def close(self) -> None:
        self._connection.close()
