"""GET /rest/api/3/project/search — projects visible to the token (startAt paging)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from spammers.jira.auth import authed_install
from spammers.jira.dto import project_dto
from spammers.jira.routes._common import unauthorized
from spammers.jira.state import state

router = APIRouter()


@router.get("/rest/api/3/project/search")
async def project_search(request: Request):
    inst = await authed_install(request)
    if inst is None:
        return unauthorized()
    st = state()
    rows = await st.pool.fetch(
        "SELECT * FROM app_jira.projects WHERE installation_pk=$1 ORDER BY key", inst["id"],
    )
    q = request.query_params
    try:
        start_at = max(0, int(q.get("startAt", 0)))
    except ValueError:
        start_at = 0
    try:
        max_results = max(1, min(int(q.get("maxResults", 50)), 50))
    except ValueError:
        max_results = 50

    base_url = inst["base_url"]
    page = rows[start_at:start_at + max_results]
    total = len(rows)
    is_last = start_at + max_results >= total
    body = {
        "self": f"{base_url}/rest/api/3/project/search?startAt={start_at}&maxResults={max_results}",
        "maxResults": max_results,
        "startAt": start_at,
        "total": total,
        "isLast": is_last,
        "values": [project_dto(dict(r), base_url) for r in page],
    }
    if not is_last:
        nxt = start_at + max_results
        body["nextPage"] = f"{base_url}/rest/api/3/project/search?startAt={nxt}&maxResults={max_results}"
    return JSONResponse(body)
