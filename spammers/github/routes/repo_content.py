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
    check_run_dto,
    commit_dto,
    issue_comment_dto,
    issue_dto,
    pull_request_dto,
    review_dto,
)
from spammers.github.ratelimit import check as ratelimit_check
from spammers.github.responses import GitHubJSONResponse as JSONResponse
from spammers.github.state import state

router = APIRouter()

_API_BASE = "https://api.github.com"
_DOCS = "https://docs.github.com/rest"


async def _ctx(request: Request, owner: str, repo: str):
    """Resolve installation + rate limit + repo. Returns (repo_row, headers, error)."""
    inst = await resolve_installation(request)
    if inst is None:
        return None, {}, JSONResponse(github_error("Bad credentials", documentation_url=_DOCS), status_code=401)
    headers, rl = await ratelimit_check(inst["installation_id"])
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
    per_page: int = Query(30, ge=1, le=100), page: int = Query(1, ge=1),
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
    rows = await state().pool.fetch(
        f"SELECT * FROM app_github.pull_requests WHERE {where} ORDER BY number DESC", *args
    )
    dtos = [pull_request_dto(dict(r), full) for r in rows]
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
    return JSONResponse(pull_request_dto(dict(row), repo_row["full_name"]), headers=headers)


@router.get("/repos/{owner}/{repo}/pulls/{number}/reviews")
async def list_reviews(
    request: Request, owner: str, repo: str, number: int,
    per_page: int = Query(30, ge=1, le=100), page: int = Query(1, ge=1),
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
    per_page: int = Query(30, ge=1, le=100), page: int = Query(1, ge=1),
):
    # NOTE: real GitHub also returns PRs here; this slice returns issues only.
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    full = repo_row["full_name"]
    where = "i.repo_pk = $1"
    args: list = [repo_row["id"]]
    if state_ in ("open", "closed"):
        where += " AND i.state = $2"
        args.append(state_)
    rows = await state().pool.fetch(
        f"""
        SELECT i.*, (SELECT count(*) FROM app_github.issue_comments c
                      WHERE c.repo_pk = i.repo_pk AND c.issue_number = i.number) AS comment_count
          FROM app_github.issues i WHERE {where} ORDER BY i.number DESC
        """,
        *args,
    )
    dtos = [issue_dto(dict(r), full, comments=r["comment_count"]) for r in rows]
    page_rows, out = _paginate(dtos, per_page, page, f"/repos/{full}/issues", headers)
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
    per_page: int = Query(30, ge=1, le=100), page: int = Query(1, ge=1),
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
    per_page: int = Query(30, ge=1, le=100), page: int = Query(1, ge=1),
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
    return JSONResponse(commit_dto(dict(row), repo_row["full_name"]), headers=headers)


@router.get("/repos/{owner}/{repo}/commits/{ref}/check-runs")
async def list_check_runs(request: Request, owner: str, repo: str, ref: str):
    repo_row, headers, err = await _ctx(request, owner, repo)
    if err:
        return err
    rows = await state().pool.fetch(
        "SELECT * FROM app_github.check_runs WHERE repo_pk = $1 AND head_sha = $2 ORDER BY started_at",
        repo_row["id"], ref,
    )
    body = {"total_count": len(rows), "check_runs": [check_run_dto(dict(r)) for r in rows]}
    return JSONResponse(body, headers=headers)
