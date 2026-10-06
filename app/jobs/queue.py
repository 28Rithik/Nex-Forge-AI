from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import sqlite3
import threading
from datetime import timedelta
from typing import Any
from uuid import uuid4

from app.jobs.worker import JobProcessor

PIPELINE_STAGES = [
    {"key": "clone", "label": "Clone Repository", "icon": "📦"},
    {"key": "ast_parsing", "label": "AST Parsing", "icon": "🌳"},
    {"key": "graph_indexing", "label": "Graph Indexing", "icon": "🕸️"},
    {"key": "debugger", "label": "Debugger Agent", "icon": "🔍"},
    {"key": "coder", "label": "Coder Patch", "icon": "⚡"},
    {"key": "sandbox_pr", "label": "Sandbox & PR", "icon": "🛡️"},
]


class JobCancelled(Exception):
    pass


def default_pipeline_progress() -> dict[str, Any]:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    return {
        "current_step": "queued",
        "active_stage_index": -1,
        "steps": {
            s["key"]: {
                "label": s["label"],
                "icon": s["icon"],
                "status": "pending",
                "detail": "Waiting in queue...",
            }
            for s in PIPELINE_STAGES
        },
        "logs": [f"[{now}] Job placed in processing queue"],
    }


@dataclass
class JobRecord:
    job_id: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    run_after: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attempts: int = 0
    max_retries: int = 3
    payload: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: str | None = None
    progress: dict[str, Any] | None = None


