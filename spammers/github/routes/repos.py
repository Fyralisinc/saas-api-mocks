"""GET /repos/{owner}/{repo} — single repository (installation-token authed)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import github_error
from spammers.github.auth import resolve_installation
from spammers.github.dto import repo_dto
from spammers.github.ratelimit import check as ratelimit_check
from spammers.github.responses import GitHubJSONResponse as JSONResponse
from spammers.github.state import state

router = APIRouter()

_DOCS = "https://docs.github.com/rest"


@router.get("/repos/{owner}/{repo}")
async def get_repo(request: Request, owner: str, repo: str):
    inst = await resolve_installation(request)
    if inst is None:
        return JSONResponse(github_error("Bad credentials", documentation_url=_DOCS), status_code=401)

    rl_headers, rl = await ratelimit_check(inst["installation_id"])
    if rl is not None:
        return rl

    row = await state().pool.fetchrow(
        """
        SELECT * FROM app_github.repositories
         WHERE installation_pk = $1 AND owner = $2 AND name = $3
        """,
        inst["installation_pk"], owner, repo,
    )
    if row is None:
        return JSONResponse(
            github_error("Not Found", documentation_url="https://docs.github.com/rest/repos/repos#get-a-repository"),
            status_code=404, headers=rl_headers,
        )
    return JSONResponse(repo_dto(dict(row)), headers=rl_headers)
