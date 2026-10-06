from __future__ import annotations

from typing import Any

from app.config import get_settings
from app.jobs.production_store import PostgresJobStore
from app.jobs.queue import JobStore


def create_job_store() -> Any:
    settings = get_settings()
    if settings.postgres_url:
        return PostgresJobStore(settings.postgres_url)
    return JobStore(settings.database_url)
