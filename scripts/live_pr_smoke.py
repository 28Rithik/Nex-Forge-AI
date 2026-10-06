from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile

from app.github_integration.pr_generator import GitHubPRGenerator


"""Explicit live PR smoke test.

Required environment variables:
  GITHUB_TOKEN or GitHub App credentials
  GITHUB_REPOSITORY=owner/repository
  GITHUB_BASE_BRANCH=main

This script creates a branch and PR in the configured repository. Run it only against
an expendable test repository and pass --confirm-live-pr.
"""


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Create a real NexForge PR in a disposable repository")
    parser.add_argument("--confirm-live-pr", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live_pr:
        raise SystemExit("Refusing to create a live PR without --confirm-live-pr")

    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.getenv("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required for Git push authentication")
    branch = os.getenv("GITHUB_SMOKE_BRANCH", "nexforge/live-smoke")
    base = os.getenv("GITHUB_BASE_BRANCH", "main")
    title = os.getenv("GITHUB_SMOKE_TITLE", "NexForge live smoke test")
    repo_url = f"https://github.com/{repository}.git"

    with tempfile.TemporaryDirectory(prefix="nexforge-pr-smoke-") as temp_dir:
        temp_root = Path(temp_dir)
        workspace = temp_root / "repository"
        subprocess.run(["git", "clone", "--depth=1", repo_url, str(workspace)], cwd=temp_root, check=True, text=True)
        subprocess.run(["git", "checkout", "-b", branch], cwd=workspace, check=True, text=True)
        (workspace / "nexforge_live_smoke.txt").write_text("created by NexForge live smoke test\n", encoding="utf-8")
        askpass = temp_root / ("askpass.cmd" if os.name == "nt" else "askpass.sh")
        if os.name == "nt":
            askpass.write_text("@echo off\necho %GITHUB_TOKEN%\n", encoding="utf-8")
        else:
            askpass.write_text("#!/bin/sh\nprintf '%s\\n' \"$GITHUB_TOKEN\"\n", encoding="utf-8")
            askpass.chmod(0o700)
        env = os.environ.copy()
        env.update({"GIT_ASKPASS": str(askpass), "GIT_TERMINAL_PROMPT": "0"})
        subprocess.run(["git", "add", "-A"], cwd=workspace, check=True, env=env)
        subprocess.run(
            ["git", "-c", "user.name=NexForge AI", "-c", "user.email=nexforge@example.invalid", "commit", "-m", title],
            cwd=workspace,
            check=True,
            env=env,
        )
        subprocess.run(["git", "push", "-u", "origin", branch], cwd=workspace, check=True, env=env)

        generator = GitHubPRGenerator(token=token)
        result = generator.open_pull_request(
            repo_full_name=repository,
            title=title,
            body="NexForge AI live integration smoke test.",
            head=branch,
            base=base,
            labels=["ai-generated"],
        )
        print(f"Created PR #{result.number}: {result.url}")


if __name__ == "__main__":
    main()
