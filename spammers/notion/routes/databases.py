"""GET /v1/databases/{id} and POST /v1/databases/{id}/query (rows)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import notion_error
from spammers.notion.auth import authed, page_slice
from spammers.notion.dto import database_dto, list_dto, page_dto
from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()

# Notion `query_database` accepts a `sorts` array. Fyralis's reconciler probe
# (`latest_database_edit`) sends `page_size=1` + a descending `last_edited_time`
# sort to read the newest row edit, so the mock MUST honour timestamp sorts —
# returning the oldest row would make every gap probe wrong.
_SORTABLE_TS = {"created_time", "last_edited_time"}


def _order_clause(sorts) -> str:
    if isinstance(sorts, list) and sorts and isinstance(sorts[0], dict):
        ts = sorts[0].get("timestamp")
        direction = "DESC" if sorts[0].get("direction") == "descending" else "ASC"
        if ts in _SORTABLE_TS:
            return f"ORDER BY {ts} {direction}, page_id ASC"
    return "ORDER BY created_time ASC, page_id ASC"


@router.get("/v1/databases/{database_id}")
async def get_database(request: Request, database_id: str):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    st = state()
    row = await st.pool.fetchrow(
        "SELECT * FROM app_notion.databases WHERE integration_pk = $1 AND database_id = $2",
        st.integration_pk, database_id,
    )
    if row is None:
        return JSONResponse(notion_error(404, "object_not_found",
                                         "Could not find database with ID: " + database_id), status_code=404)
    return JSONResponse(database_dto(dict(row)))


@router.post("/v1/databases/{database_id}/query")
async def query_database(request: Request, database_id: str):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    st = state()
    db = await st.pool.fetchrow(
        "SELECT id FROM app_notion.databases WHERE integration_pk = $1 AND database_id = $2",
        st.integration_pk, database_id,
    )
    if db is None:
        return JSONResponse(notion_error(404, "object_not_found",
                                         "Could not find database with ID: " + database_id), status_code=404)
    rows = await st.pool.fetch(
        "SELECT * FROM app_notion.pages WHERE database_pk = $1 " + _order_clause(body.get("sorts")),
        db["id"],
    )
    results = [page_dto(dict(r)) for r in rows]
    page, next_cursor = page_slice(results, body.get("start_cursor"), body.get("page_size"))
    return JSONResponse(list_dto(page, next_cursor=next_cursor, type_key="page"))
