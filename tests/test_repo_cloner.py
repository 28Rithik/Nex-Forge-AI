from __future__ import annotations

from pathlib import Path
import subprocess

from app.cloning.repo_cloner import RepoCloner


def test_repo_cloner_clones_and_creates_working_branch(tmp_path: Path, monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run(command, check, text, capture_output=False):
        commands.append(command)
        if command[:4] == ["git", "ls-remote", "--symref", "https://example.com/repo.git"]:
            return subprocess.CompletedProcess(command, 0, stdout="ref: refs/heads/main\tHEAD\n", stderr="")
        if command[:2] == ["git", "clone"]:
            workspace_path = Path(command[-1])
            workspace_path.mkdir(parents=True, exist_ok=True)
            (workspace_path / ".git").mkdir()
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("app.cloning.repo_cloner.subprocess.run", fake_run)

    cloner = RepoCloner(tmp_path)
    result = cloner.clone("https://example.com/repo.git", "job-123", issue_number=7)

    assert result.workspace_path == tmp_path / "job-123"
    assert result.default_branch == "main"
    assert result.branch_name == "ai-fix/issue-7"
    assert commands[0][:4] == ["git", "ls-remote", "--symref", "https://example.com/repo.git"]
    assert ["git", "clone", "--depth=1", "https://example.com/repo.git", str(tmp_path / "job-123")] in commands
    assert ["git", "-C", str(tmp_path / "job-123"), "checkout", "main"] in commands
    assert ["git", "-C", str(tmp_path / "job-123"), "checkout", "-b", "ai-fix/issue-7"] in commands


def test_repo_cloner_cleanup_removes_workspace(tmp_path: Path) -> None:
    cloner = RepoCloner(tmp_path)
    workspace = tmp_path / "job-456"
    workspace.mkdir()

    cloner.cleanup("job-456")

    assert not workspace.exists()