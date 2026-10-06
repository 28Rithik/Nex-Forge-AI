# NexForge AI

Autonomous issue-to-PR assistant for single repos.

## Status

This repository is scaffolded for the first build slice:

- FastAPI ingress service
- GitHub webhook verification
- Job tracking
- Repo clone/parsing/graph/agent/sandbox/pr module boundaries
- Structured issue analysis input
- Generated reproduction tests
- Baseline versus patched test validation
- Protected-path patch validation
- Stale queue-job recovery

## Setup and Run

### Windows PowerShell: local development

From the project root, create and activate the virtual environment:

```powershell
Set-Location "C:\Users\rithi\Documents\Documents Project Intership and Course\Projects\Nex Forge AI"
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install dependencies and create the local environment file:

```powershell
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Start the FastAPI application:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open the application in your browser:

```text
http://127.0.0.1:8000/
http://127.0.0.1:8000/review
http://127.0.0.1:8000/architecture
```

The application includes an integrated background worker. To run a separate worker process instead, open a second PowerShell terminal:

```powershell
Set-Location "C:\Users\rithi\Documents\Documents Project Intership and Course\Projects\Nex Forge AI"
.\.venv\Scripts\Activate.ps1
python -m app.worker_main
```

Process one queued job and exit:

```powershell
python -m app.worker_main --once
```

Check the API and dependency status:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/dependencies
```

### Submit a repository analysis job

Repository-only indexing:

```powershell
Invoke-RestMethod `
	-Method Post `
	-Uri "http://127.0.0.1:8000/repos/analyze" `
	-ContentType "application/json" `
	-Body '{"repo_url":"https://github.com/owner/repository"}'
```

Issue-to-patch analysis:

```powershell
$body = @{
	repo_url = "https://github.com/owner/repository"
	issue_title = "Handle None input"
	issue_text = "parse_value crashes when value is None"
	issue_number = 12
	stack_trace = "AttributeError: None"
	test_command = @("pytest", "-q")
	open_pr = $false
} | ConvertTo-Json

Invoke-RestMethod `
	-Method Post `
	-Uri "http://127.0.0.1:8000/repos/analyze" `
	-ContentType "application/json" `
	-Body $body
```

View jobs and a specific job:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/jobs?limit=20"
Invoke-RestMethod "http://127.0.0.1:8000/jobs/{job_id}"
```

Cancel a queued or running job:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/jobs/{job_id}/cancel"
```

### Run tests and lint

```powershell
pytest
ruff check app tests
```

Run the mock end-to-end demo:

```powershell
pytest tests/test_core_pipeline.py -k end_to_end_demo
```

### Docker Compose: production-like local environment

Make sure Docker Desktop is running, then start PostgreSQL, Redis, Neo4j, and the API:

```powershell
docker compose up -d --build
```

Build the language-specific sandbox images:

```powershell
docker build -t nexforge/runner-python:latest -f docker/runner-python.Dockerfile .
docker build -t nexforge/runner-node:latest -f docker/runner-node.Dockerfile .
```

View service status and logs:

```powershell
docker compose ps
docker compose logs -f nexforge-api
docker compose logs -f neo4j
```

Stop services:

```powershell
docker compose down
```

Run opt-in infrastructure tests after the services are healthy:

```powershell
$env:NEXFORGE_RUN_INTEGRATION = "true"
pytest tests/integration -m integration
```

## Endpoints

- `POST /webhook/github`
- `POST /repos/analyze`
- `GET /jobs/{job_id}`
- `GET /health/dependencies`

### Structured analysis request

```json
{
	"repo_url": "https://github.com/owner/repository",
	"issue_title": "Handle None input",
	"issue_text": "parse_value crashes when value is None",
	"issue_number": 12,
	"stack_trace": "AttributeError: None",
	"test_command": ["pytest", "-q"],
	"open_pr": false
}
```

The worker runs the repository tests before the patch, generates an issue-specific reproduction test, applies and validates the patch, then runs the patched tests. A pull request is only eligible when the patch is within the affected-file boundary and the patched tests pass.

Jobs that remain `running` beyond `JOB_STALE_TIMEOUT_SECONDS` are recovered by the auto-worker and returned to the queue while retry capacity remains.

## Phase 3 AI quality

Agent outputs use Pydantic schemas with bounded confidence scores, affected symbols, evidence, patch rationale, and changed-file metadata. Graph RAG ranks exact symbols above generic issue terms and adds graph-neighbor evidence. The coder can produce multiple candidates; the orchestrator evaluates candidates and selects the first passing patch. Debugger output can include an automatic reproduction test artifact, and test results are classified as `passed`, `patch_regression`, `pre_existing`, `environment`, or `timeout` with a likely-cause explanation.

## Phase 4 production features

Production Compose also provisions PostgreSQL and Redis. Set `POSTGRES_URL` to switch job persistence from SQLite to PostgreSQL; Redis publishes job lifecycle events and is exposed through dependency health checks. Operational endpoints require `API_KEY` when `AUTH_ENABLED=true`.

Repository access can be restricted with `ALLOWED_REPOSITORIES=owner/repository,owner/another-repository`. Jobs can be cancelled with `POST /jobs/{job_id}/cancel`, and worker controls require administrator authentication. Prometheus metrics are available at `/metrics`, while `/observability` returns a dashboard snapshot.

Before a PR is eligible, the worker runs Ruff and Bandit scans. The GitHub Actions workflow at `.github/workflows/ci.yml` runs tests, Ruff, and Bandit on pushes and pull requests.