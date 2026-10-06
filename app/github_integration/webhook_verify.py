from __future__ import annotations

import hmac
import hashlib


def verify_github_webhook_signature(payload: bytes, signature_header: str, secret: str) -> bool:
    if not secret or not signature_header.startswith("sha256="):
        return False
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    expected = f"sha256={digest}"
    return hmac.compare_digest(expected, signature_header)
