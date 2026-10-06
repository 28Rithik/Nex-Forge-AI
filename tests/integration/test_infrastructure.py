from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.graph.neo4j_client import Neo4jClient
from app.sandbox.docker_runner import DockerSandboxRunner


pytestmark = pytest.mark.integration


def _enabled() -> bool:
    return os.getenv("NEXFORGE_RUN_INTEGRATION", "false").lower() == "true"


@pytest.mark.skipif(not _enabled(), reason="Set NEXFORGE_RUN_INTEGRATION=true to run service integration tests")
def test_neo4j_schema_is_available() -> None:
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USER", "neo4j")
    password = os.getenv("NEO4J_PASSWORD", "password")
    client = Neo4jClient(uri, user, password)
    try:
        assert client.verify_connectivity() is True
        client.ensure_schema()
        assert client.health()["connected"] is True
    finally:
        client.close()


@pytest.mark.skipif(not _enabled(), reason="Set NEXFORGE_RUN_INTEGRATION=true to run service integration tests")
def test_docker_python_runner_executes_workspace(tmp_path: Path) -> None:
    (tmp_path / "test_smoke.py").write_text("def test_smoke():\n    assert 1 + 1 == 2\n", encoding="utf-8")
    runner = DockerSandboxRunner()
    result = runner.run(
        image=os.getenv("SANDBOX_PYTHON_IMAGE", "nexforge/runner-python:latest"),
        command=["pytest", "-q"],
        workspace_path=tmp_path,
        timeout_seconds=60,
    )
    assert result.passed, result.stderr or result.stdout
