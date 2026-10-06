from __future__ import annotations

from typing import TypedDict


class AgentState(TypedDict, total=False):
    issue_text: str
    issue_number: int
    graph_context: str
    root_cause: str
    affected_files: list[str]
    reproduction_test: str
    file_contents: dict[str, str]
    patch: str
    candidate_patches: list[dict]
    selected_patch_index: int
    debugger_result: dict
    test_analysis: dict
    test_results: dict
    retry_count: int
    pr_body: str
    human_review_required: bool
