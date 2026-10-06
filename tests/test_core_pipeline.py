from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import subprocess

from app.agents.orchestrator import build_orchestrator
from app.graph.graph_builder import GraphBuilder
from app.github_integration.pr_generator import build_pull_request_payload
from app.jobs.queue import JobStore
from app.jobs.worker import JobProcessor
from app.parsing.treesitter_parser import TreeSitterParser
from app.retrieval.graph_rag import GraphRAGRetriever
from app.sandbox.docker_runner import DockerSandboxRunner
from app.agents.tester_agent import run_repo_tests
from app.agents.reproduction import build_reproduction_test
from app.agents.debugger_agent import analyze_issue
from app.agents.coder_agent import draft_patch_candidates
from app.agents.test_reasoning import analyze_test_results
from app.validation import validate_unified_patch


def test_job_store_persists_and_updates(tmp_path: Path) -> None:
    store = JobStore(f"sqlite:///{tmp_path / 'jobs.sqlite3'}")
    job_id = store.create({"issue_text": "bug report", "title": "Bug report"})

    record = store.get(job_id)
    assert record is not None
    assert record["status"] == "queued"
    assert record["payload"]["issue_text"] == "bug report"

    store.run_job(job_id)
    completed = store.get(job_id)
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["result"]["message"] == "No repo_url provided"


def test_job_processor_handles_missing_repo_url() -> None:
    result = JobProcessor().process({"issue_text": "bug report", "title": "Bug report"})

    assert result["status"] == "completed"
    assert result["message"] == "No repo_url provided"


def test_manual_repository_analysis_does_not_attempt_patch(tmp_path: Path) -> None:
    repo = tmp_path / "analysis-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "module.py").write_text("def hello():\n    return 'hello'\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "seed analysis repo"], cwd=repo, check=True, capture_output=True, text=True)

    processor = JobProcessor()
    processor._build_neo4j_client = lambda: None  # type: ignore[method-assign]

    result = processor.process({"repo_url": str(repo)})

    assert result["status"] == "completed"
    assert result["analysis_only"] is True
    assert result["parsed_files"] == 1


def test_docker_unavailable_is_reported_without_windows_shell_error(monkeypatch, tmp_path: Path) -> None:
    class BrokenDockerRunner:
        def __init__(self):
            raise FileNotFoundError("docker executable not found")

    monkeypatch.setattr("app.agents.tester_agent.DockerSandboxRunner", BrokenDockerRunner)
    result = run_repo_tests(tmp_path, ["/bin/sh", "-lc", "pytest -q"], use_docker=True)

    assert result["passed"] is False
    assert result["sandbox_error"] is True
    assert "Docker sandbox unavailable" in result["logs"]
    assert "WinError 2" not in result["logs"]


def test_parser_extracts_python_entities(tmp_path: Path) -> None:
    source = tmp_path / "sample.py"
    source.write_text(
        """
import os

class ExampleBase:
    pass


class Example(ExampleBase):
    def run(self, value):
        helper(value)


def helper(value):
    return value
""",
        encoding="utf-8",
    )

    parsed = TreeSitterParser().parse_file(source, "python")

    kinds = {entity["kind"] for entity in parsed.entities}
    relations = {relation["kind"] for relation in parsed.relations}

    assert "Class" in kinds
    assert "Function" in kinds
    assert "Import" in kinds
    assert "EXTENDS" in relations
    assert "HAS_METHOD" in relations
    assert "CALLS" in relations


