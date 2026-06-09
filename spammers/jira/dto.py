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


def _avatar_urls(base_url: str, account_id: str) -> dict[str, str]:
    # Real Jira users always carry the four avatar sizes; the consumer doesn't
    # read them but a blind validator expects the key to exist.
    return {
        sz: f"{base_url}/secure/useravatar?size={name}&ownerId={account_id}"
        for sz, name in (("48x48", "large"), ("32x32", "medium"),
                         ("24x24", "small"), ("16x16", "xsmall"))
    }


def user_dto(account_id: Optional[str], users: dict[str, dict],
             base_url: str) -> Optional[dict[str, Any]]:
    if not account_id:
        return None
    u = users.get(account_id)
    return {
        # Real Jira: the User `self` is the per-site account URL (query-param form).
        "self": f"{base_url}/rest/api/3/user?accountId={account_id}",
        "accountId": account_id,
        "accountType": "atlassian",
        "emailAddress": (u or {}).get("email"),
        "avatarUrls": _avatar_urls(base_url, account_id),
        "displayName": (u or {}).get("display_name") or account_id,
        "active": True,
        "timeZone": "Etc/UTC",
    }


# Jira's fixed statusCategory taxonomy: (id, key, name, colorName). `undefined`
# (id=1) is unused here; issues map to new/indeterminate/done.
_STATUS_CATEGORY_DEF = {
    "new": (2, "new", "To Do", "blue-gray"),
    "indeterminate": (4, "indeterminate", "In Progress", "yellow"),
    "done": (3, "done", "Done", "green"),
}


def _status_dto(name: str, category: str, base_url: str) -> dict[str, Any]:
    cat_id, cat_key, cat_name, color = _STATUS_CATEGORY_DEF.get(
        category, _STATUS_CATEGORY_DEF["new"])
    status_id = str(abs(hash(name)) % 9000 + 1000)
    return {
        "self": f"{base_url}/rest/api/3/status/{status_id}",
        "name": name,
        "id": status_id,
        "statusCategory": {
            "self": f"{base_url}/rest/api/3/statuscategory/{cat_id}",
            "id": cat_id,
            "key": cat_key,
            "colorName": color,
            "name": cat_name,
        },
    }


def comment_dto(c: dict[str, Any], base_url: str, users: dict[str, dict]) -> dict[str, Any]:
    return {
        "self": f"{base_url}/rest/api/3/issue/comment/{c['comment_id']}",
        "id": c["comment_id"],
        "author": user_dto(c.get("author_account_id"), users, base_url),
        "updateAuthor": user_dto(c.get("author_account_id"), users, base_url),
        "body": _jsonb(c.get("body")) or adf(""),
        "created": jira_ts(c["created_at"]),
        "updated": jira_ts(c["updated_at"]),
    }


def history_dto(h: dict[str, Any], users: dict[str, dict], base_url: str) -> dict[str, Any]:
    return {
        "id": h["history_id"],
        "author": user_dto(h.get("author_account_id"), users, base_url),
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


# `comment` is the only non-navigable field the mock models — it is returned
# ONLY when explicitly requested or via `*all`, never under `*navigable`.
_NON_NAVIGABLE = {"comment"}


def select_fields(full: dict[str, Any], requested: set[str]) -> dict[str, Any]:
    """Project a full ``fields`` dict per the new ``/search/jql`` selector.

    Real Jira: an ABSENT/empty ``fields`` returns IDs only (``fields`` == ``{}``).
    Otherwise the selector is a union of positive tokens minus ``-`` excludes:
    ``*all`` (every field incl. comment), ``*navigable`` (every field except
    non-navigable ones like comment), or explicit field names. This differs from
    the old ``/search`` whose default was ``*navigable``.
    """
    if not requested:
        return {}  # IDs only — the new endpoint's default
    includes = {t for t in requested if not t.startswith("-")}
    excludes = {t[1:] for t in requested if t.startswith("-")}
    keys: set[str] = set()
    for tok in includes:
        if tok == "*all":
            keys |= set(full)
        elif tok == "*navigable":
            keys |= {k for k in full if k not in _NON_NAVIGABLE}
        elif tok in full:
            keys.add(tok)
        # an explicit name the mock doesn't model is silently dropped (real Jira
        # would return it as null); we don't fabricate keys.
    keys -= excludes
    return {k: full[k] for k in full if k in keys}


def issue_dto(
    issue: dict[str, Any],
    *,
    base_url: str,
    users: dict[str, dict],
    project: dict[str, Any],
    comments: list[dict[str, Any]],
    histories: list[dict[str, Any]],
    requested_fields: set[str],
    include_changelog: bool,
) -> dict[str, Any]:
    full: dict[str, Any] = {
        "summary": issue["summary"],
        "description": _jsonb(issue.get("description")),
        "issuetype": {"name": issue.get("issue_type") or "Task",
                      "id": str(abs(hash(issue.get("issue_type") or "Task")) % 9000 + 1000),
                      "subtask": False},
        "status": _status_dto(issue.get("status") or "To Do",
                              issue.get("status_category") or "new", base_url),
        "priority": {"name": issue.get("priority") or "Medium",
                     "id": str(abs(hash(issue.get("priority") or "Medium")) % 90 + 1)},
        "assignee": user_dto(issue.get("assignee_account_id"), users, base_url),
        "reporter": user_dto(issue.get("reporter_account_id"), users, base_url),
        "creator": user_dto(issue.get("creator_account_id"), users, base_url),
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
    # `comment` is a field; build it only when the selector will keep it.
    want_comment = "*all" in requested_fields or "comment" in requested_fields
    if want_comment:
        cs = [comment_dto(c, base_url, users) for c in comments]
        full["comment"] = {"comments": cs, "maxResults": len(cs),
                           "total": len(cs), "startAt": 0}

    out: dict[str, Any] = {
        "expand": "operations,versionedRepresentations,editmeta,changelog,renderedFields",
        "id": issue["issue_id"],
        "self": f"{base_url}/rest/api/3/issue/{issue['issue_id']}",
        "key": issue["issue_key"],
        "fields": select_fields(full, requested_fields),
    }
    if include_changelog:
        hs = [history_dto(h, users, base_url) for h in histories]
        out["changelog"] = {"startAt": 0, "maxResults": len(hs), "total": len(hs), "histories": hs}
    return out
