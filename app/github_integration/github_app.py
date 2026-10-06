from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from github import Github
from github.Auth import AppAuth


@dataclass
class GitHubAppClient:
    app_id: str
    private_key: str
    installation_id: int

    def client(self) -> Github:
        if not self.app_id or not self.private_key or not self.installation_id:
            raise ValueError("GitHub App credentials are incomplete")
        auth = AppAuth(self.app_id, self.private_key)
        installation_auth = auth.get_installation_auth(self.installation_id)
        return Github(auth=installation_auth)

    def repository(self, full_name: str) -> Any:
        return self.client().get_repo(full_name)
