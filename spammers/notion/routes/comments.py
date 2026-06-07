"""GET /v1/comments?block_id={id} — comments on a page (or block)."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import notion_error
from spammers.notion.auth import authed, page_slice
from spammers.notion.dto import comment_dto, list_dto
from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()


@router.get("/v1/comments")
async def list_comments(request: Request):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    qs = request.query_params
    block_id = qs.get("block_id")
    if not block_id:
        return JSONResponse(
            notion_error(400, "validation_error", "block_id is required."), status_code=400,
        )
    st = state()
    rows = await st.pool.fetch(
        """
        SELECT c.* FROM app_notion.comments c
          JOIN app_notion.pages p ON p.id = c.page_pk
         WHERE p.integration_pk = $1 AND c.parent_page_id = $2
         ORDER BY c.created_time ASC
        """,
        st.integration_pk, block_id,
    )
    results = [comment_dto(dict(r)) for r in rows]
    items, next_cursor = page_slice(results, qs.get("start_cursor"), qs.get("page_size"))
    return JSONResponse(list_dto(items, next_cursor=next_cursor, type_key="comment"))
