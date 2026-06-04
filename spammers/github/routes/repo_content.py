"""Repository content reads (installation-token authed):

  GET /repos/{owner}/{repo}/pulls[/{number}[/reviews]]
  GET /repos/{owner}/{repo}/issues[/{number}[/comments]]
  GET /repos/{owner}/{repo}/commits[/{sha}]
  GET /repos/{owner}/{repo}/commits/{ref}/check-runs
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, Query, Request

from spammers.common.errors import github_error
from spammers.common.pagination import github_link_header
from spammers.github.auth import resolve_installation
from spammers.github.dto import (
    _login_id,
    branch_dto,
    check_run_dto,
    commit_dto,
    event_dto,
    issue_comment_dto,
    issue_dto,
    label_dto,
    label_names,
    pr_as_issue_dto,
    pull_request_dto,
    review_dto,
)
from spammers.github.ratelimit import check as ratelimit_check
from spammers.github.responses import GitHubJSONResponse as JSONResponse
from spammers.github.state import state

router = APIRouter()

_API_BASE = "https://api.github.com"
_DOCS = "https://docs.github.com/rest"

# GitHub's documented `sort` values for issues/pulls map to row timestamps. We
# model `created`/`updated`; unmodeled values (comments/popularity/long-running)
# fall back to created, like a freshly-created repo with no such signal.
_SORT_COL = {"created": "created_at", "updated": "updated_at"}


def _order(sort: str, direction: str) -> tuple[str, str]:
    """Resolve (column, SQL direction) from GitHub's sort/direction params.

    Real GitHub honours ``sort``/``direction`` on the issues and pulls lists
    (defaults: ``sort=created&direction=desc``); a consumer that scans with
    ``sort=updated&direction=asc`` (as Fyralis does) depends on that ordering for
    its cursor + reconciler baseline, so the mock must not silently re-sort.
    """
    col = _SORT_COL.get(sort, "created_at")
    sql_dir = "ASC" if str(direction).lower() == "asc" else "DESC"
    return col, sql_dir


async def _ctx(request: Request, owner: str, repo: str):
    """Resolve installation + rate limit + repo. Returns (repo_row, headers, error)."""
    inst = await resolve_installation(request)
    if inst is None:
        return None, {}, JSONResponse(github_error("Bad credentials", documentation_url=_DOCS), status_code=401)
    headers, rl = await ratelimit_check(request, inst["installation_id"])
    if rl is not None:
        return None, headers, rl
    row = await state().pool.fetchrow(
        "SELECT * FROM app_github.repositories WHERE installation_pk = $1 AND owner = $2 AND name = $3",
        inst["installation_pk"], owner, repo,
    )
    if row is None:
        return None, headers, JSONResponse(
            github_error("Not Found", documentation_url=_DOCS), status_code=404, headers=headers
        )
    return dict(row), headers, None


def _paginate(rows: list, per_page: int, page: int, path: str, headers: dict) -> tuple[list, dict]:
    # GitHub silently clamps per_page to [1, 100] and treats page < 1 as 1 —
    # it does not 422 on out-of-range values.
    per_page = max(1, min(per_page, 100))
    page = max(1, page)
    total_pages = max(1, math.ceil(len(rows) / per_page))
    start = (page - 1) * per_page
    out = dict(headers)
    link = github_link_header(base_url=_API_BASE, path=path, per_page=per_page, page=page, total_pages=total_pages)
    if link:
        out["Link"] = link
    return rows[start:start + per_page], out


# ----------------------------- pull requests -----------------------------

@router.get("/repos/{owner}/{repo}/pulls")
async def list_pulls(
    request: Request, owner: str, repo: str,
    state_: str = Query("open", alias="state"),
    sort: str = Query("created"), direction: str = Query("desc"),
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    full = repo_row["full_name"]
    where = "repo_pk = $1"
    args: list = [repo_row["id"]]
    if state_ in ("open", "closed"):
        where += " AND state = $2"
        args.append(state_)
    col, sql_dir = _order(sort, direction)
    rows = await state().pool.fetch(
        f"SELECT * FROM app_github.pull_requests WHERE {where} "
        f"ORDER BY {col} {sql_dir}, number {sql_dir}", *args
    )
    dtos = [pull_request_dto(dict(r), full, repo_row) for r in rows]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{full}/pulls", headers)
    return JSONResponse(page_rows, headers=out)


@router.get("/repos/{owner}/{repo}/pulls/{number}")
async def get_pull(request: Request, owner: str, repo: str, number: int):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    row = await state().pool.fetchrow(
        "SELECT * FROM app_github.pull_requests WHERE repo_pk = $1 AND number = $2",
        repo_row["id"], number,
    )
    if row is None:
        return JSONResponse(github_error("Not Found", documentation_url=_DOCS), status_code=404, headers=headers)
    return JSONResponse(pull_request_dto(dict(row), repo_row["full_name"], repo_row), headers=headers)


@router.get("/repos/{owner}/{repo}/pulls/{number}/reviews")
async def list_reviews(
    request: Request, owner: str, repo: str, number: int,
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    rows = await state().pool.fetch(
        """
        SELECT rv.* FROM app_github.reviews rv
          JOIN app_github.pull_requests pr ON pr.id = rv.pr_pk
         WHERE pr.repo_pk = $1 AND pr.number = $2
         ORDER BY rv.submitted_at
        """,
        repo_row["id"], number,
    )
    dtos = [review_dto(dict(r)) for r in rows]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{repo_row['full_name']}/pulls/{number}/reviews", headers)
    return JSONResponse(page_rows, headers=out)


# --------------------------------- issues ---------------------------------

@router.get("/repos/{owner}/{repo}/issues")
async def list_issues(
    request: Request, owner: str, repo: str,
    state_: str = Query("open", alias="state"),
    sort: str = Query("created"), direction: str = Query("desc"),
    per_page: int = Query(30), page: int = Query(1),
):
    # On real GitHub, pull requests ARE issues and appear in this list too
    # (each PR carries a ``pull_request`` key). We merge issues + PRs and honour
    # the requested sort/direction (default created/desc) — a forward-scanning
    # consumer (sort=updated&direction=asc) relies on that order for its cursor.
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    full = repo_row["full_name"]
    state_filter = state_ if state_ in ("open", "closed") else None

    pool = state().pool
    issue_where = "i.repo_pk = $1" + (" AND i.state = $2" if state_filter else "")
    issue_args = [repo_row["id"]] + ([state_filter] if state_filter else [])
    issue_rows = await pool.fetch(
        f"""
        SELECT i.*, (SELECT count(*) FROM app_github.issue_comments c
                      WHERE c.repo_pk = i.repo_pk AND c.issue_number = i.number) AS comment_count
          FROM app_github.issues i WHERE {issue_where}
        """,
        *issue_args,
    )
    pr_where = "repo_pk = $1" + (" AND state = $2" if state_filter else "")
    pr_rows = await pool.fetch(
        f"SELECT * FROM app_github.pull_requests WHERE {pr_where}", *issue_args
    )

    combined = [(r["number"], issue_dto(dict(r), full, comments=r["comment_count"])) for r in issue_rows]
    combined += [(r["number"], pr_as_issue_dto(dict(r), full)) for r in pr_rows]
    # Sort on the DTO's ISO timestamp (lexically chronological), number as a
    # stable tiebreak; both in the requested direction.
    field = "updated_at" if sort == "updated" else "created_at"
    reverse = str(direction).lower() != "asc"
    combined.sort(key=lambda t: (t[1].get(field) or "", t[0]), reverse=reverse)
    dtos = [d for _, d in combined]

    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{full}/issues", headers)
    return JSONResponse(page_rows, headers=out)


@router.get("/repos/{owner}/{repo}/issues/comments")
async def list_repo_issue_comments(
    request: Request, owner: str, repo: str,
    sort: str = Query("created"), direction: str = Query("desc"),
    since: Optional[str] = Query(None),
    per_page: int = Query(30), page: int = Query(1),
):
    """Repo-wide issue-comments list (every issue's comments in one feed).

    MUST be declared before ``/issues/{number}`` — FastAPI matches in
    declaration order, so otherwise the literal ``comments`` segment gets
    captured as ``{number}`` and 422s on int parsing.
    """
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    order = "ASC" if direction.lower() == "asc" else "DESC"
    rows = await state().pool.fetch(
        f"SELECT * FROM app_github.issue_comments WHERE repo_pk = $1 ORDER BY created_at {order}",
        repo_row["id"],
    )
    dtos = [issue_comment_dto(dict(r), repo_row["full_name"]) for r in rows]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{repo_row['full_name']}/issues/comments", headers)
    return JSONResponse(page_rows, headers=out)


@router.get("/repos/{owner}/{repo}/issues/{number}")
async def get_issue(request: Request, owner: str, repo: str, number: int):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    row = await state().pool.fetchrow(
        "SELECT * FROM app_github.issues WHERE repo_pk = $1 AND number = $2",
        repo_row["id"], number,
    )
    if row is None:
        return JSONResponse(github_error("Not Found", documentation_url=_DOCS), status_code=404, headers=headers)
    n = await state().pool.fetchval(
        "SELECT count(*) FROM app_github.issue_comments WHERE repo_pk = $1 AND issue_number = $2",
        repo_row["id"], number,
    )
    return JSONResponse(issue_dto(dict(row), repo_row["full_name"], comments=n), headers=headers)


@router.get("/repos/{owner}/{repo}/issues/{number}/comments")
async def list_issue_comments(
    request: Request, owner: str, repo: str, number: int,
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    rows = await state().pool.fetch(
        "SELECT * FROM app_github.issue_comments WHERE repo_pk = $1 AND issue_number = $2 ORDER BY created_at",
        repo_row["id"], number,
    )
    dtos = [issue_comment_dto(dict(r), repo_row["full_name"]) for r in rows]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{repo_row['full_name']}/issues/{number}/comments", headers)
    return JSONResponse(page_rows, headers=out)


# -------------------------------- commits ---------------------------------

@router.get("/repos/{owner}/{repo}/commits")
async def list_commits(
    request: Request, owner: str, repo: str,
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    rows = await state().pool.fetch(
        "SELECT * FROM app_github.commits WHERE repo_pk = $1 ORDER BY committed_at DESC",
        repo_row["id"],
    )
    dtos = [commit_dto(dict(r), repo_row["full_name"]) for r in rows]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{repo_row['full_name']}/commits", headers)
    return JSONResponse(page_rows, headers=out)


@router.get("/repos/{owner}/{repo}/commits/{sha}")
async def get_commit(request: Request, owner: str, repo: str, sha: str):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    row = await state().pool.fetchrow(
        "SELECT * FROM app_github.commits WHERE repo_pk = $1 AND sha = $2",
        repo_row["id"], sha,
    )
    if row is None:
        return JSONResponse(github_error("Not Found", documentation_url=_DOCS), status_code=404, headers=headers)
    # The single-commit GET includes the per-file diff (the list endpoint omits it).
    return JSONResponse(commit_dto(dict(row), repo_row["full_name"], include_files=True), headers=headers)


@router.get("/repos/{owner}/{repo}/commits/{ref}/check-runs")
async def list_check_runs(
    request: Request, owner: str, repo: str, ref: str,
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    rows = await state().pool.fetch(
        "SELECT * FROM app_github.check_runs WHERE repo_pk = $1 AND head_sha = $2 ORDER BY started_at",
        repo_row["id"], ref,
    )
    dtos = [check_run_dto(dict(r)) for r in rows]
    page_rows, out = _paginate(
        dtos, per_page, page, f"/repos/{repo_row['full_name']}/commits/{ref}/check-runs", headers
    )
    # total_count is the full count; check_runs holds the current page.
    body = {"total_count": len(rows), "check_runs": page_rows}
    return JSONResponse(body, headers=out)


# -------------------------------- branches --------------------------------

@router.get("/repos/{owner}/{repo}/branches")
async def list_branches(
    request: Request, owner: str, repo: str,
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    full = repo_row["full_name"]
    # The mock models a single branch per repo (its default); point it at HEAD.
    head = await state().pool.fetchrow(
        "SELECT sha FROM app_github.commits WHERE repo_pk = $1 ORDER BY committed_at DESC LIMIT 1",
        repo_row["id"],
    )
    sha = head["sha"] if head else "0" * 40
    branches = [branch_dto(repo_row["default_branch"], sha, full, protected=True)]
    page_rows, out = _paginate(branches, per_page, page, f"/repos/{full}/branches", headers)
    return JSONResponse(page_rows, headers=out)


# --------------------------------- labels ---------------------------------

@router.get("/repos/{owner}/{repo}/labels")
async def list_labels(
    request: Request, owner: str, repo: str,
    per_page: int = Query(30), page: int = Query(1),
):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    full = repo_row["full_name"]
    # GitHub has no labels table here; derive the repo's label set from the
    # labels actually applied across its issues and pull requests.
    rows = await state().pool.fetch(
        """
        SELECT labels FROM app_github.issues WHERE repo_pk = $1
        UNION ALL
        SELECT labels FROM app_github.pull_requests WHERE repo_pk = $1
        """,
        repo_row["id"],
    )
    names = sorted({n for r in rows for n in label_names(r["labels"])})
    labels = [label_dto(n, full) for n in names]
    page_rows, out = _paginate(labels, per_page, page, f"/repos/{full}/labels", headers)
    return JSONResponse(page_rows, headers=out)


def _evid(*parts) -> int:
    return _login_id("#".join(str(p) for p in parts))


@router.get("/repos/{owner}/{repo}/events")
async def list_repo_events(
    request: Request, owner: str, repo: str,
    per_page: int = Query(30), page: int = Query(1),
):
    """The repository's recent activity feed, newest first — synthesized from
    its pull requests, issues and pushes, the way real GitHub aggregates them."""
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    full = repo_row["full_name"]
    rid = repo_row["repo_id"]
    branch = repo_row["default_branch"]
    pool = state().pool
    events: list[tuple] = []

    for pr in await pool.fetch("SELECT * FROM app_github.pull_requests WHERE repo_pk = $1", repo_row["id"]):
        prd = dict(pr)
        events.append((prd.get("created_at"), event_dto(
            event_id=_evid(full, "pr", prd["number"]), kind="PullRequestEvent",
            actor_login=prd["user_login"], full_name=full, repo_id=rid,
            created_at=prd.get("created_at"),
            payload={"action": "opened", "number": prd["number"],
                     "pull_request": pull_request_dto(prd, full, repo_row)},
        )))

    for i in await pool.fetch("SELECT * FROM app_github.issues WHERE repo_pk = $1", repo_row["id"]):
        idd = dict(i)
        events.append((idd.get("created_at"), event_dto(
            event_id=_evid(full, "issue", idd["number"]), kind="IssuesEvent",
            actor_login=idd["user_login"], full_name=full, repo_id=rid,
            created_at=idd.get("created_at"),
            payload={"action": "opened", "issue": issue_dto(idd, full)},
        )))

    for c in await pool.fetch("SELECT * FROM app_github.commits WHERE repo_pk = $1", repo_row["id"]):
        cd = dict(c)
        sha = cd["sha"]
        events.append((cd.get("committed_at"), event_dto(
            event_id=_evid(full, "push", sha), kind="PushEvent",
            actor_login=cd["author_login"], full_name=full, repo_id=rid,
            created_at=cd.get("committed_at"),
            payload={"push_id": _evid(full, "pushid", sha), "size": 1, "distinct_size": 1,
                     "ref": f"refs/heads/{branch}", "head": sha, "before": "0" * 40,
                     "commits": [{"sha": sha, "distinct": True, "message": cd["message"],
                                  "author": {"email": cd["author_email"], "name": cd["author_login"]},
                                  "url": f"{_API_BASE}/repos/{full}/commits/{sha}"}]},
        )))

    # Newest first; rows without a timestamp sort last.
    events.sort(key=lambda t: t[0].timestamp() if t[0] is not None else 0.0, reverse=True)
    dtos = [e for _, e in events]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{full}/events", headers)
    return JSONResponse(page_rows, headers=out)
