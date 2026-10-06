from __future__ import annotations

import json
import difflib
import re
from pathlib import Path

from app.config import get_settings
from app.agents.schemas import PatchCandidate

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional dependency path
    ChatOpenAI = None


def _llm_enabled() -> bool:
    settings = get_settings()
    return bool(settings.openai_api_key and ChatOpenAI is not None)


def draft_patch(root_cause: str, affected_files: list[str], file_contents: dict[str, str]) -> str:
    candidates = draft_patch_candidates(root_cause, affected_files, file_contents)
    return candidates[0].patch if candidates else ""


def draft_patch_candidates(root_cause: str, affected_files: list[str], file_contents: dict[str, str]) -> list[PatchCandidate]:
    if _llm_enabled() and affected_files:
        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=get_settings().openai_api_key)
            prompt = (
                "You are a code-fixing agent. Return strict JSON with a candidates list. Each candidate must contain patch, rationale, confidence, changed_files. "
                "Only modify files in affected_files. Keep the patch minimal and valid.\n\n"
                f"ROOT_CAUSE:\n{root_cause}\n\nAFFECTED_FILES:\n{affected_files}\n\nFILE_CONTENTS:\n{json.dumps(file_contents, indent=2)}"
            )
            response = llm.invoke(prompt)
            data = json.loads(getattr(response, "content", str(response)))
            candidates = [PatchCandidate.model_validate(candidate) for candidate in data.get("candidates", [])]
            if candidates:
                return candidates
        except Exception:
            pass

    if not affected_files:
        return []

    candidates: list[PatchCandidate] = []
    for file_path in affected_files:
        original = file_contents.get(file_path, "")
        updated = _apply_heuristic_fix(file_path, original, root_cause)
        display_path = _display_path(file_path)
        diff = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{display_path}",
                tofile=f"b/{display_path}",
                lineterm="",
            )
        )
        if diff:
            candidates.append(
                PatchCandidate(
                    patch=diff + "\n",
                    rationale="Deterministic minimal fallback for the identified root cause",
                    confidence=0.55,
                    changed_files=[display_path],
                )
            )
    return candidates


def _display_path(file_path: str) -> str:
    path = Path(file_path)
    if path.is_absolute():
        return path.name
    return file_path.replace("\\", "/")


def _apply_heuristic_fix(file_path: str, original: str, root_cause: str) -> str:
    if not original:
        return original

    if any(token in root_cause.lower() for token in ["null", "none", "guard"]):
        if file_path.endswith(".py"):
            return _inject_python_none_guard(original)
        if file_path.endswith((".js", ".jsx", ".ts", ".tsx")):
            return _inject_js_null_guard(original)

    return original


def _inject_python_none_guard(source: str) -> str:
    lines = source.splitlines()
    for index, line in enumerate(lines):
        if re.match(r"\s*def\s+", line):
            indent = line[: len(line) - len(line.lstrip())] + "    "
            return "\n".join(
                lines[: index + 1]
                + [f"{indent}if value is None:", f"{indent}    return None"]
                + lines[index + 1 :]
            )
    return source


def _inject_js_null_guard(source: str) -> str:
    if "if (value == null)" in source:
        return source
    brace_index = source.find("{")
    if brace_index == -1:
        return source
    return source[: brace_index + 1] + "\n  if (value == null) return null;" + source[brace_index + 1 :]
