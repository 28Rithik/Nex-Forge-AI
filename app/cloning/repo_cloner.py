from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import stat
import subprocess
import shutil

from app.cloning.repo_validation import is_valid_repository_reference


@dataclass
class CloneResult:
    workspace_path: Path
    branch_name: str
    default_branch: str


class RepoCloner:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir

    def clone(self, repo_url: str, job_id: str, issue_number: int | None = None) -> CloneResult:
        if not is_valid_repository_reference(repo_url):
            raise ValueError("Invalid repository reference; expected a repository URL or existing local path")
        workspace_path = self.base_dir / job_id
        workspace_path.mkdir(parents=True, exist_ok=True)
        default_branch = self._detect_default_branch(repo_url)

        self._run(["git", "clone", "--depth=1", repo_url, str(workspace_path)])
        if default_branch:
            self._run(["git", "-C", str(workspace_path), "checkout", default_branch])

        branch_name = f"ai-fix/issue-{issue_number or 'manual'}"
        self._run(["git", "-C", str(workspace_path), "checkout", "-b", branch_name])
        return CloneResult(workspace_path=workspace_path, branch_name=branch_name, default_branch=default_branch)

    def cleanup(self, job_id: str) -> None:
        workspace_path = self.base_dir / job_id
        if workspace_path.exists():
            shutil.rmtree(workspace_path, onerror=self._remove_readonly)

    def _remove_readonly(self, function, path, excinfo) -> None:
        del excinfo
        try:
            os.chmod(path, stat.S_IWRITE)
        except OSError:
            pass
        function(path)

    def _detect_default_branch(self, repo_url: str) -> str:
        result = self._run(["git", "ls-remote", "--symref", repo_url, "HEAD"], capture_output=True)
        stdout = result.stdout or ""
        for line in stdout.splitlines():
            if line.startswith("ref:") and "refs/heads/" in line:
                return line.split("refs/heads/", maxsplit=1)[1].split("\t", maxsplit=1)[0].strip()
        return ""

    def _run(self, command: list[str], capture_output: bool = False) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            command,
            check=True,
            text=True,
            capture_output=capture_output,
        )
