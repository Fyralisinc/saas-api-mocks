"""Jira Cloud v3 resource shapes: issue (+inline comment & changelog), project,
user, ADF. Field names mirror the REST v3 reference; the consumer reads specific
keys (fields.status.name, changelog.histories[].items[].toString, …).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

# Jira status name -> statusCategory key.
_STATUS_CATEGORY = {
    "To Do": "new", "Open": "new", "Backlog": "new",
    "In Progress": "indeterminate", "In Review": "indeterminate",
    "Done": "done", "Closed": "done", "Resolved": "done",
}


def jira_ts(dt: Optional[datetime]) -> Optional[str]:
    """Jira's timestamp: millis + offset WITHOUT a colon, e.g. ...+0000."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}+0000"


def adf(text: str) -> dict[str, Any]:
    """Wrap plain text in a minimal Atlassian Document Format doc."""
    return {
        "type": "doc", "version": 1,
        "content": [{"type": "paragraph",
                     "content": [{"type": "text", "text": text}]}],
    }


def _jsonb(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except Exception:
            return v
    return v


def user_dto(account_id: Optional[str], users: dict[str, dict]) -> Optional[dict[str, Any]]:
    if not account_id:
        return None
    u = users.get(account_id)
    base = f"https://mock/rest/api/3/user?accountId={account_id}"
    return {
        "self": base,
        "accountId": account_id,
        "accountType": "atlassian",
        "emailAddress": (u or {}).get("email"),
        "displayName": (u or {}).get("display_name") or account_id,
        "active": True,
        "timeZone": "Etc/UTC",
    }


def _status_dto(name: str, category: str) -> dict[str, Any]:
    cat_id = {"new": 2, "indeterminate": 4, "done": 3}.get(category, 2)
    cat_key = {"new": "new", "indeterminate": "indeterminate", "done": "done"}.get(category, "new")
    return {
        "name": name,
        "id": str(abs(hash(name)) % 9000 + 1000),
        "statusCategory": {"id": cat_id, "key": cat_key,
                           "name": {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}.get(cat_key, "To Do")},
    }


def comment_dto(c: dict[str, Any], base_url: str, users: dict[str, dict]) -> dict[str, Any]:
    return {
        "self": f"{base_url}/rest/api/3/issue/comment/{c['comment_id']}",
        "id": c["comment_id"],
        "author": user_dto(c.get("author_account_id"), users),
        "updateAuthor": user_dto(c.get("author_account_id"), users),
        "body": _jsonb(c.get("body")) or adf(""),
        "created": jira_ts(c["created_at"]),
        "updated": jira_ts(c["updated_at"]),
    }


def history_dto(h: dict[str, Any], users: dict[str, dict]) -> dict[str, Any]:
    return {
        "id": h["history_id"],
        "author": user_dto(h.get("author_account_id"), users),
        "created": jira_ts(h["created_at"]),
        "items": _jsonb(h.get("items")) or [],
    }


def project_dto(p: dict[str, Any], base_url: str) -> dict[str, Any]:
    return {
        "self": f"{base_url}/rest/api/3/project/{p['project_id']}",
        "id": p["project_id"],
        "key": p["key"],
        "name": p["name"],
        "projectTypeKey": p.get("project_type_key") or "software",
        "simplified": False,
        "style": "classic",
        "isPrivate": False,
    }


def issue_dto(
    issue: dict[str, Any],
    *,
    base_url: str,
    users: dict[str, dict],
    project: dict[str, Any],
    comments: list[dict[str, Any]],
    histories: list[dict[str, Any]],
    include_comment: bool,
    include_changelog: bool,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": issue["summary"],
        "description": _jsonb(issue.get("description")),
        "issuetype": {"name": issue.get("issue_type") or "Task",
                      "id": str(abs(hash(issue.get("issue_type") or "Task")) % 9000 + 1000),
                      "subtask": False},
        "status": _status_dto(issue.get("status") or "To Do", issue.get("status_category") or "new"),
        "priority": {"name": issue.get("priority") or "Medium",
                     "id": str(abs(hash(issue.get("priority") or "Medium")) % 90 + 1)},
        "assignee": user_dto(issue.get("assignee_account_id"), users),
        "reporter": user_dto(issue.get("reporter_account_id"), users),
        "creator": user_dto(issue.get("creator_account_id"), users),
        "created": jira_ts(issue["created_at"]),
        "updated": jira_ts(issue["updated_at"]),
        "resolution": ({"name": issue["resolution"]} if issue.get("resolution") else None),
        "resolutiondate": jira_ts(issue.get("resolution_date")) if issue.get("resolution_date") else None,
        "labels": _jsonb(issue.get("labels")) or [],
        "components": _jsonb(issue.get("components")) or [],
        "project": project_dto(project, base_url),
        "customfield_10016": issue.get("story_points"),
        "customfield_10020": None,
    }
    if include_comment:
        cs = [comment_dto(c, base_url, users) for c in comments]
        fields["comment"] = {"comments": cs, "maxResults": len(cs),
                             "total": len(cs), "startAt": 0}

    out: dict[str, Any] = {
        "expand": "operations,versionedRepresentations,editmeta,changelog,renderedFields",
        "id": issue["issue_id"],
        "self": f"{base_url}/rest/api/3/issue/{issue['issue_id']}",
        "key": issue["issue_key"],
        "fields": fields,
    }
    if include_changelog:
        hs = [history_dto(h, users) for h in histories]
        out["changelog"] = {"startAt": 0, "maxResults": len(hs), "total": len(hs), "histories": hs}
    return out
