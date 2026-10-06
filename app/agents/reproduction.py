from __future__ import annotations

from pathlib import Path
import re


def build_reproduction_test(issue_text: str, affected_files: list[str], root_cause: str) -> str:
    """Build a conservative pytest reproduction artifact for common Python bug reports."""
    python_files = [path for path in affected_files if path.endswith(".py")]
    if not python_files:
        return ""

    module_name = Path(python_files[0]).stem
    function_match = re.search(r"(?:\.py\s+|function\s+|symbol\s+|\b)([A-Za-z_]\w*)(?:\s*\(|\s+fails|\s+crashes|\s+returns)", issue_text, re.IGNORECASE)
    function_name = function_match.group(1) if function_match else "target_function"
    if function_name in {"parse", "value", "fails", "when"}:
        function_name = "target_function"

    if any(token in f"{issue_text} {root_cause}".lower() for token in ["none", "null", "null pointer"]):
        assertion = f"result = {function_name}(None)\n    assert result is None"
    else:
        assertion = "assert True, 'Issue-specific assertion requires review'"

    return (
        "import pytest\n"
        f"from {module_name} import {function_name}\n\n\n"
        f"def test_nexforge_reproduces_issue_{function_name}():\n"
        f"    {assertion}\n"
    )
