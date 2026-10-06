from __future__ import annotations

from contextlib import asynccontextmanager
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import get_settings
from app.cloning.repo_validation import is_valid_repository_reference, repository_validation_message
from app.github_integration.webhook_verify import verify_github_webhook_signature
from app.graph.neo4j_client import Neo4jClient
from app.observability import dashboard_snapshot, prometheus_metrics, record_job_event
from app.redis_client import RedisEvents
from app.security import allowed_repository, require_admin, require_auth
from app.jobs.auto_worker import AutoWorker
from app.jobs.store_factory import create_job_store
from app.jobs.queue import enqueue_analysis_job

job_store = create_job_store()
redis_events = RedisEvents(get_settings().redis_url)
auto_worker = AutoWorker(job_store, poll_interval=1.5, enabled=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    auto_worker.start()
    yield
    auto_worker.stop()


app = FastAPI(title="NexForge AI", version="0.1.0", lifespan=lifespan)

# Serve static UI
_static_dir = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_dir), name="static")


@app.get("/", include_in_schema=False)
def root():
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/review", include_in_schema=False)
def review_page():
    return FileResponse(os.path.join(_static_dir, "review.html"))


@app.get("/architecture", include_in_schema=False)
def architecture_page():
    return FileResponse(os.path.join(_static_dir, "architecture.html"))


@app.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/dependencies")
def dependency_health() -> dict[str, Any]:
    settings = get_settings()
    neo4j = Neo4jClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
    try:
        return {
            "neo4j": neo4j.health(),
            "redis": redis_events.health(),
            "postgres_configured": bool(settings.postgres_url),
            "docker_configured": bool(settings.docker_host or os.name == "nt"),
        }
    finally:
        neo4j.close()


@app.get("/metrics")
def metrics() -> Response:
    return Response(content=prometheus_metrics(), media_type="text/plain; version=0.0.4")


@app.get("/observability")
def observability(principal=Depends(require_auth)) -> dict[str, object]:
    del principal
    return dashboard_snapshot(list_jobs(limit=1000))


@app.get("/worker/status")
def get_worker_status() -> dict[str, Any]:
    return auto_worker.get_status()


@app.post("/worker/toggle")
def toggle_worker(principal=Depends(require_admin)) -> dict[str, Any]:
    del principal
    auto_worker.toggle()
    return auto_worker.get_status()


class WorkerSetRequest(BaseModel):
    enabled: bool


class AnalysisRequest(BaseModel):
    repo_url: str
    issue_title: str = ""
    issue_text: str = ""
    issue_number: int = 0
    stack_trace: str = ""
    test_command: list[str] | None = None
    open_pr: bool = False
    repo_full_name: str = ""
    max_retries: int = Field(default=3, ge=0, le=10)


@app.post("/worker/set")
def set_worker(req: WorkerSetRequest, principal=Depends(require_admin)) -> dict[str, Any]:
    del principal
    auto_worker.set_enabled(req.enabled)
    return auto_worker.get_status()


@app.get("/jobs")
def list_jobs(status: str | None = None, limit: int = 50) -> list[dict]:
    """List all jobs with full pipeline progress metadata."""
    if hasattr(job_store, "list_jobs"):
        jobs = job_store.list_jobs(limit)
    else:
        with job_store._lock:
            rows = job_store._connection.execute(
                "SELECT job_id FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        jobs = [job_store.get(r["job_id"]) for r in rows]
        jobs = [j for j in jobs if j is not None]
    if status:
        jobs = [j for j in jobs if j["status"] == status]
    return jobs


@app.post("/webhook/github")
async def github_webhook(request: Request) -> dict[str, str]:
    settings = get_settings()
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256", "")

    if not verify_github_webhook_signature(body, signature, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    payload = await request.json()
    event_name = request.headers.get("X-GitHub-Event", "")
    action = payload.get("action", "")

    if event_name != "issues" or action not in {"opened", "labeled"}:
        return {"status": "ignored"}

    labels = {label.get("name") for label in payload.get("issue", {}).get("labels", [])}
    if action == "labeled" and "ai-fix" not in labels:
        return {"status": "ignored"}

    issue = payload.get("issue", {})
    repository = payload.get("repository", {})
    repo_full_name = payload.get("repo_full_name") or repository.get("full_name", "")
    if not allowed_repository(repo_full_name):
        raise HTTPException(status_code=403, detail="Repository is not allowlisted")
    normalized_payload = {
        **payload,
        "repo_url": payload.get("repo_url") or repository.get("clone_url") or repository.get("html_url", ""),
        "repo_full_name": repo_full_name,
        "issue_number": issue.get("number", payload.get("issue_number", 0)),
        "issue_title": issue.get("title", payload.get("issue_title", "")),
        "issue_text": issue.get("body", payload.get("issue_text", "")),
        "stack_trace": payload.get("stack_trace", ""),
    }
    job_id = enqueue_analysis_job(job_store, normalized_payload)
    record_job_event("queued", job_id)
    redis_events.publish("nexforge.jobs", {"event": "queued", "job_id": job_id})
    return {"status": "queued", "job_id": job_id}


@app.post("/repos/analyze")
def analyze_repo(repo_url: str | None = None, request: AnalysisRequest | None = None, principal=Depends(require_auth)) -> dict[str, str]:
    del principal
    data = request.model_dump() if request is not None else {"repo_url": repo_url or ""}
    repo_url = str(data.get("repo_url", ""))
    if not is_valid_repository_reference(repo_url):
        raise HTTPException(status_code=422, detail=repository_validation_message(repo_url))
    from app.cloning.repo_validation import repository_full_name

    full_name = data.get("repo_full_name") or repository_full_name(repo_url)
    if not allowed_repository(full_name):
        raise HTTPException(status_code=403, detail="Repository is not allowlisted")
    data["repo_full_name"] = full_name
    job_id = enqueue_analysis_job(job_store, data)
    record_job_event("queued", job_id)
    redis_events.publish("nexforge.jobs", {"event": "queued", "job_id": job_id})
    return {"status": "queued", "job_id": job_id}


@app.post("/jobs/run-next")
def run_next_job(principal=Depends(require_admin)) -> dict[str, Any]:
    del principal
    if auto_worker.is_busy:
        return {
            "status": "busy",
            "message": "Auto-worker is currently executing a job",
            "current_job_id": auto_worker.current_job_id,
        }
    if job_store.run_next_job():
        return {"status": "processed"}
    return {"status": "idle"}


@app.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    job = job_store.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, principal=Depends(require_auth)) -> dict[str, object]:
    del principal
    if not job_store.cancel(job_id):
        raise HTTPException(status_code=409, detail="Job cannot be cancelled in its current state")
    record_job_event("cancelled", job_id)
    redis_events.publish("nexforge.jobs", {"event": "cancelled", "job_id": job_id})
    return job_store.get(job_id) or {"job_id": job_id, "status": "cancelled"}
