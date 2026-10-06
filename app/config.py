from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    github_app_id: str = ""
    github_private_key: str = ""
    github_installation_id: int = 0
    github_webhook_secret: str = ""
    github_token: str = ""
    github_repository: str = ""
    openai_api_key: str = ""
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "password"
    database_url: str = "sqlite:///./nexforge.db"
    postgres_url: str = ""
    redis_url: str = ""
    auth_enabled: bool = False
    api_key: str = ""
    jwt_secret: str = ""
    allowed_repositories: str = ""
    metrics_enabled: bool = True
    security_scan_enabled: bool = True
    ci_status_required: bool = False
    docker_host: str = ""
    max_agent_retries: int = 3
    sandbox_timeout_seconds: int = 300
    sandbox_allow_local_fallback: bool = False
    sandbox_python_image: str = "nexforge/runner-python:latest"
    sandbox_node_image: str = "nexforge/runner-node:latest"
    job_stale_timeout_seconds: int = 900
    live_integration_tests: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache
def get_settings() -> Settings:
    return Settings()
