"""GET /v1/pages/{id} — single page (webhook hydration path)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import notion_error
from spammers.notion.auth import authed
from spammers.notion.dto import page_dto
from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()


@router.get("/v1/pages/{page_id}")
async def get_page(request: Request, page_id: str):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    st = state()
    row = await st.pool.fetchrow(
        "SELECT * FROM app_notion.pages WHERE integration_pk = $1 AND page_id = $2",
        st.integration_pk, page_id,
    )
    if row is None:
        return JSONResponse(
            notion_error(404, "object_not_found", "Could not find page with ID: " + page_id),
            status_code=404,
        )
    return JSONResponse(page_dto(dict(row)))