class JobStore:
    def __init__(self, database_url: str = "sqlite:///./nexforge.db") -> None:
        self.database_url = database_url
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self._resolve_database_path(database_url), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._initialize_schema()
        self._processor = JobProcessor()

    def _resolve_database_path(self, database_url: str) -> str:
        if database_url in {"", ":memory:", "sqlite:///:memory:"}:
            return ":memory:"
        if database_url.startswith("sqlite:///"):
            return database_url.removeprefix("sqlite:///")
        if database_url.startswith("sqlite://"):
            return database_url.removeprefix("sqlite://")
        return database_url

    def _initialize_schema(self) -> None:
        with self._lock:
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    run_after TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    max_retries INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    result TEXT,
                    error TEXT,
                    progress TEXT
                )
                """
            )
            try:
                self._connection.execute("ALTER TABLE jobs ADD COLUMN progress TEXT")
            except sqlite3.OperationalError:
                pass
            self._connection.execute("CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(status, run_after, created_at)")
            self._connection.commit()

    def create(self, payload: dict[str, Any], *, max_retries: int | None = None) -> str:
        with self._lock:
            job_id = str(uuid4())
            timestamp = datetime.now(timezone.utc).isoformat()
            job_max_retries = int(max_retries if max_retries is not None else payload.get("max_retries", 3) or 3)
            progress_data = default_pipeline_progress()
            self._connection.execute(
                """
                INSERT INTO jobs (job_id, status, created_at, updated_at, run_after, attempts, max_retries, payload, result, error, progress)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    "queued",
                    timestamp,
                    timestamp,
                    timestamp,
                    0,
                    job_max_retries,
                    json.dumps(payload),
                    None,
                    None,
                    json.dumps(progress_data),
                ),
            )
            self._connection.commit()
            return job_id

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
            if row is None:
                return None
            row_keys = row.keys()
            progress = None
            if "progress" in row_keys and row["progress"]:
                try:
                    progress = json.loads(row["progress"])
                except Exception:
                    progress = None
            return {
                "job_id": row["job_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "run_after": row["run_after"],
                "attempts": row["attempts"],
                "max_retries": row["max_retries"],
                "payload": json.loads(row["payload"]),
                "result": json.loads(row["result"]) if row["result"] else None,
                "error": row["error"],
                "progress": progress,
            }

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._lock:
            timestamp = datetime.now(timezone.utc).isoformat()
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = 'queued' AND run_after <= ?
                ORDER BY created_at ASC
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                self._connection.execute("COMMIT")
                return None
            self._connection.execute(
                """
                UPDATE jobs
                SET status = 'running', updated_at = ?, attempts = attempts + 1
                WHERE job_id = ?
                """,
                (timestamp, row["job_id"]),
            )
            self._connection.execute("COMMIT")
            return self.get(row["job_id"])

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            current = self.get(job_id)
            if current is None or current["status"] not in {"queued", "running"}:
                return False
            self._connection.execute(
                "UPDATE jobs SET status='cancelled', updated_at=?, error=? WHERE job_id=?",
                (datetime.now(timezone.utc).isoformat(), "Cancelled by operator", job_id),
            )
            self._connection.commit()
            return True

    def update_progress(self, job_id: str, stage: str, status: str, detail: str) -> None:
        with self._lock:
            current = self.get(job_id)
            if current is None:
                return
            progress = current.get("progress") or default_pipeline_progress()
            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            stage_keys = [s["key"] for s in PIPELINE_STAGES]
            stage_idx = stage_keys.index(stage) if stage in stage_keys else -1

            # Mark prior pending/running steps as completed
            if stage_idx > 0 and status in {"running", "completed"}:
                for prev_key in stage_keys[:stage_idx]:
                    prev_step = progress["steps"].get(prev_key, {})
                    if prev_step.get("status") in {"pending", "running"}:
                        prev_step["status"] = "completed"
                        if not prev_step.get("detail") or "Waiting" in prev_step.get("detail", ""):
                            prev_step["detail"] = "Completed"

            if stage in progress["steps"]:
                progress["steps"][stage]["status"] = status
                progress["steps"][stage]["detail"] = detail
            progress["current_step"] = stage
            progress["active_stage_index"] = stage_idx

            log_entry = f"[{now_str}] [{stage.upper()}] {detail}"
            if "logs" not in progress:
                progress["logs"] = []
            progress["logs"].append(log_entry)
            if len(progress["logs"]) > 150:
                progress["logs"] = progress["logs"][-150:]

            timestamp = datetime.now(timezone.utc).isoformat()
            self._connection.execute(
                "UPDATE jobs SET progress = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(progress), timestamp, job_id),
            )
            self._connection.commit()

    def execute_job_with_progress(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.update(job_id, status="running")

        def on_progress(stage: str, status: str, detail: str) -> None:
            current = self.get(job_id)
            if current and current["status"] == "cancelled":
                raise JobCancelled("Job cancelled by operator")
            self.update_progress(job_id, stage, status, detail)

        try:
            try:
                result = self._processor.process(payload, progress_callback=on_progress)
            except TypeError as te:
                if "progress_callback" in str(te):
                    result = self._processor.process(payload)
                else:
                    raise
            with self._lock:
                status = str(result.get("status", "completed"))
                current = self.get(job_id)
                if current and current.get("progress"):
                    prog = current["progress"]
                    # Mark all steps completed unless failed
                    for k, step in prog.get("steps", {}).items():
                        if step.get("status") in {"pending", "running"}:
                            step["status"] = "completed"
                    prog["current_step"] = "done"
                    prog["active_stage_index"] = len(PIPELINE_STAGES)
                    prog["logs"].append(
                        f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] [FINISH] Job execution completed: {status}"
                    )
                    self._connection.execute(
                        "UPDATE jobs SET progress = ? WHERE job_id = ?",
                        (json.dumps(prog), job_id),
                    )
                self.update(job_id, status=status, result=result, error=None)
            return result
        except Exception as exc:
            with self._lock:
                current = self.get(job_id)
                if current and current["status"] == "cancelled":
                    return {"status": "cancelled", "message": str(exc)}
                curr_step = "unknown"
                if current and current.get("progress"):
                    curr_step = current["progress"].get("current_step", "unknown")
                self.update_progress(job_id, curr_step, "failed", f"Execution error: {exc}")
                attempts = current["attempts"] if current else 1
                max_retries = current["max_retries"] if current else 3
                self.fail(job_id, str(exc), attempts=attempts, max_retries=max_retries)
            raise

    def run_job(self, job_id: str) -> None:
        current = self.get(job_id)
        if current is None:
            return
        try:
            self.execute_job_with_progress(job_id, current["payload"])
        except Exception:
            pass

    def run_next_job(self) -> bool:
        job = self.claim_next_job()
        if job is None:
            return False
        try:
            self.execute_job_with_progress(job["job_id"], job["payload"])
        except Exception:
            pass
        return True

    def fail(
        self,
        job_id: str,
        error: str,
        *,
        attempts: int | None = None,
        max_retries: int | None = None,
    ) -> None:
        with self._lock:
            current = self.get(job_id)
            if current is None:
                return
            current_attempts = int(attempts if attempts is not None else current["attempts"])
            current_max_retries = int(max_retries if max_retries is not None else current["max_retries"])
            if current_attempts < current_max_retries:
                delay_seconds = min(60, 2 ** max(current_attempts - 1, 0))
                run_after = datetime.now(timezone.utc).timestamp() + delay_seconds
                self._connection.execute(
                    """
                    UPDATE jobs
                    SET status = 'queued', updated_at = ?, run_after = datetime(?, 'unixepoch'), error = ?
                    WHERE job_id = ?
                    """,
                    (datetime.now(timezone.utc).isoformat(), run_after, error, job_id),
                )
                self._connection.commit()
                return
            self.update(job_id, status="failed", error=error)

    def update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        run_after: str | None = None,
        progress: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            current = self.get(job_id)
            if current is None:
                return
            new_status = status or current["status"]
            timestamp = datetime.now(timezone.utc).isoformat()
            if progress is not None:
                self._connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, updated_at = ?, run_after = COALESCE(?, run_after), result = ?, error = ?, progress = ?
                    WHERE job_id = ?
                    """,
                    (
                        new_status,
                        timestamp,
                        run_after,
                        json.dumps(result) if result is not None else None,
                        error,
                        json.dumps(progress),
                        job_id,
                    ),
                )
            else:
                self._connection.execute(
                    """
                    UPDATE jobs
                    SET status = ?, updated_at = ?, run_after = COALESCE(?, run_after), result = ?, error = ?
                    WHERE job_id = ?
                    """,
                    (new_status, timestamp, run_after, json.dumps(result) if result is not None else None, error, job_id),
                )
            self._connection.commit()

    def list_queued(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT job_id FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [self.get(row["job_id"]) for row in rows if self.get(row["job_id"]) is not None]

    def reset_running_jobs(self) -> int:
        with self._lock:
            timestamp = datetime.now(timezone.utc).isoformat()
            result = self._connection.execute(
                "UPDATE jobs SET status = 'queued', updated_at = ? WHERE status = 'running'",
                (timestamp,),
            )
            self._connection.commit()
            return result.rowcount

    def recover_stale_jobs(self, timeout_seconds: int = 900) -> int:
        """Requeue jobs abandoned by a crashed worker, preserving retry accounting."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)).isoformat()
        with self._lock:
            result = self._connection.execute(
                """
                UPDATE jobs
                SET status = 'queued', updated_at = ?, run_after = ?, error = ?
                WHERE status = 'running' AND updated_at < ? AND attempts < max_retries
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    datetime.now(timezone.utc).isoformat(),
                    "Recovered stale running job after worker interruption",
                    cutoff,
                ),
            )
            failed = self._connection.execute(
                """
                UPDATE jobs
                SET status = 'failed', updated_at = ?, error = ?
                WHERE status = 'running' AND updated_at < ? AND attempts >= max_retries
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    "Job exceeded retry limit after worker interruption",
                    cutoff,
                ),
            )
            self._connection.commit()
            return result.rowcount + failed.rowcount


def enqueue_analysis_job(store: JobStore, payload: dict[str, Any]) -> str:
    return store.create(payload)