def test_graph_builder_emits_merge_queries() -> None:
    queries: list[str] = []

    class FakeClient:
        def execute_query(self, cypher: str, **params):
            queries.append(cypher)
            return []

    parsed_files = [
        {
            "path": "sample.py",
            "language": "python",
            "hash": "abc",
            "entities": [
                {"kind": "Function", "name": "helper", "start_line": 1, "end_line": 3, "signature": "(value)", "docstring": "", "class_name": ""},
                {"kind": "Class", "name": "Example", "bases": ["ExampleBase"], "start_line": 4, "end_line": 8},
                {"kind": "Import", "module": "os", "name": "os"},
            ],
            "relations": [
                {"kind": "EXTENDS", "source": "Example", "target": "ExampleBase"},
                {"kind": "HAS_METHOD", "source": "Example", "target": "helper"},
                {"kind": "CALLS", "source": "Example", "target": "helper"},
            ],
        }
    ]

    result = GraphBuilder(FakeClient()).upsert_parsed_files(parsed_files)

    assert result["files_processed"] == 1
    assert result["nodes_written"] == 3
    assert result["relationships_written"] == 3
    assert any("MERGE (f:File" in query for query in queries)
    assert any("MERGE (n:Function" in query for query in queries)


def test_graph_builder_skips_unchanged_files() -> None:
    queries: list[str] = []

    class FakeClient:
        def execute_query(self, cypher: str, **params):
            queries.append(cypher)
            if "RETURN f.path AS path, f.hash AS hash" in cypher:
                return [{"path": "unchanged.py", "hash": "same"}]
            return []

    parsed_files = [
        {
            "path": "unchanged.py",
            "language": "python",
            "hash": "same",
            "entities": [{"kind": "Function", "name": "helper", "start_line": 1, "end_line": 3, "signature": "()", "docstring": "", "class_name": ""}],
            "relations": [],
        },
        {
            "path": "changed.py",
            "language": "python",
            "hash": "different",
            "entities": [{"kind": "Function", "name": "changed", "start_line": 1, "end_line": 3, "signature": "()", "docstring": "", "class_name": ""}],
            "relations": [],
        },
    ]

    result = GraphBuilder(FakeClient()).upsert_parsed_files(parsed_files)

    assert result["files_processed"] == 1
    assert result["files_skipped"] == 1
    assert queries.count("\n                MERGE (f:File {path: $path})\n                SET f.language = $language, f.hash = $hash\n                ") == 1


def test_patch_validation_rejects_out_of_scope_file() -> None:
    patch = "--- a/parser.py\n+++ b/parser.py\n@@ -1 +1 @@\n-old\n+new\n"

    result = validate_unified_patch(patch, ["other.py"])

    assert result["valid"] is False
    assert "outside affected_files" in result["reason"]


def test_patch_validation_rejects_protected_paths() -> None:
    patch = "--- a/.env\n+++ b/.env\n@@ -1 +1 @@\n-OLD\n+SECRET\n"

    result = validate_unified_patch(patch, [".env"])

    assert result["valid"] is False
    assert "protected paths" in result["reason"]


def test_reproduction_test_generator_creates_python_artifact() -> None:
    reproduction = build_reproduction_test(
        "parser.py parse_value fails when value is None",
        ["parser.py"],
        "Missing None guard",
    )

    assert "from parser import parse_value" in reproduction
    assert "parse_value(None)" in reproduction
    assert "test_nexforge_reproduces_issue" in reproduction


def test_debugger_returns_confidence_and_evidence_schema() -> None:
    result = analyze_issue("parser.py parse_value fails on None", "- parser.py\n- parse_value")

    assert 0.0 <= result["confidence"] <= 1.0
    assert result["affected_files"] == ["parser.py"]
    assert result["evidence"]


def test_coder_returns_structured_patch_candidates() -> None:
    candidates = draft_patch_candidates(
        "Missing None guard",
        ["parser.py"],
        {"parser.py": "def parse_value(value):\n    return int(value)\n"},
    )

    assert candidates
    assert candidates[0].patch.startswith("--- a/parser.py")
    assert 0.0 <= candidates[0].confidence <= 1.0
    assert candidates[0].changed_files == ["parser.py"]


def test_test_reasoning_classifies_environment_failure() -> None:
    analysis = analyze_test_results(
        {"passed": False, "logs": "Docker sandbox unavailable: daemon is not running", "sandbox_error": True, "exit_code": 125},
        {"passed": True, "logs": "baseline passed"},
    )

    assert analysis["category"] == "environment"
    assert analysis["passed"] is False
    assert analysis["confidence"] > 0.5


