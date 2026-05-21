"""Response-object builders matching GitHub's REST shapes."""
from __future__ import annotations

import base64
from datetime import datetime, timezone


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _node_id(kind: str, num: int) -> str:
    return base64.b64encode(f"010:{kind}{num}".encode()).decode()


def app_dto(app: dict) -> dict:
    return {
        "id": app["app_id"],
        "slug": app["slug"],
        "node_id": _node_id("Integration", app["app_id"]),
        "name": app["name"],
        "client_id": app["client_id"],
        "owner": {"login": app["slug"], "type": "Organization"},
        "description": "",
        "external_url": "",
        "html_url": f"https://github.com/apps/{app['slug']}",
        "permissions": app["permissions"] if isinstance(app["permissions"], dict) else {},
        "events": app["events"] if isinstance(app["events"], list) else [],
    }


def installation_dto(inst: dict, app_id: int) -> dict:
    return {
        "id": inst["installation_id"],
        "account": {
            "login": inst["account_login"],
            "id": inst["account_id"],
            "type": inst["account_type"],
        },
        "repository_selection": inst["repository_selection"],
        "app_id": app_id,
        "target_id": inst["account_id"],
        "target_type": inst["account_type"],
        "permissions": {"contents": "read", "metadata": "read", "pull_requests": "read", "issues": "read"},
        "events": ["push", "pull_request", "issues", "issue_comment", "pull_request_review", "check_run"],
        "created_at": iso(inst.get("created_at")),
    }


def repo_dto(repo: dict) -> dict:
    full_name = f"{repo['owner']}/{repo['name']}"
    return {
        "id": repo["repo_id"],
        "node_id": _node_id("Repository", repo["repo_id"]),
        "name": repo["name"],
        "full_name": full_name,
        "private": repo["private"],
        "owner": {"login": repo["owner"], "type": "Organization"},
        "html_url": f"https://github.com/{full_name}",
        "description": repo.get("description"),
        "fork": False,
        "url": f"https://api.github.com/repos/{full_name}",
        "default_branch": repo["default_branch"],
        "created_at": iso(repo.get("created_at")),
        "updated_at": iso(repo.get("created_at")),
        "pushed_at": iso(repo.get("created_at")),
    }
