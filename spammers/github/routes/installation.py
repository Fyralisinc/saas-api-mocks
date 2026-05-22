"""GET /installation/repositories — repos visible to the installation token."""
from __future__ import annotations

import math

from fastapi import APIRouter, Query, Request

from spammers.common.errors import github_error
from spammers.common.pagination import github_link_header
from spammers.github.auth import resolve_installation
from spammers.github.dto import repo_dto
from spammers.github.ratelimit import check as ratelimit_check
from spammers.github.responses import GitHubJSONResponse as JSONResponse
from spammers.github.state import state

router = APIRouter()

_API_BASE = "https://api.github.com"
_DOCS = "https://docs.github.com/rest"


@router.get("/installation/repositories")
async def installation_repositories(
    request: Request,
    per_page: int = Query(30, ge=1, le=100),
    page: int = Query(1, ge=1),
):
    inst = await resolve_installation(request)
    if inst is None:
        return JSONResponse(github_error("Bad credentials", documentation_url=_DOCS), status_code=401)

    rl_headers, rl = await ratelimit_check(request, inst["installation_id"])
    if rl is not None:
        return rl

    rows = await state().pool.fetch(
        "SELECT * FROM app_github.repositories WHERE installation_pk = $1 ORDER BY name",
        inst["installation_pk"],
    )
    total = len(rows)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    page_rows = rows[start:start + per_page]

    headers = dict(rl_headers)
    link = github_link_header(
        base_url=_API_BASE, path="/installation/repositories",
        per_page=per_page, page=page, total_pages=total_pages,
    )
    if link:
        headers["Link"] = link

    body = {"total_count": total, "repositories": [repo_dto(dict(r)) for r in page_rows]}
    return JSONResponse(body, headers=headers)
