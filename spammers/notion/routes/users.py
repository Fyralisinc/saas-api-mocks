"""GET /v1/users/me (bot identity) and GET /v1/users/{id}."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Request

from spammers.common.errors import notion_error
from spammers.notion.auth import authed, page_slice
from spammers.notion.dto import bot_user_dto, list_dto, person_user_dto
from spammers.notion.responses import NotionJSONResponse as JSONResponse
from spammers.notion.state import state

router = APIRouter()


@router.get("/v1/users")
async def list_users(request: Request):
    """List all users in the workspace — the integration's bot plus the
    workspace members (people). Cursor-paginated, like the real endpoint."""
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    st = state()
    rows = await st.pool.fetch(
        "SELECT id, full_name, email FROM org.people WHERE run_id=$1 ORDER BY full_name, id", st.run_id)
    results = [bot_user_dto(st)] + [person_user_dto(r["id"], r["full_name"], r["email"]) for r in rows]
    qs = request.query_params
    items, next_cursor = page_slice(results, qs.get("start_cursor"), qs.get("page_size"))
    return JSONResponse(list_dto(items, next_cursor=next_cursor, type_key="user"))


@router.get("/v1/users/me")
async def users_me(request: Request):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."), status_code=401)
    return JSONResponse(bot_user_dto(state()))


@router.get("/v1/users/{user_id}")
async def get_user(request: Request, user_id: str):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "The bearer token is not valid."),
                            status_code=401)
    st = state()
    if user_id == st.bot_user_id:
        return JSONResponse(bot_user_dto(st))
    # A non-UUID id is a malformed request → 400 validation_error (real Notion).
    try:
        _uuid = UUID(user_id)
    except (ValueError, AttributeError):
        return JSONResponse(
            notion_error(400, "validation_error",
                         f"path failed validation: path.user_id should be a valid uuid, "
                         f"instead was `\"{user_id}\"`."),
            status_code=400)
    row = await st.pool.fetchrow(
        "SELECT id, full_name, email FROM org.people WHERE run_id=$1 AND id=$2",
        st.run_id, _uuid)
    if row is None:
        # Unknown / inaccessible user → 404, like real Notion.
        return JSONResponse(
            notion_error(404, "object_not_found",
                         "Could not find user with ID: " + user_id),
            status_code=404)
    return JSONResponse(person_user_dto(row["id"], row["full_name"], row["email"]))
