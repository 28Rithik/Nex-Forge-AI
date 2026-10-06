from __future__ import annotations

from pydantic import BaseModel, Field


class DebuggerResult(BaseModel):
    root_cause: str
    affected_files: list[str] = Field(default_factory=list)
    affected_symbols: list[str] = Field(default_factory=list)
    reproduction_test: str = ""
    evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class PatchCandidate(BaseModel):
    patch: str
    rationale: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    changed_files: list[str] = Field(default_factory=list)


class TestAnalysis(BaseModel):
    passed: bool
    summary: str
    category: str = "unknown"
    likely_cause: str = ""
    flaky: bool = False
    failing_tests: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
