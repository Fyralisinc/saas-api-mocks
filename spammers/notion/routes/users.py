"""GET /v1/users/me (bot identity) and GET /v1/users/{id}."""
from __future__ import annotations

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
        return JSONResponse(notion_error(401, "unauthorized", "API token is invalid."), status_code=401)
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
        return JSONResponse(notion_error(401, "unauthorized", "API token is invalid."), status_code=401)
    return JSONResponse(bot_user_dto(state()))


@router.get("/v1/users/{user_id}")
async def get_user(request: Request, user_id: str):
    if not authed(request):
        return JSONResponse(notion_error(401, "unauthorized", "API token is invalid."), status_code=401)
    st = state()
    if user_id == st.bot_user_id:
        return JSONResponse(bot_user_dto(st))
    # Workspace members are returned as partial person users.
    return JSONResponse({
        "object": "user", "id": user_id, "name": None, "avatar_url": None,
        "type": "person", "person": {},
    })
