"""POST /v1/search — enumerate pages and/or databases.

The backfill uses ``filter.value`` = ``page`` | ``database`` to walk each object
class; the reconcile probe sends ``sort`` desc + ``page_size`` 1 for a cheap
"latest edit" check.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import notion_error
from spammers.notion.auth import authed, page_slice
from spammers.notion.dto import database_dto, list_dto, page_dto
from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()


@router.post("/v1/search")
async def search(request: Request):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}

    flt = (body.get("filter") or {})
    value = flt.get("value")  # 'page' | 'database' | None
    query = (body.get("query") or "").lower()
    sort = body.get("sort") or {}
    direction = (sort.get("direction") or "descending")

    st = state()
    items: list[tuple] = []  # (last_edited_time, dto)

    if value in (None, "page"):
        rows = await st.pool.fetch(
            "SELECT * FROM app_notion.pages WHERE integration_pk = $1", st.integration_pk,
        )
        for r in rows:
            if query and query not in (r["title"] or "").lower():
                continue
            items.append((r["last_edited_time"], page_dto(dict(r))))
    if value in (None, "database"):
        rows = await st.pool.fetch(
            "SELECT * FROM app_notion.databases WHERE integration_pk = $1", st.integration_pk,
        )
        for r in rows:
            if query and query not in (r["title"] or "").lower():
                continue
            items.append((r["last_edited_time"], database_dto(dict(r))))

    items.sort(key=lambda t: t[0], reverse=(direction != "ascending"))
    page, next_cursor = page_slice([d for _, d in items], body.get("start_cursor"), body.get("page_size"))
    return JSONResponse(list_dto(page, next_cursor=next_cursor, type_key="page_or_database"))
