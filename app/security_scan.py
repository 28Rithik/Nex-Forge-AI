from __future__ import annotations

from pathlib import Path
import subprocess
from typing import Any


BLOCKED_SCAN_FILES = {".env"}


def run_security_scans(workspace_path: Path, enabled: bool = True) -> dict[str, Any]:
    if not enabled:
        return {"passed": True, "skipped": True, "findings": [], "logs": "Security scanning disabled"}

    findings: list[str] = []
    logs: list[str] = []
    for name, command in [
        ("ruff", ["ruff", "check", "."]),
        ("bandit", ["bandit", "-q", "-r", "."]),
    ]:
        try:
            result = subprocess.run(command, cwd=workspace_path, capture_output=True, text=True, timeout=120)
            output = (result.stdout or "") + (result.stderr or "")
            logs.append(f"[{name}] exit={result.returncode}\n{output}")
            if result.returncode != 0:
                findings.append(f"{name}: security/static scan failed")
        except FileNotFoundError:
            logs.append(f"[{name}] scanner not installed; skipped")
        except subprocess.TimeoutExpired:
            findings.append(f"{name}: scanner timed out")
            logs.append(f"[{name}] timed out")

    for path in workspace_path.rglob("*"):
        if path.is_file() and path.name in BLOCKED_SCAN_FILES:
            findings.append(f"protected file present: {path.relative_to(workspace_path)}")

    return {"passed": not findings, "skipped": False, "findings": findings, "logs": "\n".join(logs)}
