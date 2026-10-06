from __future__ import annotations

from typing import Any, Callable

from langgraph.graph import END, StateGraph

from app.agents.state import AgentState
from app.agents.coder_agent import draft_patch_candidates
from app.agents.debugger_agent import analyze_issue
from app.agents.schemas import PatchCandidate
from app.agents.tester_agent import run_tests
from app.agents.test_reasoning import analyze_test_results
from app.github_integration.pr_generator import build_pull_request_body


def build_orchestrator(
    *,
    debugger: Callable[[str, str], dict[str, object]] = analyze_issue,
    coder: Callable[[str, list[str], dict[str, str]], object] = draft_patch_candidates,
    tester: Callable[[str], dict[str, object]] = run_tests,
) -> Any:
    workflow = StateGraph(AgentState)

    def debugger_node(state: AgentState) -> AgentState:
        output = debugger(str(state.get("issue_text", "")), str(state.get("graph_context", "")))
        return {
            **state,
            "debugger_result": output,
            "root_cause": str(output.get("root_cause", "")),
            "affected_files": list(output.get("affected_files", [])),
            "reproduction_test": str(output.get("reproduction_test", "")),
        }

    def coder_node(state: AgentState) -> AgentState:
        output = coder(
            str(state.get("root_cause", "")),
            list(state.get("affected_files", [])),
            dict(state.get("file_contents", {})),
        )
        if isinstance(output, str):
            candidates = [PatchCandidate(patch=output, confidence=0.4, changed_files=list(state.get("affected_files", [])))]
        else:
            candidates = [PatchCandidate.model_validate(candidate) for candidate in (output or [])]
        candidate_data = [candidate.model_dump() for candidate in candidates]
        return {
            **state,
            "candidate_patches": candidate_data,
            "patch": candidate_data[0]["patch"] if candidate_data else "",
            "selected_patch_index": 0,
        }

    def tester_node(state: AgentState) -> AgentState:
        candidates = list(state.get("candidate_patches", [])) or [{"patch": str(state.get("patch", "")), "confidence": 0.0}]
        selected_index = int(state.get("selected_patch_index", 0))
        results: dict[str, object] = {"passed": False, "logs": "No candidate patch", "failing_tests": []}
        for index, candidate in enumerate(candidates):
            candidate_results = tester(str(candidate.get("patch", "")))
            results = {**candidate_results, "candidate_index": index}
            if candidate_results.get("passed"):
                selected_index = index
                break
        analysis = analyze_test_results(results)
        retry_count = int(state.get("retry_count", 0))
        if not results.get("passed"):
            retry_count += 1
        return {
            **state,
            "patch": str(candidates[selected_index].get("patch", "")),
            "selected_patch_index": selected_index,
            "test_results": results,
            "test_analysis": analysis,
            "retry_count": retry_count,
        }

    def decision_node(state: AgentState) -> str:
        results = state.get("test_results", {})
        if results.get("passed"):
            return "pr"
        if int(state.get("retry_count", 0)) < 3:
            return "coder"
        return "human_review"

    def pr_node(state: AgentState) -> AgentState:
        pr_body = build_pull_request_body(
            int(state.get("issue_number", 0)),
            str(state.get("root_cause", "")),
            list(state.get("affected_files", [])),
            str(state.get("test_results", {})),
        )
        return {**state, "pr_body": pr_body}

    def human_review_node(state: AgentState) -> AgentState:
        return {**state, "human_review_required": True}

    workflow.add_node("debugger", debugger_node)
    workflow.add_node("coder", coder_node)
    workflow.add_node("tester", tester_node)
    workflow.add_node("pr", pr_node)
    workflow.add_node("human_review", human_review_node)
    workflow.set_entry_point("debugger")
    workflow.add_edge("debugger", "coder")
    workflow.add_edge("coder", "tester")
    workflow.add_conditional_edges("tester", decision_node, {"coder": "coder", "pr": "pr", "human_review": "human_review"})
    workflow.add_edge("pr", END)
    workflow.add_edge("human_review", END)
    return workflow.compile()


def run_fix_cycle(issue_text: str, graph_context: str, file_contents: dict[str, str]) -> dict[str, object]:
    graph = build_orchestrator()
    state = graph.invoke(
        {
            "issue_text": issue_text,
            "graph_context": graph_context,
            "file_contents": file_contents,
            "retry_count": 0,
        }
    )
    return dict(state)
