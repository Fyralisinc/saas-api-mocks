"""Response-object builders matching GitHub's REST shapes.

These aim to be indistinguishable from real GitHub for the fields a REST client
reads: ids/node_ids, the nested ``user`` objects, link URLs, and per-resource
metadata. Values that the mock doesn't model are returned with GitHub's documented
defaults (e.g. ``mergeable_state: "unknown"``, counts of 0) rather than omitted.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID

_API = "https://api.github.com"


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


def _login_id(login: str) -> int:
    """Stable numeric user id derived from a login."""
    return int(hashlib.sha1(login.encode()).hexdigest()[:8], 16)


def _jsonb(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else json.loads(value)


def _label_name(item) -> str:
    """A stored label is either a bare name (``"bug"``) or a GitHub-style object
    (``{"name": "bug", …}`` — what the corpus persists). Normalize to the name."""
    if isinstance(item, dict):
        return item.get("name", "")
    return item


def label_names(value) -> list[str]:
    """All label names from a stored JSONB labels column (either shape)."""
    return [_label_name(n) for n in _jsonb(value)]


def user_dto(login: str) -> dict:
    uid = _login_id(login)
    return {
        "login": login,
        "id": uid,
        "node_id": _node_id("User", uid),
        "avatar_url": f"https://avatars.githubusercontent.com/u/{uid}?v=4",
        "gravatar_id": "",
        "url": f"{_API}/users/{login}",
        "html_url": f"https://github.com/{login}",
        "followers_url": f"{_API}/users/{login}/followers",
        "following_url": f"{_API}/users/{login}/following{{/other_user}}",
        "gists_url": f"{_API}/users/{login}/gists{{/gist_id}}",
        "starred_url": f"{_API}/users/{login}/starred{{/owner}}{{/repo}}",
        "subscriptions_url": f"{_API}/users/{login}/subscriptions",
        "organizations_url": f"{_API}/users/{login}/orgs",
        "repos_url": f"{_API}/users/{login}/repos",
        "events_url": f"{_API}/users/{login}/events{{/privacy}}",
        "received_events_url": f"{_API}/users/{login}/received_events",
        "type": "User",
        "site_admin": False,
    }


def app_dto(app: dict) -> dict:
    return {
        "id": app["app_id"],
        "slug": app["slug"],
        "node_id": _node_id("Integration", app["app_id"]),
        "name": app["name"],
        "client_id": app["client_id"],
        "owner": user_dto(app["slug"]),
        "description": "",
        "external_url": "",
        "html_url": f"https://github.com/apps/{app['slug']}",
        "created_at": iso(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        "updated_at": iso(datetime(2024, 1, 1, tzinfo=timezone.utc)),
        "permissions": app["permissions"] if isinstance(app["permissions"], dict) else {},
        "events": app["events"] if isinstance(app["events"], list) else [],
    }


def installation_dto(inst: dict, app_id: int) -> dict:
    account = {**user_dto(inst["account_login"]), "type": inst["account_type"]}
    return {
        "id": inst["installation_id"],
        "account": account,
        "repository_selection": inst["repository_selection"],
        "access_tokens_url": f"{_API}/app/installations/{inst['installation_id']}/access_tokens",
        "repositories_url": f"{_API}/installation/repositories",
        "html_url": f"https://github.com/organizations/{inst['account_login']}/settings/installations/{inst['installation_id']}",
        "app_id": app_id,
        "app_slug": "",
        "target_id": inst["account_id"],
        "target_type": inst["account_type"],
        "permissions": {"contents": "read", "metadata": "read", "pull_requests": "read", "issues": "read"},
        "events": ["push", "pull_request", "issues", "issue_comment", "pull_request_review", "check_run"],
        "created_at": iso(inst.get("created_at")),
        "updated_at": iso(inst.get("created_at")),
        "suspended_at": iso(inst.get("suspended_at")),
        "suspended_by": None,
    }


def repo_dto(repo: dict) -> dict:
    full = f"{repo['owner']}/{repo['name']}"
    rid = repo["repo_id"]
    created = iso(repo.get("created_at"))
    return {
        "id": rid,
        "node_id": _node_id("Repository", rid),
        "name": repo["name"],
        "full_name": full,
        "private": repo["private"],
        "owner": {**user_dto(repo["owner"]), "type": "Organization"},
        "html_url": f"https://github.com/{full}",
        "description": repo.get("description"),
        "fork": False,
        "url": f"{_API}/repos/{full}",
        # URL templates — GitHub returns the full set on every repository object.
        "forks_url": f"{_API}/repos/{full}/forks",
        "keys_url": f"{_API}/repos/{full}/keys{{/key_id}}",
        "collaborators_url": f"{_API}/repos/{full}/collaborators{{/collaborator}}",
        "teams_url": f"{_API}/repos/{full}/teams",
        "hooks_url": f"{_API}/repos/{full}/hooks",
        "issue_events_url": f"{_API}/repos/{full}/issues/events{{/number}}",
        "events_url": f"{_API}/repos/{full}/events",
        "assignees_url": f"{_API}/repos/{full}/assignees{{/user}}",
        "branches_url": f"{_API}/repos/{full}/branches{{/branch}}",
        "tags_url": f"{_API}/repos/{full}/tags",
        "blobs_url": f"{_API}/repos/{full}/git/blobs{{/sha}}",
        "git_tags_url": f"{_API}/repos/{full}/git/tags{{/sha}}",
        "git_refs_url": f"{_API}/repos/{full}/git/refs{{/sha}}",
        "trees_url": f"{_API}/repos/{full}/git/trees{{/sha}}",
        "statuses_url": f"{_API}/repos/{full}/statuses/{{sha}}",
        "languages_url": f"{_API}/repos/{full}/languages",
        "stargazers_url": f"{_API}/repos/{full}/stargazers",
        "contributors_url": f"{_API}/repos/{full}/contributors",
        "subscribers_url": f"{_API}/repos/{full}/subscribers",
        "subscription_url": f"{_API}/repos/{full}/subscription",
        "commits_url": f"{_API}/repos/{full}/commits{{/sha}}",
        "git_commits_url": f"{_API}/repos/{full}/git/commits{{/sha}}",
        "comments_url": f"{_API}/repos/{full}/comments{{/number}}",
        "issue_comment_url": f"{_API}/repos/{full}/issues/comments{{/number}}",
        "contents_url": f"{_API}/repos/{full}/contents/{{+path}}",
        "compare_url": f"{_API}/repos/{full}/compare/{{base}}...{{head}}",
        "merges_url": f"{_API}/repos/{full}/merges",
        "archive_url": f"{_API}/repos/{full}/{{archive_format}}{{/ref}}",
        "downloads_url": f"{_API}/repos/{full}/downloads",
        "issues_url": f"{_API}/repos/{full}/issues{{/number}}",
        "pulls_url": f"{_API}/repos/{full}/pulls{{/number}}",
        "milestones_url": f"{_API}/repos/{full}/milestones{{/number}}",
        "notifications_url": f"{_API}/repos/{full}/notifications{{?since,all,participating}}",
        "labels_url": f"{_API}/repos/{full}/labels{{/name}}",
        "releases_url": f"{_API}/repos/{full}/releases{{/id}}",
        "deployments_url": f"{_API}/repos/{full}/deployments",
        "git_url": f"git://github.com/{full}.git",
        "ssh_url": f"git@github.com:{full}.git",
        "clone_url": f"https://github.com/{full}.git",
        "svn_url": f"https://github.com/{full}",
        "mirror_url": None,
        "homepage": None,
        "language": None,
        "forks_count": 0,
        "forks": 0,
        "stargazers_count": 0,
        "watchers_count": 0,
        "watchers": 0,
        "size": 0,
        "open_issues_count": 0,
        "open_issues": 0,
        "default_branch": repo["default_branch"],
        "has_issues": True,
        "has_projects": True,
        "has_downloads": True,
        "has_wiki": True,
        "has_pages": False,
        "license": None,
        "visibility": "private" if repo["private"] else "public",
        "archived": False,
        "disabled": False,
        "created_at": created,
        "updated_at": created,
        "pushed_at": created,
    }


# GitHub's built-in default labels (default: true on a fresh repo), with the
# canonical color + description GitHub ships them with.
_DEFAULT_LABEL_META: dict[str, tuple[str, str]] = {
    "bug":              ("d73a4a", "Something isn't working"),
    "documentation":    ("0075ca", "Improvements or additions to documentation"),
    "duplicate":        ("cfd3d7", "This issue or pull request already exists"),
    "enhancement":      ("a2eeef", "New feature or request"),
    "good first issue": ("7057ff", "Good for newcomers"),
    "help wanted":      ("008672", "Extra attention is needed"),
    "invalid":          ("e4e669", "This doesn't seem right"),
    "question":         ("d876e3", "Further information is requested"),
    "wontfix":          ("ffffff", "This will not be done"),
}
_DEFAULT_LABELS = frozenset(_DEFAULT_LABEL_META)

# Descriptions for the synthetic org's custom labels (a real org describes its
# labels; the embedded PR-label schema requires a non-null description).
_CUSTOM_LABEL_DESCRIPTIONS: dict[str, str] = {
    "chore": "Routine task or maintenance work",
    "deps":  "Dependency updates",
}


def min_repo_dto(repo: dict) -> dict:
    """The slim repository object GitHub embeds in installation / lifecycle
    webhook payloads (id, node_id, name, full_name, private)."""
    full = f"{repo['owner']}/{repo['name']}"
    rid = repo["repo_id"]
    return {
        "id": rid,
        "node_id": _node_id("Repository", rid),
        "name": repo["name"],
        "full_name": full,
        "private": repo["private"],
    }


def branch_dto(name: str, sha: str, full_name: str, *, protected: bool = False) -> dict:
    return {
        "name": name,
        "commit": {"sha": sha, "url": f"{_API}/repos/{full_name}/commits/{sha}"},
        "protected": protected,
    }


def label_dto(name: str, full_name: str) -> dict:
    lid = _login_id(f"{full_name}#label#{name}")
    meta = _DEFAULT_LABEL_META.get(name)
    if meta is not None:
        color, description = meta
    else:
        color = hashlib.sha1(name.encode()).hexdigest()[:6]
        description = _CUSTOM_LABEL_DESCRIPTIONS.get(name)
    return {
        "id": lid,
        "node_id": _node_id("Label", lid),
        "url": f"{_API}/repos/{full_name}/labels/{name.replace(' ', '%20')}",
        "name": name,
        "description": description,
        "color": color,
        "default": name in _DEFAULT_LABELS,
    }


def _ref(full_name: str, login: str, branch: str, sha: str, repo_obj: dict | None = None) -> dict:
    owner = full_name.split("/", 1)[0]
    return {
        "label": f"{owner}:{branch}",
        "ref": branch,
        "sha": sha,
        "user": user_dto(login),
        # head.repo / base.repo are full repository objects on the real API.
        "repo": repo_obj if repo_obj is not None else {
            "id": 0, "name": full_name.split("/", 1)[1], "full_name": full_name,
            "private": False, "owner": user_dto(owner),
        },
    }


def pull_request_dto(pr: dict, full_name: str, repo_row: dict | None = None) -> dict:
    num = pr["number"]
    repo_obj = repo_dto(repo_row) if repo_row is not None else None
    url = f"{_API}/repos/{full_name}/pulls/{num}"
    html_url = f"https://github.com/{full_name}/pull/{num}"
    issue_url = f"{_API}/repos/{full_name}/issues/{num}"
    comments_url = f"{_API}/repos/{full_name}/issues/{num}/comments"
    review_comments_url = f"{_API}/repos/{full_name}/pulls/{num}/comments"
    review_comment_url = f"{_API}/repos/{full_name}/pulls/comments{{/number}}"
    commits_url = f"{_API}/repos/{full_name}/pulls/{num}/commits"
    statuses_url = f"{_API}/repos/{full_name}/statuses/{pr['head_sha']}"
    return {
        "id": _num_id(pr["id"]),
        # node_id must be globally unique like real GitHub's — PR numbers
        # restart per repo, so key it on the row UUID, not the number, or a
        # dedup-by-node_id consumer collapses same-numbered PRs across repos.
        "node_id": _node_id("PullRequest", _num_id(pr["id"])),
        "number": num,
        "state": pr["state"],
        "locked": False,
        "title": pr["title"],
        "body": pr["body"],
        "draft": False,
        "user": user_dto(pr["user_login"]),
        "url": url,
        "html_url": html_url,
        "diff_url": f"{html_url}.diff",
        "patch_url": f"{html_url}.patch",
        "issue_url": issue_url,
        "comments_url": comments_url,
        "review_comments_url": review_comments_url,
        "review_comment_url": review_comment_url,
        "commits_url": commits_url,
        "statuses_url": statuses_url,
        "head": _ref(full_name, pr["user_login"], pr["head_ref"], pr["head_sha"], repo_obj),
        "base": _ref(full_name, pr["user_login"], pr["base_ref"], pr["base_sha"], repo_obj),
        "_links": {
            "self": {"href": url},
            "html": {"href": html_url},
            "issue": {"href": issue_url},
            "comments": {"href": comments_url},
            "review_comments": {"href": review_comments_url},
            "review_comment": {"href": review_comment_url},
            "commits": {"href": commits_url},
            "statuses": {"href": statuses_url},
        },
        "merged": pr["merged"],
        "merged_at": iso(pr.get("merged_at")),
        "merge_commit_sha": pr["head_sha"] if pr["merged"] else None,
        "mergeable": None,
        "mergeable_state": "unknown",
        "maintainer_can_modify": False,
        "merged_by": user_dto(pr["user_login"]) if pr["merged"] else None,
        "comments": 0,
        "review_comments": 0,
        "commits": 1,
        "additions": pr["additions"],
        "deletions": pr["deletions"],
        "changed_files": pr["changed_files"],
        "author_association": "MEMBER",
        "auto_merge": None,
        "assignee": None,
        "assignees": [],
        "requested_reviewers": [user_dto(login) for login in _jsonb(pr.get("requested_reviewers"))],
        "labels": [label_dto(n, full_name) for n in label_names(pr.get("labels"))],
        "milestone": None,
        "created_at": iso(pr.get("created_at")),
        "updated_at": iso(pr.get("updated_at")),
        "closed_at": iso(pr.get("closed_at")),
    }


def issue_dto(issue: dict, full_name: str, comments: int = 0, *, pull_request: dict | None = None) -> dict:
    num = issue["number"]
    dto = {
        "id": _num_id(issue["id"]),
        # Globally unique node_id (issue numbers restart per repo — keying on
        # the number collides across repos and a dedup-by-node_id consumer
        # collapses them). PRs-as-issues pass their PR UUID here, so their
        # Issue-kind node_id stays distinct from their PullRequest-kind one.
        "node_id": _node_id("Issue", _num_id(issue["id"])),
        "number": num,
        "state": issue["state"],
        "locked": False,
        "title": issue["title"],
        "body": issue["body"],
        "user": user_dto(issue["user_login"]),
        "url": f"{_API}/repos/{full_name}/issues/{num}",
        "repository_url": f"{_API}/repos/{full_name}",
        "comments_url": f"{_API}/repos/{full_name}/issues/{num}/comments",
        "events_url": f"{_API}/repos/{full_name}/issues/{num}/events",
        "labels_url": f"{_API}/repos/{full_name}/issues/{num}/labels{{/name}}",
        "html_url": f"https://github.com/{full_name}/issues/{num}",
        "assignee": None,
        "assignees": [user_dto(login) for login in _jsonb(issue.get("assignees"))],
        "labels": [label_dto(n, full_name) for n in label_names(issue.get("labels"))],
        "milestone": None,
        "comments": comments,
        "author_association": "MEMBER",
        "created_at": iso(issue.get("created_at")),
        "updated_at": iso(issue.get("updated_at")),
        "closed_at": iso(issue.get("closed_at")),
    }
    if pull_request is not None:
        dto["pull_request"] = pull_request
    return dto


def pr_as_issue_dto(pr: dict, full_name: str) -> dict:
    """A PR as it appears in the issues list (GitHub returns PRs there too)."""
    num = pr["number"]
    link = {
        "url": f"{_API}/repos/{full_name}/pulls/{num}",
        "html_url": f"https://github.com/{full_name}/pull/{num}",
        "diff_url": f"https://github.com/{full_name}/pull/{num}.diff",
        "patch_url": f"https://github.com/{full_name}/pull/{num}.patch",
        "merged_at": iso(pr.get("merged_at")),
    }
    issue_shaped = {
        "id": pr["id"],
        "number": num,
        "state": pr["state"],
        "title": pr["title"],
        "body": pr["body"],
        "user_login": pr["user_login"],
        "assignees": "[]",
        "labels": pr.get("labels"),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "closed_at": pr.get("closed_at"),
    }
    return issue_dto(issue_shaped, full_name, comments=0, pull_request=link)


# Deterministic synthetic file paths for a commit — same (repo, sha) always
# yields the same set, so the file list is stable across the single-commit GET
# and the push webhook (what a blast-radius consumer keys on). Plausible source
# paths/statuses; real GitHub's exact paths aren't reproducible anyway.
_SYNTH_DIRS = ("src", "lib", "internal", "pkg", "tests", "docs", "api", "cmd")
_SYNTH_NAMES = ("handler", "client", "service", "models", "utils", "config",
                "router", "cache", "auth", "db", "queue", "schema", "worker", "store")
_SYNTH_EXTS = (".py", ".ts", ".go", ".js", ".sql", ".md", ".yaml")
# Weighted toward "modified" — most real commits touch existing files.
_SYNTH_STATUS = ("modified", "modified", "modified", "added", "removed")


def synth_commit_files(full_name: str, sha: str) -> list[dict]:
    """A deterministic, plausible list of changed files for a commit (GitHub's
    commit ``files[]`` shape)."""
    h = hashlib.sha1(f"{full_name}@{sha}".encode()).hexdigest()
    count = 1 + (int(h[0], 16) % 4)  # 1..4 files
    files: list[dict] = []
    seen: set[str] = set()
    for i in range(count):
        seg = h[i * 6:(i + 1) * 6] or h[:6]
        path = (f"{_SYNTH_DIRS[int(seg[0], 16) % len(_SYNTH_DIRS)]}/"
                f"{_SYNTH_NAMES[int(seg[1], 16) % len(_SYNTH_NAMES)]}"
                f"{_SYNTH_EXTS[int(seg[2], 16) % len(_SYNTH_EXTS)]}")
        if path in seen:
            d, _, f = path.rpartition("/")
            path = f"{d}/{i}_{f}"
        seen.add(path)
        status = _SYNTH_STATUS[int(seg[3], 16) % len(_SYNTH_STATUS)]
        adds = 0 if status == "removed" else 1 + int(seg[4], 16) % 60
        dels = 0 if status == "added" else 1 + int(seg[5], 16) % 30
        files.append({
            "sha": hashlib.sha1(f"{sha}:{path}".encode()).hexdigest()[:40],
            "filename": path,
            "status": status,
            "additions": adds,
            "deletions": dels,
            "changes": adds + dels,
            "blob_url": f"https://github.com/{full_name}/blob/{sha}/{path}",
            "raw_url": f"https://github.com/{full_name}/raw/{sha}/{path}",
            "contents_url": f"{_API}/repos/{full_name}/contents/{path}?ref={sha}",
            "patch": f"@@ -0,0 +1,{adds} @@",
        })
    return files


def push_commit_file_lists(files: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """Split commit ``files[]`` into the (added, removed, modified) path lists a
    GitHub ``push`` payload carries on each commit."""
    added = [f["filename"] for f in files if f["status"] == "added"]
    removed = [f["filename"] for f in files if f["status"] == "removed"]
    modified = [f["filename"] for f in files if f["status"] not in ("added", "removed")]
    return added, removed, modified


def commit_dto(c: dict, full_name: str, *, include_files: bool = False) -> dict:
    sha = c["sha"]
    when = iso(c.get("committed_at"))
    dto = {
        "sha": sha,
        "node_id": _node_id("Commit", _num_id(c["id"])),
        "url": f"{_API}/repos/{full_name}/commits/{sha}",
        "html_url": f"https://github.com/{full_name}/commit/{sha}",
        "comments_url": f"{_API}/repos/{full_name}/commits/{sha}/comments",
        "commit": {
            "url": f"{_API}/repos/{full_name}/git/commits/{sha}",
            "message": c["message"],
            "author": {"name": c["author_login"], "email": c["author_email"], "date": when},
            "committer": {"name": c["author_login"], "email": c["author_email"], "date": when},
            "comment_count": 0,
            "tree": {"sha": sha, "url": f"{_API}/repos/{full_name}/git/trees/{sha}"},
        },
        "author": user_dto(c["author_login"]),
        "committer": user_dto(c["author_login"]),
        "parents": [{"sha": s, "url": f"{_API}/repos/{full_name}/commits/{s}"} for s in _jsonb(c.get("parents"))],
        "stats": {"additions": c["additions"], "deletions": c["deletions"], "total": c["additions"] + c["deletions"]},
    }
    # Real GitHub returns ``files`` only on the single-commit GET, not the list.
    if include_files:
        dto["files"] = synth_commit_files(full_name, sha)
    return dto


def _actor_dto(login: str) -> dict:
    """The slim actor object used inside Events API entries."""
    uid = _login_id(login)
    return {
        "id": uid,
        "login": login,
        "display_login": login,
        "gravatar_id": "",
        "url": f"{_API}/users/{login}",
        "avatar_url": f"https://avatars.githubusercontent.com/u/{uid}?v=4",
    }


def event_dto(
    *, event_id: int, kind: str, actor_login: str, full_name: str,
    repo_id: int, created_at, payload: dict,
) -> dict:
    """A repository Event (``GET /repos/{owner}/{repo}/events``)."""
    return {
        "id": str(event_id),
        "type": kind,
        "actor": _actor_dto(actor_login),
        "repo": {"id": repo_id, "name": full_name, "url": f"{_API}/repos/{full_name}"},
        "payload": payload,
        "public": True,
        "created_at": iso(created_at),
    }


def review_dto(r: dict) -> dict:
    return {
        "id": _num_id(r["id"]),
        "node_id": _node_id("PullRequestReview", _num_id(r["id"])),
        "user": user_dto(r["user_login"]),
        "body": r["body"],
        "state": r["state"].upper(),       # APPROVED / CHANGES_REQUESTED / COMMENTED / DISMISSED
        "author_association": "MEMBER",
        "submitted_at": iso(r.get("submitted_at")),
        "commit_id": None,
    }


def issue_comment_dto(c: dict, full_name: str) -> dict:
    cid = _num_id(c["id"])
    return {
        "id": cid,
        "node_id": _node_id("IssueComment", cid),
        "user": user_dto(c["user_login"]),
        "body": c["body"],
        "url": f"{_API}/repos/{full_name}/issues/comments/{cid}",
        "html_url": f"https://github.com/{full_name}/issues/{c['issue_number']}#issuecomment-{cid}",
        "issue_url": f"{_API}/repos/{full_name}/issues/{c['issue_number']}",
        "author_association": "MEMBER",
        "created_at": iso(c.get("created_at")),
        "updated_at": iso(c.get("created_at")),
    }


def check_run_dto(cr: dict) -> dict:
    cid = _num_id(cr["id"])
    return {
        "id": cid,
        "node_id": _node_id("CheckRun", cid),
        "head_sha": cr["head_sha"],
        "external_id": "",
        "url": f"{_API}/repos/checks/check-runs/{cid}",
        "html_url": f"https://github.com/checks/{cid}",
        "details_url": "",
        "status": cr["status"],
        "conclusion": cr.get("conclusion"),
        "started_at": iso(cr.get("started_at")),
        "completed_at": iso(cr.get("completed_at")),
        "name": cr["name"],
        "check_suite": {"id": cid},
        "output": {"title": None, "summary": None, "text": None, "annotations_count": 0},
        "pull_requests": [],
    }
