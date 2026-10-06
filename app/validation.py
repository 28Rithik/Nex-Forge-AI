from __future__ import annotations

import re


_DIFF_PATH_RE = re.compile(r"^(?:---|\+\+\+) [ab]/(.+)$", re.MULTILINE)
_HUNK_RE = re.compile(r"^@@ .* @@", re.MULTILINE)
_BLOCKED_PATH_RE = re.compile(r"(^|/)(?:\.env(?:\..*)?|\.git/|\.github/workflows/|.*\.(?:pem|key|p12))$", re.IGNORECASE)
MAX_PATCH_BYTES = 256_000
MAX_PATCH_FILES = 12
MAX_PATCH_LINES = 2_000


def validate_unified_patch(patch: str, allowed_files: list[str]) -> dict[str, object]:
    """Validate patch shape and enforce the coder's file boundary."""
    if not patch.strip():
        return {"valid": False, "reason": "Patch is empty", "files": []}
    if len(patch.encode("utf-8")) > MAX_PATCH_BYTES:
        return {"valid": False, "reason": "Patch exceeds the 256 KB safety limit", "files": []}
    if len(patch.splitlines()) > MAX_PATCH_LINES:
        return {"valid": False, "reason": "Patch exceeds the 2,000 line safety limit", "files": []}
    if "GIT binary patch" in patch or "Binary files" in patch:
        return {"valid": False, "reason": "Binary patches are not allowed", "files": []}
    if not _HUNK_RE.search(patch):
        return {"valid": False, "reason": "Patch has no unified-diff hunk", "files": []}

    patch_files = sorted({match.replace("\\", "/") for match in _DIFF_PATH_RE.findall(patch)})
    allowed = {_normalize_path(path) for path in allowed_files}
    normalized_patch_files = {_normalize_path(path) for path in patch_files}

    if not patch_files:
        return {"valid": False, "reason": "Patch has no unified-diff file headers", "files": []}
    if len(patch_files) > MAX_PATCH_FILES:
        return {"valid": False, "reason": f"Patch changes more than {MAX_PATCH_FILES} files", "files": patch_files}

    blocked = sorted(path for path in normalized_patch_files if _BLOCKED_PATH_RE.search(path) or ".." in path.split("/"))
    if blocked:
        return {"valid": False, "reason": f"Patch targets protected paths: {', '.join(blocked)}", "files": patch_files}

    disallowed = sorted(path for path in normalized_patch_files if path not in allowed and path.split("/")[-1] not in allowed)
    if disallowed:
        return {
            "valid": False,
            "reason": f"Patch modifies files outside affected_files: {', '.join(disallowed)}",
            "files": patch_files,
        }

    return {"valid": True, "reason": "Patch is valid and within affected_files", "files": patch_files}


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
