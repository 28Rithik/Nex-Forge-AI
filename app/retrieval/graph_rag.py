from __future__ import annotations

import re
from typing import Any


class GraphRAGRetriever:
    def __init__(self, client: object | None = None, hop_count: int = 2, context_limit: int = 10) -> None:
        self.client = client
        self.hop_count = hop_count
        self.context_limit = context_limit

    def extract_candidates(self, issue_text: str) -> list[str]:
        candidates: set[str] = set()
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_\.]+", issue_text):
            if token.lower() in {"error", "exception", "traceback", "issue", "fix", "failed"}:
                continue
            candidates.add(token.rstrip(".:,;"))
        return sorted(candidates)

    def retrieve(self, issue_text: str) -> str:
        candidates = self.extract_candidates(issue_text)
        rows: list[dict[str, Any]] = []
        if self.client is not None and candidates:
            rows = self._query_subgraph(candidates)
        ranked_candidates = self._rank_candidates(issue_text, candidates, rows)
        return self._format_context(issue_text, ranked_candidates, rows)

    def _query_subgraph(self, candidates: list[str]) -> list[dict[str, Any]]:
        cypher = """
        MATCH (start)
        WHERE start.name IN $candidates OR start.path IN $candidates OR start.module IN $candidates
        OPTIONAL MATCH p=(start)-[*0..$hops]-(neighbor)
        RETURN start, neighbor
        LIMIT $limit
        """
        try:
            if hasattr(self.client, "execute_query"):
                result = self.client.execute_query(cypher, candidates=candidates, hops=self.hop_count, limit=self.context_limit)
                return list(result) if result is not None else []
            if hasattr(self.client, "session"):
                with self.client.session() as session:
                    result = session.run(cypher, candidates=candidates, hops=self.hop_count, limit=self.context_limit)
                    return list(result)
        except Exception:
            return []
        return []

    def _format_context(self, issue_text: str, candidates: list[str], rows: list[dict[str, Any]]) -> str:
        lines = ["Issue:", issue_text.strip(), "", "Candidate symbols:"]
        lines.extend(f"- {candidate}" for candidate in candidates[: self.context_limit])
        if rows:
            lines.append("")
            lines.append("Graph matches:")
            for row in rows[: self.context_limit]:
                lines.append(f"- {row}")
        return "\n".join(lines)

    def _rank_candidates(self, issue_text: str, candidates: list[str], rows: list[dict[str, Any]]) -> list[str]:
        lowered_issue = issue_text.lower()
        generic_terms = {"fails", "failure", "when", "with", "returns", "crashes", "needs", "error"}
        scores: dict[str, float] = {}
        for candidate in candidates:
            score = 1.0
            if candidate.lower() in generic_terms:
                score -= 3.0
            if candidate.lower() in lowered_issue:
                score += 3.0
            if "." in candidate:
                score += 0.5
            else:
                score += 1.5
            scores[candidate] = score

        for row in rows:
            text = str(row).lower()
            for candidate in candidates:
                if candidate.lower() in text:
                    scores[candidate] += 2.0
        return sorted(candidates, key=lambda candidate: (-scores[candidate], candidate))[: self.context_limit]
