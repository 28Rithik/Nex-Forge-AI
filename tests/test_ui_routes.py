from fastapi.testclient import TestClient

from app.main import app, auto_worker, job_store


def test_ui_pages_are_served() -> None:
    with TestClient(app) as client:
        operations = client.get("/")
        review = client.get("/review")
        architecture = client.get("/architecture")

    assert operations.status_code == 200
    assert "Autonomous Issue" in operations.text
    assert review.status_code == 200
    assert "Review the work" in review.text
    assert architecture.status_code == 200
    assert "repair pipeline" in architecture.text


def test_structured_analysis_request_is_accepted() -> None:
    auto_worker.set_enabled(False)
    try:
        with TestClient(app) as client:
            response = client.post(
                "/repos/analyze",
                json={
                    "repo_url": "https://github.com/owner/repository",
                    "issue_title": "Handle None input",
                    "issue_text": "parse_value crashes when value is None",
                    "issue_number": 12,
                    "stack_trace": "AttributeError: None",
                    "test_command": ["pytest", "-q"],
                    "open_pr": False,
                },
            )
    finally:
        auto_worker.set_enabled(True)

    assert response.status_code == 200
    assert response.json()["status"] == "queued"


def test_invalid_analysis_repository_is_rejected() -> None:
    with TestClient(app) as client:
        response = client.post(
            "/repos/analyze",
            json={"repo_url": "NexForge dashboard text pasted here"},
        )

    assert response.status_code == 422


def test_job_cancellation_endpoint() -> None:
    job_id = job_store.create({"repo_url": "https://github.com/owner/repository"})
    with TestClient(app) as client:
        response = client.post(f"/jobs/{job_id}/cancel")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_metrics_endpoint_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "nexforge_job_events" in response.text
