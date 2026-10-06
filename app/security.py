from __future__ import annotations

from dataclasses import dataclass
import hmac

from fastapi import Depends, Header, HTTPException, Request

from app.config import get_settings


@dataclass
class Principal:
    subject: str
    role: str = "operator"


def allowed_repository(full_name: str) -> bool:
    configured = [item.strip().lower() for item in get_settings().allowed_repositories.split(",") if item.strip()]
    return not configured or full_name.strip().lower() in configured


def require_auth(request: Request, x_api_key: str | None = Header(default=None)) -> Principal:
    settings = get_settings()
    if not settings.auth_enabled:
        return Principal(subject="development", role="admin")
    if settings.api_key and x_api_key and hmac.compare_digest(settings.api_key, x_api_key):
        return Principal(subject="api-key", role="admin")
    raise HTTPException(status_code=401, detail="Authentication required")


def require_admin(principal: Principal = Depends(require_auth)) -> Principal:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator permission required")
    return principal
