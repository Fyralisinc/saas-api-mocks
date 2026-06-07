"""GET /v1/blocks/{id}/children — the block tree under a page (or block).

The backfill calls this with a page id to read its content blocks. Blocks in
this mock are flat (one level under the page), so a block id that isn't a page
returns an empty child list.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import notion_error
from spammers.notion.auth import authed, page_slice
from spammers.notion.dto import block_dto, list_dto
from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()


@router.get("/v1/blocks/{block_id}/children")
async def block_children(request: Request, block_id: str):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    st = state()
    page = await st.pool.fetchrow(
        "SELECT id, page_id FROM app_notion.pages WHERE integration_pk = $1 AND page_id = $2",
        st.integration_pk, block_id,
    )
    if page is None:
        # Either an unknown id, or a leaf block with no children.
        return JSONResponse(list_dto([], next_cursor=None, type_key="block"))
    rows = await st.pool.fetch(
        "SELECT * FROM app_notion.blocks WHERE page_pk = $1 ORDER BY position ASC",
        page["id"],
    )
    results = [block_dto(dict(r), page["page_id"]) for r in rows]
    qs = request.query_params
    items, next_cursor = page_slice(results, qs.get("start_cursor"), qs.get("page_size"))
    return JSONResponse(list_dto(items, next_cursor=next_cursor, type_key="block"))
