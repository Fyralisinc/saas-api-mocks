"""Per-provider error-body factories. Real shapes, not generic 500s."""
from __future__ import annotations

from typing import Any


def slack_error(code: str, **extra: Any) -> dict[str, Any]:
    """Slack returns ``{"ok": false, "error": "..."}``."""
    body: dict[str, Any] = {"ok": False, "error": code}
    body.update(extra)
    return body


def github_error(message: str, *, documentation_url: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"message": message}
    if documentation_url:
        body["documentation_url"] = documentation_url
    return body


def discord_error(code: int, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"code": code, "message": message}
    body.update(extra)
    return body


def gmail_error(code: int, message: str, *, reason: str | None = None) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    if reason:
        errors.append({"reason": reason, "message": message, "domain": "global"})
    return {
        "error": {
            "code": code,
            "message": message,
            "errors": errors,
            "status": _GOOGLE_STATUS.get(code, "INTERNAL"),
        },
    }


def notion_error(status: int, code: str, message: str) -> dict[str, Any]:
    """Notion returns ``{"object":"error","status":…,"code":"…","message":"…"}``."""
    return {"object": "error", "status": status, "code": code, "message": message}


def google_error(
    code: int,
    message: str,
    *,
    reason: str | None = None,
    domain: str = "global",
    location: str | None = None,
    location_type: str | None = None,
) -> dict[str, Any]:
    """Google's standard error envelope (Calendar / Gmail / Directory share it)."""
    entry: dict[str, Any] = {"domain": domain, "message": message}
    if reason:
        entry["reason"] = reason
    if location_type:
        entry["locationType"] = location_type
    if location:
        entry["location"] = location
    return {
        "error": {
            "errors": [entry] if reason else [],
            "code": code,
            "message": message,
            "status": _GOOGLE_STATUS.get(code, "INTERNAL"),
        },
    }


_GOOGLE_STATUS = {
    400: "INVALID_ARGUMENT",
    401: "UNAUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    429: "RESOURCE_EXHAUSTED",
    500: "INTERNAL",
    503: "UNAVAILABLE",
}
