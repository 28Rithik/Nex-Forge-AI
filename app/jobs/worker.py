from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable
import subprocess
import time

from app.agents.orchestrator import run_fix_cycle
from app.cloning.repo_cloner import RepoCloner
from app.github_integration.pr_generator import GitHubPRGenerator, build_pull_request_payload
from app.graph.graph_builder import GraphBuilder
from app.graph.neo4j_client import Neo4jClient
from app.retrieval.graph_rag import GraphRAGRetriever
from app.config import get_settings
from app.parsing.treesitter_parser import TreeSitterParser
from app.agents.tester_agent import run_repo_tests, summarize_test_logs
from app.agents.test_reasoning import analyze_test_results
from app.security_scan import run_security_scans
from app.validation import validate_unified_patch


class JobProcessor:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.parser = TreeSitterParser()

    def process(
        self,
        payload: dict[str, Any],
        progress_callback: Callable[[str, str, str], None] | None = None,
    ) -> dict[str, Any]:
        def report(stage: str, status: str, detail: str) -> None:
            if progress_callback is not None:
                try:
                    progress_callback(stage, status, detail)
                except Exception:
                    pass

        repo_url = str(payload.get("repo_url", "")).strip()
        issue_text = str(payload.get("issue_text", payload.get("title", "")))
        issue_number = int(payload.get("issue_number", 0) or 0)
        issue_title = str(payload.get("issue_title", payload.get("title", "")))
        repo_full_name = str(payload.get("repo_full_name", self.settings.github_repository or "")).strip()

        if not repo_url:
            report("clone", "completed", "No repo_url provided; skipped cloning")
            return {"status": "completed", "message": "No repo_url provided", "payload": payload}

        report("clone", "running", f"Cloning repository {repo_url}...")
        with TemporaryDirectory(prefix="nexforge-") as temp_dir:
            temp_root = Path(temp_dir)
            cloner = RepoCloner(temp_root / "workspace")
            clone_result = cloner.clone(repo_url, job_id=str(payload.get("job_id", "manual")), issue_number=issue_number)
            workspace_path = clone_result.workspace_path
            report("clone", "completed", f"Repository cloned (workspace ready on branch {clone_result.branch_name})")

            report("ast_parsing", "running", "Parsing source code with Tree-Sitter & Python AST...")
            parsed_files = self.parser.parse_workspace(workspace_path)
            report("ast_parsing", "completed", f"Extracted AST entities and relations from {len(parsed_files)} source files")

            report("graph_indexing", "running", "Indexing entities into code graph and running GraphRAG...")
            neo4j_client = self._build_neo4j_client()
            try:
                if neo4j_client is not None:
                    try:
                        neo4j_client.ensure_schema()
                        GraphBuilder(neo4j_client).upsert_parsed_files(parsed_files)
                    except Exception:
                        neo4j_client = None  # Gracefully fall back if Neo4j is offline
                retriever = GraphRAGRetriever(neo4j_client)
                graph_context = retriever.retrieve(issue_text or issue_title or repo_url)
                retrieved_lines = len(graph_context.splitlines())
                report("graph_indexing", "completed", f"Graph context assembled ({retrieved_lines} lines of context retrieved)")

                if not issue_text.strip() and not issue_title.strip():
                    report("debugger", "completed", "Skipped: manual repository indexing has no issue to diagnose")
                    report("coder", "completed", "Skipped: no issue patch requested")
                    report("sandbox_pr", "completed", "Skipped: repository analysis only")
                    return {
                        "status": "completed",
                        "analysis_only": True,
                        "message": "Repository indexed successfully; provide an issue title or body to generate a fix",
                        "repo_url": repo_url,
                        "workspace": str(workspace_path),
                        "parsed_files": len(parsed_files),
                        "graph_context": graph_context,
                        "issue_number": issue_number,
                        "issue_title": issue_title,
                    }

                file_contents = {
                    alias: Path(record["path"]).read_text(encoding="utf-8", errors="ignore")
                    for record in parsed_files
                    for alias in {
                        record["path"],
                        Path(record["path"]).name,
                        Path(record["path"]).as_posix(),
                    }
                }

                report("debugger", "running", "Debugger Agent analyzing issue report and graph context...")
                fix_cycle = run_fix_cycle(issue_text or issue_title or repo_url, graph_context, file_contents)
                root_cause = str(
                    fix_cycle.get("root_cause", "")
                    or fix_cycle.get("debugger_output", {}).get("root_cause", "")
                    or "Identified bug root cause"
                )
                affected_files = list(
                    fix_cycle.get("affected_files", [])
                    or fix_cycle.get("debugger_output", {}).get("affected_files", [])
                )
                report("debugger", "completed", f"Root cause: {root_cause[:65]} ({len(affected_files)} files affected)")

                report("coder", "running", f"Coder Agent generating unified diff patch for {len(affected_files)} files...")
                pr_payload = build_pull_request_payload(
                    issue_number,
                    issue_title or "Automated fix",
                    root_cause,
                    affected_files,
                    str(fix_cycle.get("test_results", {})),
                )
                patch = str(fix_cycle.get("patch", ""))
                patch_validation = validate_unified_patch(patch, affected_files)
                if not patch_validation["valid"]:
                    report("coder", "failed", str(patch_validation["reason"]))

                report("sandbox_pr", "running", "Executing test suite inside Docker sandbox container...")
                baseline_test_results = self._run_tests_compat(workspace_path, payload.get("test_command"))
                report(
                    "sandbox_pr",
                    "running",
                    "Baseline tests completed: " + ("passed" if baseline_test_results.get("passed") else "failed as expected or pre-existing"),
                )

                patch_applied = False
                if patch_validation["valid"]:
                    patch_applied = self._apply_patch(workspace_path, patch)
                    diff_line_count = len(patch.splitlines())
                    patch_status = "applied to workspace" if patch_applied else "synthesized"
                    report("coder", "completed" if patch_applied else "failed", f"Unified diff patch {patch_status} ({diff_line_count} diff lines)")

                reproduction_test = str(
                    fix_cycle.get("reproduction_test", "")
                    or fix_cycle.get("debugger_output", {}).get("reproduction_test", "")
                )
                reproduction_path = self._write_reproduction_test(workspace_path, reproduction_test) if patch_applied else None
                if patch_applied:
                    patched_test_results = self._run_tests_compat(workspace_path, payload.get("test_command"))
                else:
                    patched_test_results = {
                        "passed": False,
                        "logs": "Patch was not applied because validation failed",
                        "failing_tests": [],
                        "exit_code": 1,
                    }
                test_results = patched_test_results
                if not test_results.get("passed"):
                    test_results["logs"] = summarize_test_logs(str(test_results.get("logs", "")))
                test_analysis = analyze_test_results(test_results, baseline_test_results)
                security_scan = run_security_scans(workspace_path, enabled=self.settings.security_scan_enabled)

                pr_result: dict[str, Any] | None = None
                github_token = self.settings.github_token.strip()
                github_app_ready = bool(
                    self.settings.github_app_id
                    and self.settings.github_private_key
                    and self.settings.github_installation_id
                )
                if (github_token or github_app_ready) and repo_full_name and patch_validation["valid"] and patch_applied and test_results.get("passed") and security_scan["passed"] and payload.get("open_pr", False):
                    report("sandbox_pr", "running", "Tests passed! Opening Pull Request on GitHub...")
                    pr_result = self._open_pr(
                        workspace_path=workspace_path,
                        branch_name=clone_result.branch_name,
                        repo_full_name=repo_full_name,
                        issue_number=issue_number,
                        issue_title=issue_title or "Automated fix",
                        pr_payload=pr_payload,
                        github_token=github_token,
                    )
                    report("sandbox_pr", "completed", f"Tests passed & PR #{pr_result['number']} created at {pr_result['url']}")
                elif test_results.get("passed"):
                    logs_snippet = str(test_results.get("logs", "all tests passed")).strip().splitlines()
                    first_line = logs_snippet[0] if logs_snippet else "tests verified"
                    report("sandbox_pr", "completed", f"Sandbox tests passed ({first_line[:55]})")
                else:
                    report("sandbox_pr", "failed", f"Sandbox tests failed (exit code {test_results.get('exit_code', 1)})")

                status = "completed" if patch_validation["valid"] and patch_applied and test_results.get("passed") and security_scan["passed"] else "human_review"

                return {
                    "status": status,
                    "repo_url": repo_url,
                    "workspace": str(workspace_path),
                    "parsed_files": len(parsed_files),
                    "graph_context": graph_context,
                    "fix_cycle": fix_cycle,
                    "patch_applied": patch_applied,
                    "patch_validation": patch_validation,
                    "reproduction_test": reproduction_test,
                    "reproduction_path": reproduction_path,
                    "baseline_test_results": baseline_test_results,
                    "test_results": test_results,
                    "test_analysis": test_analysis,
                    "security_scan": security_scan,
                    "confidence": fix_cycle.get("debugger_result", {}).get("confidence", 0.0),
                    "candidate_patches": fix_cycle.get("candidate_patches", []),
                    "pr_payload": pr_payload,
                    "pr_result": pr_result,
                    "issue_number": issue_number,
                    "issue_title": issue_title,
                    "note": "PR creation is available when GitHub credentials and open_pr=true are provided",
                }
            finally:
                if neo4j_client is not None:
                    neo4j_client.close()
                cloner.cleanup(str(payload.get("job_id", "manual")))

    def _build_neo4j_client(self) -> Neo4jClient | None:
        if not self.settings.neo4j_uri:
            return None
        if not self.settings.neo4j_user or not self.settings.neo4j_password:
            return None
        try:
            return Neo4jClient(self.settings.neo4j_uri, self.settings.neo4j_user, self.settings.neo4j_password)
        except Exception:
            return None

    def _apply_patch(self, workspace_path: Path, patch: str) -> bool:
        completed = subprocess.run(
            ["git", "-C", str(workspace_path), "apply", "--whitespace=nowarn", "-"],
            input=patch,
            text=True,
            capture_output=True,
        )
        return completed.returncode == 0

    def _run_repo_tests(self, workspace_path: Path, test_command: list[str] | None = None) -> dict[str, Any]:
        if test_command:
            return run_repo_tests(
                workspace_path,
                test_command,
                use_docker=True,
                timeout_seconds=self.settings.sandbox_timeout_seconds,
                image=self._sandbox_image(workspace_path),
            )
        requirements = workspace_path / "requirements.txt"
        if requirements.exists():
            command = [
                "/bin/sh",
                "-lc",
                "python -m pip install -r requirements.txt >/tmp/nexforge-pip.log 2>&1 && pytest -q",
            ]
        elif (workspace_path / "package.json").exists():
            command = ["/bin/sh", "-lc", "npm test -- --runInBand"]
        else:
            return {"passed": True, "logs": "No repository test command detected", "failing_tests": [], "exit_code": 0}

        return run_repo_tests(
            workspace_path,
            command,
            use_docker=True,
            timeout_seconds=self.settings.sandbox_timeout_seconds,
            image=self._sandbox_image(workspace_path),
        )

    def _sandbox_image(self, workspace_path: Path) -> str:
        if (workspace_path / "package.json").exists() or (workspace_path / "pnpm-lock.yaml").exists() or (workspace_path / "yarn.lock").exists():
            return self.settings.sandbox_node_image
        return self.settings.sandbox_python_image

    def _run_tests_compat(self, workspace_path: Path, test_command: list[str] | None) -> dict[str, Any]:
        try:
            return self._run_repo_tests(workspace_path, test_command)
        except TypeError as exc:
            if "positional argument" not in str(exc) and "given" not in str(exc):
                raise
            return self._run_repo_tests(workspace_path)  # type: ignore[call-arg]

    def _write_reproduction_test(self, workspace_path: Path, content: str) -> str | None:
        if not content.strip() or not content.lstrip().startswith(("import", "from")):
            return None
        test_path = workspace_path / "tests" / "test_nexforge_reproduction.py"
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text(content, encoding="utf-8")
        return str(test_path.relative_to(workspace_path)).replace("\\", "/")

    def _open_pr(
        self,
        *,
        workspace_path: Path,
        branch_name: str,
        repo_full_name: str,
        issue_number: int,
        issue_title: str,
        pr_payload: dict[str, object],
        github_token: str,
    ) -> dict[str, Any]:
        try:
            generator = GitHubPRGenerator(
                token=github_token,
                app_id=self.settings.github_app_id,
                private_key=self.settings.github_private_key,
                installation_id=self.settings.github_installation_id,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            generator = GitHubPRGenerator(token=github_token)
        commit_message = str(pr_payload["title"])
        generator.commit_patch(workspace_path, branch_name, commit_message)
        result = generator.open_pull_request(
            repo_full_name=repo_full_name,
            title=str(pr_payload["title"]),
            body=str(pr_payload["body"]),
            head=branch_name,
            labels=list(pr_payload.get("labels", [])),
        )
        return {
            "url": result.url,
            "number": result.number,
            "issue_number": issue_number,
            "issue_title": issue_title,
        }


class JobQueueWorker:
    def __init__(self, store, poll_interval_seconds: float = 1.0) -> None:
        self.store = store
        self.poll_interval_seconds = poll_interval_seconds

    def run_once(self) -> bool:
        return self.store.run_next_job()

    def run_forever(self, stop_after_idle_cycles: int | None = None) -> None:
        idle_cycles = 0
        while True:
            processed = self.run_once()
            if processed:
                idle_cycles = 0
                continue
            idle_cycles += 1
            if stop_after_idle_cycles is not None and idle_cycles >= stop_after_idle_cycles:
                return
            time.sleep(self.poll_interval_seconds)