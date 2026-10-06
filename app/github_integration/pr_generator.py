from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from github import Github

from app.github_integration.github_app import GitHubAppClient


def build_pull_request_body(issue_number: int, root_cause: str, files_changed: list[str], test_summary: str) -> str:
    files = "\n".join(f"- {path}" for path in files_changed) if files_changed else "- None"
    return (
        f"Root cause: {root_cause}\n\n"
        f"Files changed:\n{files}\n\n"
        f"Test results: {test_summary}\n\n"
        f"Closes #{issue_number}"
    )


@dataclass
class PullRequestResult:
    url: str
    number: int


class GitHubPRGenerator:
    def __init__(
        self,
        token: str | None = None,
        github_client: Any | None = None,
        app_id: str = "",
        private_key: str = "",
        installation_id: int = 0,
    ) -> None:
        self.token = token
        self.github_client = github_client
        if self.github_client is None and app_id and private_key and installation_id:
            self.github_client = GitHubAppClient(app_id, private_key, installation_id).client()
        if self.github_client is None and token:
            self.github_client = Github(token)

    def commit_patch(self, workspace_path: Path, branch_name: str, commit_message: str) -> None:
        commands = [
            ["git", "-C", str(workspace_path), "add", "-A"],
            ["git", "-C", str(workspace_path), "commit", "-m", commit_message],
            ["git", "-C", str(workspace_path), "push", "-u", "origin", branch_name],
        ]
        for command in commands:
            subprocess.run(command, check=True, text=True)

    def open_pull_request(
        self,
        *,
        repo_full_name: str,
        title: str,
        body: str,
        head: str,
        base: str = "main",
        labels: list[str] | None = None,
    ) -> PullRequestResult:
        if self.github_client is None:
            raise ValueError("GitHub client not configured")
        repo = self.github_client.get_repo(repo_full_name)
        pull_request = repo.create_pull(title=title, body=body, head=head, base=base)
        if labels:
            pull_request.set_labels(*labels)
        return PullRequestResult(url=pull_request.html_url, number=pull_request.number)


def build_pull_request_payload(
    issue_number: int,
    issue_title: str,
    root_cause: str,
    files_changed: list[str],
    test_summary: str,
) -> dict[str, object]:
    return {
        "title": f"Fix: {issue_title}",
        "body": build_pull_request_body(issue_number, root_cause, files_changed, test_summary),
        "labels": ["ai-generated"],
    }
