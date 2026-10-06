from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def is_valid_repository_reference(value: str) -> bool:
    reference = value.strip()
    if not reference or len(reference) > 2048 or any(char.isspace() for char in reference):
        return False

    parsed = urlparse(reference)
    if parsed.scheme in {"http", "https"}:
        return bool(parsed.netloc and any(part for part in parsed.path.split("/") if part))
    if parsed.scheme == "file":
        return Path(parsed.path).exists()
    return Path(reference).exists()


def repository_validation_message(value: str) -> str:
    if len(value.strip()) > 2048:
        return "Repository reference is too long. Paste only a repository URL."
    return "Enter a valid repository URL such as https://github.com/owner/repository."


def repository_full_name(value: str) -> str:
    parsed = urlparse(value.strip())
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return ""