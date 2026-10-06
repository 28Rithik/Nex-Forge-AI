from __future__ import annotations

import json
import re

from app.agents.schemas import TestAnalysis
from app.config import get_settings

try:
    from langchain_openai import ChatOpenAI
except Exception:  # pragma: no cover
    ChatOpenAI = None

TestAnalysis.__test__ = False


def analyze_test_results(results: dict[str, object], baseline: dict[str, object] | None = None) -> dict[str, object]:
    logs = str(results.get("logs", ""))
    if get_settings().openai_api_key and ChatOpenAI is not None:
        try:
            llm = ChatOpenAI(model="gpt-4o-mini", temperature=0, api_key=get_settings().openai_api_key)
            response = llm.invoke(
                "Return strict JSON with passed, summary, category, likely_cause, flaky, failing_tests, confidence. "
                "Classify the test result as patch_regression, pre_existing, environment, timeout, or unknown.\n\n"
                f"BASELINE:\n{json.dumps(baseline or {})}\n\nPATCHED:\n{json.dumps(results)}"
            )
            parsed = TestAnalysis.model_validate_json(str(getattr(response, "content", response)))
            return parsed.model_dump()
        except Exception:
            pass

    failures = _extract_failing_tests(logs)
    passed = bool(results.get("passed"))
    sandbox_error = bool(results.get("sandbox_error"))
    timed_out = bool(results.get("timed_out"))
    baseline_passed = bool((baseline or {}).get("passed"))
    if timed_out:
        category, cause = "timeout", "The sandbox exceeded its wall-clock limit"
    elif sandbox_error:
        category, cause = "environment", "The sandbox runtime was unavailable or misconfigured"
    elif not passed and not baseline_passed:
        category, cause = "pre_existing", "The baseline repository already had failing tests"
    elif not passed:
        category, cause = "patch_regression", "The patched workspace introduced or preserved a failing test"
    else:
        category, cause = "passed", "No test failures detected"

    analysis = TestAnalysis(
        passed=passed,
        summary="Tests passed" if passed else f"Tests failed ({len(failures)} failing tests)",
        category=category,
        likely_cause=cause,
        flaky=_looks_flaky(logs),
        failing_tests=failures,
        confidence=0.9 if category != "unknown" else 0.35,
    )
    return analysis.model_dump()


def _extract_failing_tests(logs: str) -> list[str]:
    matches = re.findall(r"(?:FAILED|ERROR)\s+([^\s:]+(?:::[^\s:]+)?)", logs)
    return sorted(set(matches))[:50]


def _looks_flaky(logs: str) -> bool:
    lowered = logs.lower()
    return "rerun" in lowered or "flaky" in lowered or "random" in lowered
