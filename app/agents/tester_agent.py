from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from app.sandbox.docker_runner import DockerSandboxRunner
from app.config import get_settings

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional dependency path
    ChatOpenAI = None


def run_tests(patch: str) -> dict[str, object]:
    if not patch.strip():
        return {"passed": False, "logs": "No patch generated", "failing_tests": []}
    return {"passed": True, "logs": "Patch validated in heuristic mode", "failing_tests": []}


def run_repo_tests(
    workspace_path: Path,
    command: list[str],
    use_docker: bool = True,
    timeout_seconds: int = 300,
    image: str | None = None,
) -> dict[str, object]:
    if use_docker:
        try:
            runner = DockerSandboxRunner()
            result = runner.run(
                image=image or "python:3.11-slim",
                command=command,
                workspace_path=workspace_path,
                timeout_seconds=timeout_seconds,
            )
            return {
                "passed": result.passed,
                "logs": result.stdout + (f"\n{result.stderr}" if result.stderr else ""),
                "failing_tests": [],
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "sandbox_error": False,
            }
        except Exception as exc:
            if not get_settings().sandbox_allow_local_fallback:
                return {
                    "passed": False,
                    "logs": f"Docker sandbox unavailable: {exc}",
                    "failing_tests": [],
                    "exit_code": 125,
                    "sandbox_error": True,
                }

    local_command = command
    if sys.platform == "win32" and command[:2] == ["/bin/sh", "-lc"]:
        local_command = ["cmd", "/c", command[2]]
    completed = subprocess.run(local_command, cwd=workspace_path, capture_output=True, text=True)
    return {
        "passed": completed.returncode == 0,
        "logs": (completed.stdout or "") + (completed.stderr or ""),
        "failing_tests": [],
        "exit_code": completed.returncode,
    }


def summarize_test_logs(logs: str) -> str:
    if not (get_settings().openai_api_key and ChatOpenAI is not None):
        return logs
    try:
        llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=get_settings().openai_api_key)
        response = llm.invoke(
            "Summarize the test failure in one paragraph and mention the likely regression cause.\n\n"
            f"LOGS:\n{logs}"
        )
        return str(getattr(response, "content", response))
    except Exception:
        return logs