def test_graph_rag_retriever_formats_context() -> None:
    class FakeClient:
        def execute_query(self, cypher: str, **params):
            return [{"start": {"name": "Example"}, "neighbor": {"name": "helper"}}]

    retriever = GraphRAGRetriever(FakeClient())
    context = retriever.retrieve("Traceback in Example.run when helper fails")

    assert "Candidate symbols:" in context
    assert "Example" in context
    assert "Graph matches:" in context


def test_graph_rag_ranks_exact_symbols_first() -> None:
    retriever = GraphRAGRetriever()
    ranked = retriever._rank_candidates("parse_value fails in parser.py", ["fails", "parser.py", "parse_value"], [])

    assert ranked[0] == "parse_value"
    assert ranked[1] == "parser.py"


def test_orchestrator_reaches_pr_node() -> None:
    graph = build_orchestrator(
        debugger=lambda issue_text, graph_context: {
            "root_cause": "missing null check",
            "affected_files": ["app/main.py"],
            "reproduction_test": "pytest -q",
        },
        coder=lambda root_cause, affected_files, file_contents: "diff --git a/app/main.py b/app/main.py",
        tester=lambda patch: {"passed": True, "logs": "ok", "failing_tests": []},
    )

    state = graph.invoke({"issue_text": "bug", "graph_context": "context", "file_contents": {}, "retry_count": 0, "issue_number": 7})

    assert state["patch"].startswith("diff --git")
    assert "Closes #7" in state["pr_body"]


def test_docker_runner_uses_client() -> None:
    class FakeContainer:
        def wait(self, timeout: int):
            return {"StatusCode": 0}

        def logs(self, stdout: bool, stderr: bool):
            return b"tests passed"

        def kill(self):
            raise AssertionError("kill should not be called")

    class FakeContainers:
        def run(self, **kwargs):
            return FakeContainer()

    class FakeClient:
        containers = FakeContainers()

    result = DockerSandboxRunner(FakeClient()).run(
        image="python:3.11-slim",
        command=["pytest"],
        workspace_path=Path.cwd(),
        timeout_seconds=30,
    )

    assert result.passed is True
    assert "tests passed" in result.stdout


def test_queue_claims_and_runs_one_job() -> None:
    store = JobStore("sqlite:///:memory:")
    job_id = store.create({"issue_text": "bug report", "title": "Bug report"})
    store._processor.process = lambda payload: {"status": "completed", "message": payload["title"]}  # type: ignore[method-assign]

    assert store.run_next_job() is True
    record = store.get(job_id)
    assert record is not None
    assert record["status"] == "completed"


def test_queue_recovers_stale_running_job() -> None:
    store = JobStore("sqlite:///:memory:")
    job_id = store.create({"issue_text": "bug", "max_retries": 3})
    store._connection.execute(
        "UPDATE jobs SET status = 'running', attempts = 1, updated_at = '2000-01-01T00:00:00+00:00' WHERE job_id = ?",
        (job_id,),
    )
    store._connection.commit()

    assert store.recover_stale_jobs(timeout_seconds=1) == 1
    recovered = store.get(job_id)
    assert recovered is not None
    assert recovered["status"] == "queued"
    assert "Recovered stale" in recovered["error"]


def test_queue_failure_moves_job_to_failed_state() -> None:
    store = JobStore("sqlite:///:memory:")
    job_id = store.create({"issue_text": "bug report", "title": "Bug report", "max_retries": 1})

    def boom(payload):
        raise RuntimeError(payload["title"])

    store._processor.process = boom  # type: ignore[method-assign]

    assert store.run_next_job() is True
    record = store.get(job_id)
    assert record is not None
    assert record["status"] == "failed"
    assert "Bug report" in str(record["error"])


