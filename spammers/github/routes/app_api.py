"""GitHub App API — authenticated with the App JWT (RS256).

  GET  /app
  GET  /app/installations
  GET  /app/installations/{installation_id}
  POST /app/installations/{installation_id}/access_tokens
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, Request

from spammers.common.errors import github_error
from spammers.common.ids import github_installation_token
from spammers.github.dto import app_dto, installation_dto, iso
from spammers.github.jwt_verify import resolve_app
from spammers.github.responses import GitHubJSONResponse as JSONResponse
from spammers.github.state import state

router = APIRouter()

_DOCS = "https://docs.github.com/rest"
# GitHub points App/installation 404s at the apps docs. A consumer treats a 404
# whose documentation_url is under /rest/apps/(apps|installations) as a
# revocation signal (the installation is gone/suspended) — so these MUST use the
# apps URL, distinct from the generic /rest 404s elsewhere.
_APPS_DOCS = "https://docs.github.com/rest/apps/apps"


def _unauthorized() -> JSONResponse:
    # GitHub returns 401 with this body when the App JWT is missing/invalid.
    return JSONResponse(github_error("Bad credentials", documentation_url=_DOCS), status_code=401)


@router.get("/app")
async def get_app(request: Request):
    app = await resolve_app(request)
    if app is None:
        return _unauthorized()
    return JSONResponse(app_dto(app))


@router.get("/app/installations")
async def list_installations(request: Request):
    app = await resolve_app(request)
    if app is None:
        return _unauthorized()
    rows = await state().pool.fetch(
        "SELECT * FROM app_github.installations WHERE app_pk = $1 ORDER BY installation_id",
        app["id"],
    )
    return JSONResponse([installation_dto(dict(r), app["app_id"]) for r in rows])


@router.get("/app/installations/{installation_id}")
async def get_installation(request: Request, installation_id: int):
    app = await resolve_app(request)
    if app is None:
        return _unauthorized()
    row = await state().pool.fetchrow(
        "SELECT * FROM app_github.installations WHERE app_pk = $1 AND installation_id = $2",
        app["id"], installation_id,
    )
    if row is None:
        return JSONResponse(github_error("Not Found", documentation_url=_APPS_DOCS), status_code=404)
    return JSONResponse(installation_dto(dict(row), app["app_id"]))


@router.post("/app/installations/{installation_id}/access_tokens")
async def create_access_token(request: Request, installation_id: int):
    app = await resolve_app(request)
    if app is None:
        return _unauthorized()
    st = state()
    inst = await st.pool.fetchrow(
        "SELECT * FROM app_github.installations WHERE app_pk = $1 AND installation_id = $2",
        app["id"], installation_id,
    )
    # A suspended/deleted installation can't mint tokens — GitHub 404s with the
    # apps documentation_url, which the consumer reads as a revocation signal.
    if inst is None or inst["suspended_at"] is not None:
        return JSONResponse(github_error("Not Found", documentation_url=_APPS_DOCS), status_code=404)

    token = github_installation_token()
    expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
    permissions = {"contents": "read", "metadata": "read", "pull_requests": "read", "issues": "read"}

    # Record the minted token so REST calls authenticate (oauth.installs, like Slack).
    await st.pool.execute(
        """
        INSERT INTO oauth.installs
            (id, run_id, provider, fyralis_tenant_id, provider_account_id,
             access_token, scopes, expires_at, extra)
        VALUES ($1, $2, 'github',
                (SELECT fyralis_tenant_id FROM org.runs WHERE id = $2),
                $3, $4, $5::jsonb, $6, $7::jsonb)
        ON CONFLICT (run_id, provider, provider_account_id) DO UPDATE
          SET access_token = EXCLUDED.access_token,
              expires_at  = EXCLUDED.expires_at,
              revoked_at  = NULL
        """,
        uuid4(), st.run_id, str(installation_id), token,
        '["contents:read","metadata:read"]', expires_at,
        '{"app_id": %d}' % app["app_id"],
    )

    return JSONResponse(
        {
            "token": token,
            "expires_at": iso(expires_at),
            "permissions": permissions,
            "repository_selection": inst["repository_selection"],
        },
        status_code=201,
    )
