from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import docker


@dataclass
class SandboxResult:
    passed: bool
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False


class DockerSandboxRunner:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client or docker.from_env()

    def run(
        self,
        *,
        image: str,
        command: list[str],
        workspace_path: Path,
        workdir: str = "/workspace",
        timeout_seconds: int = 300,
    ) -> SandboxResult:
        container = self.client.containers.run(
            image=image,
            command=command,
            working_dir=workdir,
            volumes={str(workspace_path): {"bind": workdir, "mode": "rw"}},
            network_disabled=True,
            detach=True,
            remove=True,
            user="1000:1000",
            mem_limit="1g",
            cpu_period=100000,
            cpu_quota=100000,
            stdin_open=False,
            tty=False,
        )
        timed_out = False
        try:
            result = container.wait(timeout=timeout_seconds)
        except Exception:
            timed_out = True
            container.kill()
            result = {"StatusCode": 1}
        stdout_logs = container.logs(stdout=True, stderr=False)
        stderr_logs = container.logs(stdout=False, stderr=True)
        stdout = stdout_logs.decode("utf-8", errors="ignore") if isinstance(stdout_logs, (bytes, bytearray)) else str(stdout_logs)
        stderr = stderr_logs.decode("utf-8", errors="ignore") if isinstance(stderr_logs, (bytes, bytearray)) else str(stderr_logs)
        exit_code = int(result.get("StatusCode", 1))
        if timed_out:
            stderr = f"Sandbox timed out after {timeout_seconds} seconds.\n{stderr}".strip()
        return SandboxResult(passed=exit_code == 0 and not timed_out, stdout=stdout, stderr=stderr, exit_code=exit_code, timed_out=timed_out)

def run_in_sandbox(command: list[str], timeout_seconds: int) -> dict[str, object]:
    runner = DockerSandboxRunner()
    result = runner.run(image="python:3.11-slim", command=command, workspace_path=Path.cwd(), timeout_seconds=timeout_seconds)
    return {"passed": result.passed, "stdout": result.stdout, "stderr": result.stderr, "exit_code": result.exit_code}