def test_job_processor_human_review_when_repo_tests_fail(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "sample-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "parser.py").write_text(
        """
def parse_value(value):
    return int(value)
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "seed bug repo"], cwd=repo, check=True, capture_output=True, text=True)

    open_pr_calls = []

    class FakeGitHubPRGenerator:
        def __init__(self, token: str | None = None, github_client=None) -> None:
            self.token = token

        def commit_patch(self, workspace_path: Path, branch_name: str, commit_message: str) -> None:
            open_pr_calls.append((str(workspace_path), branch_name, commit_message))

        def open_pull_request(self, **kwargs):
            open_pr_calls.append(kwargs)
            return SimpleNamespace(url="https://example.com/pr/1", number=1)

    monkeypatch.setattr("app.jobs.worker.GitHubPRGenerator", FakeGitHubPRGenerator)

    processor = JobProcessor()
    processor.settings.github_token = "token"
    processor.settings.github_repository = "owner/repo"
    processor._build_neo4j_client = lambda: None  # type: ignore[method-assign]
    processor._run_repo_tests = lambda workspace_path: {"passed": False, "logs": "pytest failed", "failing_tests": ["test_parse_value_none_returns_none"], "exit_code": 1}  # type: ignore[method-assign]

    result = processor.process(
        {
            "repo_url": str(repo),
            "repo_full_name": "owner/repo",
            "issue_text": "parser.py parse_value fails on None and needs a null guard",
            "issue_title": "Fix parse_value null handling",
            "issue_number": 43,
            "open_pr": True,
        }
    )

    assert result["status"] == "human_review"
    assert result["pr_result"] is None
    assert result["test_results"]["passed"] is False
    assert open_pr_calls == []


def test_end_to_end_demo_generates_pr_payload_and_open_pr(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "sample-repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "requirements.txt").write_text("pytest==8.3.2\n", encoding="utf-8")
    (repo / "parser.py").write_text(
        """
def parse_value(value):
    return int(value)
""",
        encoding="utf-8",
    )
    (repo / "test_parser.py").write_text(
        """
from parser import parse_value


def test_parse_value_none_returns_none():
    assert parse_value(None) is None


def test_parse_value_string_returns_int():
    assert parse_value("7") == 7
""",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "seed bug repo"], cwd=repo, check=True, capture_output=True, text=True)

    committed = []

    class FakeGitHubPRGenerator:
        def __init__(self, token: str | None = None, github_client=None) -> None:
            self.token = token

        def commit_patch(self, workspace_path: Path, branch_name: str, commit_message: str) -> None:
            committed.append((str(workspace_path), branch_name, commit_message))

        def open_pull_request(self, *, repo_full_name: str, title: str, body: str, head: str, base: str = "main", labels=None):
            committed.append((repo_full_name, title, body, head, base, labels))
            return SimpleNamespace(url="https://example.com/pr/1", number=1)

    monkeypatch.setattr("app.jobs.worker.GitHubPRGenerator", FakeGitHubPRGenerator)

    processor = JobProcessor()
    processor.settings.github_token = "token"
    processor.settings.github_repository = "owner/repo"
    processor._build_neo4j_client = lambda: None  # type: ignore[method-assign]
    processor._run_repo_tests = lambda workspace_path: {"passed": True, "logs": "ok", "failing_tests": [], "exit_code": 0}  # type: ignore[method-assign]

    result = processor.process(
        {
            "repo_url": str(repo),
            "repo_full_name": "owner/repo",
            "issue_text": "parser.py parse_value fails on None and needs a null guard",
            "issue_title": "Fix parse_value null handling",
            "issue_number": 42,
            "open_pr": True,
        }
    )

    assert result["status"] == "completed"
    assert result["patch_applied"] is True
    assert result["test_results"]["passed"] is True
    assert "baseline_test_results" in result
    assert result["pr_result"]["url"] == "https://example.com/pr/1"
    assert committed
    assert "Closes #42" in result["pr_payload"]["body"]


def test_pr_payload_builds_expected_fields() -> None:
    payload = build_pull_request_payload(12, "Null pointer on startup", "missing null check", ["app/main.py"], "all tests passed")

    assert payload["title"] == "Fix: Null pointer on startup"
    assert "Closes #12" in payload["body"]
    assert payload["labels"] == ["ai-generated"]