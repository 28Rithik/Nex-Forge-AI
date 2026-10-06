from app.github_integration.pr_generator import build_pull_request_body


def test_build_pull_request_body() -> None:
    body = build_pull_request_body(12, "null check missing", ["app/main.py"], "all tests passed")
    assert "Closes #12" in body
    assert "app/main.py" in body
