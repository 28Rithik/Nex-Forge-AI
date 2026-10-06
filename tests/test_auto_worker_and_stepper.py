from __future__ import annotations

import time
from pathlib import Path

from app.jobs.auto_worker import AutoWorker
from app.jobs.queue import JobStore, PIPELINE_STAGES
from fastapi.testclient import TestClient
from app.main import app


def test_pipeline_stages_initialized_and_tracked(tmp_path: Path) -> None:
    store = JobStore(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    job_id = store.create({"issue_text": "sample issue", "title": "Test Issue"})

    job = store.get(job_id)
    assert job is not None
    assert job["progress"] is not None
    assert len(job["progress"]["steps"]) == 6
    for stage in PIPELINE_STAGES:
        assert stage["key"] in job["progress"]["steps"]
        assert job["progress"]["steps"][stage["key"]]["status"] == "pending"

    # Simulate running through stages
    store.update_progress(job_id, "clone", "running", "Cloning test repo...")
    updated = store.get(job_id)
    assert updated["progress"]["current_step"] == "clone"
    assert updated["progress"]["steps"]["clone"]["status"] == "running"

    store.update_progress(job_id, "clone", "completed", "Repo cloned")
    store.update_progress(job_id, "ast_parsing", "running", "Parsing AST...")
    updated2 = store.get(job_id)
    assert updated2["progress"]["steps"]["clone"]["status"] == "completed"
    assert updated2["progress"]["steps"]["ast_parsing"]["status"] == "running"


def test_auto_worker_processes_jobs(tmp_path: Path) -> None:
    store = JobStore(f"sqlite:///{tmp_path / 'auto_worker.sqlite3'}")
    # Mock processor to succeed quickly
    store._processor.process = lambda payload, progress_callback=None: {
        "status": "completed",
        "message": "processed by auto-worker",
    }

    worker = AutoWorker(store, poll_interval=0.1, enabled=True)
    worker.start()

    try:
        job_id = store.create({"issue_text": "auto bug", "title": "Auto Bug"})
        assert store.get(job_id)["status"] == "queued"

        # Wait for worker to pick up and process
        start_time = time.time()
        while time.time() - start_time < 3.0:
            rec = store.get(job_id)
            if rec and rec["status"] == "completed":
                break
            time.sleep(0.1)

        rec = store.get(job_id)
        assert rec is not None
        assert rec["status"] == "completed"
        assert worker.processed_count >= 1
    finally:
        worker.stop()


def test_worker_api_endpoints() -> None:
    client = TestClient(app)
    status_res = client.get("/worker/status")
    assert status_res.status_code == 200
    data = status_res.json()
    assert "enabled" in data
    assert "is_busy" in data
    assert "processed_count" in data

    # Test toggle
    toggle_res = client.post("/worker/toggle")
    assert toggle_res.status_code == 200
    toggled_data = toggle_res.json()
    assert toggled_data["enabled"] != data["enabled"]

    # Restore to enabled=True
    set_res = client.post("/worker/set", json={"enabled": True})
    assert set_res.status_code == 200
    assert set_res.json()["enabled"] is True
