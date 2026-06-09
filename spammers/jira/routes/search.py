"""POST/GET /rest/api/3/search/jql  +  POST /rest/api/3/search/approximate-count.

The modern token-paginated issue search (the legacy ``/rest/api/3/search`` is
gone). Real Jira supports **both GET** (small queries; params in the query
string) **and POST** (large queries; JSON body) on ``/search/jql``. Either way it
returns ``{issues, isLast, nextPageToken?}`` — no ``startAt``/``total``.
``expand=changelog`` inlines each issue's changelog histories; ``fields``
including ``comment`` inlines the comments.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from spammers.jira.auth import authed_install
from spammers.jira.dto import issue_dto
from spammers.jira.jql import matches_updated, parse_jql
from spammers.jira.routes._common import decode_token, encode_token, load_users, unauthorized
from spammers.jira.state import state

router = APIRouter()

_DEFAULT_MAX = 50
_HARD_MAX = 5000  # real /search/jql cap (id/key-only pages); fields reduce it server-side


async def _read_json(request: Request) -> dict:
    try:
        body = await request.json()
        return body if isinstance(body, dict) else {}
    except Exception:
        return {}


async def _matching_issues(st, inst, jql_str: str):
    """Return (project_row, ordered+filtered issue rows) for a JQL string."""
    q = parse_jql(jql_str)
    if not q.project_key:
        return None, []
    project = await st.pool.fetchrow(
        "SELECT * FROM app_jira.projects WHERE installation_pk=$1 AND key=$2",
        inst["id"], q.project_key,
    )
    if project is None:
        return None, []
    rows = await st.pool.fetch(
        "SELECT * FROM app_jira.issues WHERE project_pk=$1", project["id"],
    )
    issues = [
        dict(r) for r in rows
        if matches_updated(r["updated_at"], q.updated_op, q.updated_dt)
    ]
    issues.sort(key=lambda r: r["updated_at"], reverse=q.order_desc)
    return dict(project), issues


async def _run_search(
    inst, *, jql_str: str, max_results: int, field_set: set, expand_str: str,
    next_page_token: Optional[str],
) -> dict[str, Any]:
    include_changelog = "changelog" in expand_str
    # `comment` is a non-navigable field: fetched only when explicitly selected
    # (or via `*all`). A default/empty selector returns IDs only — no comment.
    want_comment = ("comment" in field_set) or ("*all" in field_set)

    st = state()
    project, issues = await _matching_issues(st, inst, jql_str)
    if project is None:
        return {"issues": [], "isLast": True}

    offset = decode_token(next_page_token)
    page = issues[offset:offset + max_results]
    users = await load_users(st, inst["id"])
    base_url = inst["base_url"]

    out_issues: list[dict[str, Any]] = []
    for issue in page:
        comments = histories = []
        if want_comment:
            comments = [dict(c) for c in await st.pool.fetch(
                "SELECT * FROM app_jira.comments WHERE issue_pk=$1 ORDER BY position, created_at",
                issue["id"])]
        if include_changelog:
            histories = [dict(h) for h in await st.pool.fetch(
                "SELECT * FROM app_jira.changelogs WHERE issue_pk=$1 ORDER BY position, created_at",
                issue["id"])]
        out_issues.append(issue_dto(
            issue, base_url=base_url, users=users, project=project,
            comments=comments, histories=histories,
            requested_fields=field_set, include_changelog=include_changelog,
        ))

    more = offset + max_results < len(issues)
    resp: dict[str, Any] = {"issues": out_issues, "isLast": not more}
    if more:
        resp["nextPageToken"] = encode_token(offset + max_results)
    return resp


def _clamp_max(raw: Any) -> int:
    try:
        return max(1, min(int(raw), _HARD_MAX))
    except (TypeError, ValueError):
        return _DEFAULT_MAX


@router.post("/rest/api/3/search/jql")
async def search_jql_post(request: Request):
    inst = await authed_install(request)
    if inst is None:
        return unauthorized()
    body = await _read_json(request)
    fields = body.get("fields")
    field_set = set(fields) if isinstance(fields, list) else set()
    expand = body.get("expand") or ""
    expand_str = expand if isinstance(expand, str) else ",".join(expand) if isinstance(expand, list) else ""
    return JSONResponse(await _run_search(
        inst, jql_str=body.get("jql") or "", max_results=_clamp_max(body.get("maxResults", _DEFAULT_MAX)),
        field_set=field_set, expand_str=expand_str, next_page_token=body.get("nextPageToken"),
    ))


@router.get("/rest/api/3/search/jql")
async def search_jql_get(request: Request):
    inst = await authed_install(request)
    if inst is None:
        return unauthorized()
    q = request.query_params
    raw_fields = q.get("fields")
    field_set = {f.strip() for f in raw_fields.split(",") if f.strip()} if raw_fields else set()
    return JSONResponse(await _run_search(
        inst, jql_str=q.get("jql") or "", max_results=_clamp_max(q.get("maxResults", _DEFAULT_MAX)),
        field_set=field_set, expand_str=(q.get("expand") or ""),
        next_page_token=q.get("nextPageToken"),
    ))


@router.post("/rest/api/3/search/approximate-count")
async def approximate_count(request: Request):
    inst = await authed_install(request)
    if inst is None:
        return unauthorized()
    body = await _read_json(request)
    st = state()
    _project, issues = await _matching_issues(st, inst, body.get("jql") or "")
    return JSONResponse({"count": len(issues)})
