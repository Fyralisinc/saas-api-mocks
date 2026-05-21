"""Response-object builders matching GitHub's REST shapes."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from uuid import UUID


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _node_id(kind: str, num: int) -> str:
    return base64.b64encode(f"010:{kind}{num}".encode()).decode()


def _num_id(pk: UUID) -> int:
    """A stable numeric id for objects whose schema only has a UUID pk."""
    return pk.int % 9_000_000_000 + 1_000_000_000


def _user(login: str) -> dict:
    return {"login": login, "type": "User", "site_admin": False}


def _jsonb(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else json.loads(value)


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


def pull_request_dto(pr: dict, full_name: str) -> dict:
    return {
        "id": _num_id(pr["id"]),
        "node_id": _node_id("PullRequest", pr["number"]),
        "number": pr["number"],
        "state": pr["state"],
        "title": pr["title"],
        "body": pr["body"],
        "draft": False,
        "user": _user(pr["user_login"]),
        "head": {"ref": pr["head_ref"], "sha": pr["head_sha"]},
        "base": {"ref": pr["base_ref"], "sha": pr["base_sha"]},
        "merged": pr["merged"],
        "merged_at": iso(pr.get("merged_at")),
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": pr["changed_files"],
        "labels": [{"name": n} for n in _jsonb(pr.get("labels"))],
        "requested_reviewers": [_user(login) for login in _jsonb(pr.get("requested_reviewers"))],
        "html_url": f"https://github.com/{full_name}/pull/{pr['number']}",
        "created_at": iso(pr.get("created_at")),
        "updated_at": iso(pr.get("updated_at")),
        "closed_at": iso(pr.get("closed_at")),
    }


def issue_dto(issue: dict, full_name: str, comments: int = 0) -> dict:
    return {
        "id": _num_id(issue["id"]),
        "node_id": _node_id("Issue", issue["number"]),
        "number": issue["number"],
        "state": issue["state"],
        "title": issue["title"],
        "body": issue["body"],
        "user": _user(issue["user_login"]),
        "assignees": [_user(login) for login in _jsonb(issue.get("assignees"))],
        "labels": [{"name": n} for n in _jsonb(issue.get("labels"))],
        "comments": comments,
        "html_url": f"https://github.com/{full_name}/issues/{issue['number']}",
        "created_at": iso(issue.get("created_at")),
        "updated_at": iso(issue.get("updated_at")),
        "closed_at": iso(issue.get("closed_at")),
    }


def commit_dto(c: dict, full_name: str) -> dict:
    return {
        "sha": c["sha"],
        "node_id": _node_id("Commit", _num_id(c["id"])),
        "html_url": f"https://github.com/{full_name}/commit/{c['sha']}",
        "commit": {
            "message": c["message"],
            "author": {"name": c["author_login"], "email": c["author_email"], "date": iso(c.get("committed_at"))},
            "committer": {"name": c["author_login"], "email": c["author_email"], "date": iso(c.get("committed_at"))},
        },
        "author": _user(c["author_login"]),
        "committer": _user(c["author_login"]),
        "parents": [{"sha": sha} for sha in _jsonb(c.get("parents"))],
        "stats": {"additions": c["additions"], "deletions": c["deletions"], "total": c["additions"] + c["deletions"]},
    }


def review_dto(r: dict) -> dict:
    return {
        "id": _num_id(r["id"]),
        "user": _user(r["user_login"]),
        "body": r["body"],
        "state": r["state"].upper(),       # GitHub returns APPROVED / CHANGES_REQUESTED / ...
        "submitted_at": iso(r.get("submitted_at")),
    }


def issue_comment_dto(c: dict, full_name: str) -> dict:
    return {
        "id": _num_id(c["id"]),
        "user": _user(c["user_login"]),
        "body": c["body"],
        "html_url": f"https://github.com/{full_name}/issues/{c['issue_number']}#issuecomment-{_num_id(c['id'])}",
        "created_at": iso(c.get("created_at")),
        "updated_at": iso(c.get("created_at")),
    }


def check_run_dto(cr: dict) -> dict:
    return {
        "id": _num_id(cr["id"]),
        "name": cr["name"],
        "head_sha": cr["head_sha"],
        "status": cr["status"],
        "conclusion": cr.get("conclusion"),
        "started_at": iso(cr.get("started_at")),
        "completed_at": iso(cr.get("completed_at")),
    }
