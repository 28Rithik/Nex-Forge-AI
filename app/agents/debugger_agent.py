from __future__ import annotations

import json
import re

from app.config import get_settings
from app.agents.reproduction import build_reproduction_test
from app.agents.schemas import DebuggerResult

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover - optional dependency path
    ChatOpenAI = None


def _llm_enabled() -> bool:
    settings = get_settings()
    return bool(settings.openai_api_key and ChatOpenAI is not None)


def analyze_issue(issue_text: str, graph_context: str) -> dict[str, object]:
    if _llm_enabled():
        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=get_settings().openai_api_key)
            prompt = (
                "You are a debugger agent for code fixes. Return strict JSON with keys root_cause, affected_files, reproduction_test. "
                "Use only the provided context and keep affected_files as a list of file paths.\n\n"
                f"ISSUE:\n{issue_text}\n\nGRAPH_CONTEXT:\n{graph_context}"
            )
            response = llm.invoke(prompt)
            content = getattr(response, "content", str(response))
            data = json.loads(content)
            if isinstance(data, dict):
                result = DebuggerResult(
                    root_cause=str(data.get("root_cause", "LLM analysis")),
                    affected_files=list(data.get("affected_files", [])),
                    reproduction_test=str(data.get("reproduction_test", "")),
                    affected_symbols=list(data.get("affected_symbols", [])),
                    evidence=list(data.get("evidence", [])),
                    confidence=float(data.get("confidence", 0.6)),
                )
                return result.model_dump()
        except Exception:
            pass

    issue_lower = issue_text.lower()
    root_cause = "Unknown failure mode"
    reproduction_test = ""

    if any(token in issue_lower for token in ["null", "none", "attributeerror", "null pointer"]):
        root_cause = "Missing null/None guard in the failing code path"
        reproduction_test = ""
    elif any(token in issue_lower for token in ["import", "module not found", "cannot import"]):
        root_cause = "Broken or missing import dependency"
        reproduction_test = ""
    elif any(token in issue_lower for token in ["test", "assert", "failing"]):
        root_cause = "Behavior regressed against an existing test expectation"

    affected_files = _extract_file_paths(issue_text, graph_context)
    if not affected_files and "app/" in graph_context:
        affected_files = _extract_file_paths(graph_context, graph_context)

    reproduction_test = build_reproduction_test(issue_text, affected_files, root_cause)
    result = DebuggerResult(
        root_cause=root_cause,
        affected_files=affected_files,
        affected_symbols=_extract_symbols(issue_text),
        reproduction_test=reproduction_test,
        evidence=[line.strip() for line in graph_context.splitlines() if line.strip()][-5:],
        confidence=0.82 if root_cause != "Unknown failure mode" and affected_files else 0.28,
    )
    return result.model_dump()


def _extract_file_paths(*texts: str) -> list[str]:
    files: list[str] = []
    for text in texts:
        for match in re.findall(r"[A-Za-z0-9_./\\-]+\.(?:py|js|ts|tsx|jsx)", text):
            normalized = match.replace("\\", "/")
            if normalized not in files:
                files.append(normalized)
    return files


def _extract_symbols(issue_text: str) -> list[str]:
    return sorted(set(re.findall(r"\b[A-Za-z_]\w*\s*(?=\()", issue_text)))
