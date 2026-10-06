from __future__ import annotations

from collections import Counter
from threading import Lock
from prometheus_client import Counter as PromCounter, Histogram, generate_latest

job_events = PromCounter("nexforge_job_events_total", "NexForge job events", ["status"])
job_duration = Histogram("nexforge_job_duration_seconds", "NexForge job duration")
_metrics_lock = Lock()
_recent_events: list[dict[str, str]] = []


def record_job_event(status: str, job_id: str = "") -> None:
    job_events.labels(status=status).inc()
    with _metrics_lock:
        _recent_events.append({"status": status, "job_id": job_id})
        del _recent_events[:-100]


def dashboard_snapshot(jobs: list[dict]) -> dict[str, object]:
    counts = Counter(job.get("status", "unknown") for job in jobs)
    return {"total": len(jobs), "statuses": dict(counts), "recent_events": list(_recent_events)}


def prometheus_metrics() -> bytes:
    return generate_latest()
