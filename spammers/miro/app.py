"""Miro collaborative-whiteboard mock — FastAPI app.

Serves the REAL ``api.miro.com`` ``/v2/`` read surface a connector hits for
whiteboard ingestion. The Fyralis flow doc's Brex-Bearer-cloned client assumes
"everything is the same paginator" and is UNVERIFIED; the REAL contract splits
into TWO paginators (divergence LOGGED in miro-fidelity-audit):

    GET /v2/boards                  (OFFSET pagination — limit 1-50/def 20, offset;
                                     {data,total,size,offset,limit,links,type})
    GET /v2/boards/{board_id}       (single board — adds a links{self,related} object)
    GET /v2/boards/{board_id}/items (CURSOR pagination — limit 10-50/def 10, opaque
                                     cursor; {data,total,size,cursor,limit,links};
                                     `cursor` ABSENT on the last page)

Auth is ``Authorization: Bearer`` (scope ``boards:read``); the mock is single-tenant
per run and accepts any non-empty Bearer. A missing/blank token is **401** with the
``{status, code:"tokenNotProvided", message, type:"error"}`` envelope. Rate limiting
is CREDIT-based: 429 + ``X-RateLimit-Limit/Remaining/Reset`` headers and **NO
Retry-After** (a real divergence from the Brex archetype). Timestamps are UTC
ISO-8601 with **millisecond** precision and a trailing ``Z``.

**Poll-only.** Miro discontinued its experimental webhooks on 2025-12-05; there is
NO production push and no signature scheme. There is therefore no webhooks module,
no live emit, and no live ingest slice — incremental ingestion is the consumer
re-walking ``/items`` and dedup'ing via the versioned external_id.
"""
from __future__ import annotations

import time
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager

from spammers.miro import dto as _dto
from spammers.miro import state as _state
from spammers.miro.auth import is_authed

_FORCED_429 = {"count": 0}
_BOARDS_DEFAULT_LIMIT = 20
_BOARDS_MAX_LIMIT = 50
_BOARDS_MIN_LIMIT = 1
_ITEMS_DEFAULT_LIMIT = 10
_ITEMS_MAX_LIMIT = 50
_ITEMS_MIN_LIMIT = 10
_RL_LIMIT = 100_000  # credits/min (the documented global Miro budget)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _error(status: int, code: str, message: str) -> JSONResponse:
    # Miro's error envelope: {status, code, message, context?, type:"error"}.
    return JSONResponse(
        {"status": status, "code": code, "message": message, "type": "error"},
        status_code=status)


def _unauthorized() -> JSONResponse:
    return _error(401, "tokenNotProvided",
                  "No authorization data was found on the request")


def _not_found(message: str = "Board not found") -> JSONResponse:
    return _error(404, "notFound", message)


def _int_param(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _rl_headers() -> dict[str, str]:
    # Credit-based rate-limit signalling — present on every response. No Retry-After.
    return {
        "X-RateLimit-Limit": str(_RL_LIMIT),
        "X-RateLimit-Remaining": str(_RL_LIMIT - 100),
        "X-RateLimit-Reset": str(int(time.time()) + 60),
    }


def create_app() -> FastAPI:
    app = FastAPI(title="Miro mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v2/"):
            if _FORCED_429["count"] > 0:
                _FORCED_429["count"] -= 1
                resp = _error(429, "tooManyRequests", "Request rate limit exceeded")
                # Credit-based: X-RateLimit-* headers, NO Retry-After (vs Figma/Brex).
                for k, v in _rl_headers().items():
                    resp.headers[k] = v
                resp.headers["X-RateLimit-Remaining"] = "0"
                return resp
            resp = await call_next(request)
            for k, v in _rl_headers().items():
                resp.headers.setdefault(k, v)
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "miro-mock", "run_id": str(s.run_id),
                "org_id": org["org_id"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # --------------------------------------------------- shared helpers

    async def _user(s, user_pk) -> Optional[dict]:
        if user_pk is None:
            return None
        row = await s.pool.fetchrow(
            "SELECT miro_user_id, name, role FROM app_miro.users WHERE id = $1", user_pk)
        return dict(row) if row else None

    async def _member(s, org_pk) -> Optional[dict]:
        row = await s.pool.fetchrow(
            "SELECT miro_user_id, name, role FROM app_miro.users "
            "WHERE org_pk = $1 AND is_me = TRUE LIMIT 1", org_pk)
        return dict(row) if row else None

    def _page_links(request: Request, *, limit: int, offset: int,
                    total: int, size: int) -> dict[str, str]:
        def url(off: int) -> str:
            # include_query_params merges — keeps any team_id/query/sort filter.
            return str(request.url.include_query_params(limit=limit, offset=off))
        links = {"self": url(offset), "first": url(0)}
        if offset > 0:
            links["prev"] = url(max(0, offset - limit))
        if offset + size < total:
            links["next"] = url(offset + size)
        last_off = max(0, ((total - 1) // limit) * limit) if total else 0
        links["last"] = url(last_off)
        return links

    # ------------------------------------------------ GET /v2/boards (offset)

    @app.get("/v2/boards")
    async def list_boards(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse({"data": [], "total": 0, "size": 0, "offset": 0,
                                 "limit": _BOARDS_DEFAULT_LIMIT, "links": {},
                                 "type": "list"})
        qp = request.query_params

        limit = _BOARDS_DEFAULT_LIMIT
        if qp.get("limit") is not None:
            li = _int_param(qp.get("limit"))
            if li is None:
                return _error(400, "parametersValidationError", "Invalid limit")
            limit = max(_BOARDS_MIN_LIMIT, min(li, _BOARDS_MAX_LIMIT))
        offset = 0
        if qp.get("offset") is not None:
            of = _int_param(qp.get("offset"))
            if of is None or of < 0:
                return _error(400, "parametersValidationError", "Invalid offset")
            offset = of

        total = await s.pool.fetchval(
            "SELECT count(*) FROM app_miro.boards WHERE org_pk = $1", org["id"])
        total = int(total or 0)
        rows = await s.pool.fetch(
            "SELECT * FROM app_miro.boards WHERE org_pk = $1 "
            "ORDER BY sort_key ASC, board_id ASC LIMIT $2 OFFSET $3",
            org["id"], limit, offset)
        member = await _member(s, org["id"])
        data = []
        for r in rows:
            rd = dict(r)
            data.append(_dto.board_dto(
                rd,
                owner=await _user(s, rd.get("owner_user_pk")),
                created_by=await _user(s, rd.get("created_by_user_pk")),
                modified_by=await _user(s, rd.get("modified_by_user_pk")),
                member=member, team_id=org["team_id"], team_name=org["team_name"],
                base_url=org["base_url"]))
        links = _page_links(request, limit=limit, offset=offset,
                            total=total, size=len(data))
        return JSONResponse({"data": data, "total": total, "size": len(data),
                             "offset": offset, "limit": limit, "links": links,
                             "type": "list"})

    # --------------------------------------- GET /v2/boards/{id} (single board)

    @app.get("/v2/boards/{board_id}")
    async def get_board(request: Request, board_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _not_found()
        row = await s.pool.fetchrow(
            "SELECT * FROM app_miro.boards WHERE org_pk = $1 AND board_id = $2",
            org["id"], board_id)
        if row is None:
            return _not_found()
        rd = dict(row)
        member = await _member(s, org["id"])
        return JSONResponse(_dto.board_dto(
            rd,
            owner=await _user(s, rd.get("owner_user_pk")),
            created_by=await _user(s, rd.get("created_by_user_pk")),
            modified_by=await _user(s, rd.get("modified_by_user_pk")),
            member=member, team_id=org["team_id"], team_name=org["team_name"],
            base_url=org["base_url"], with_links=True))

    # --------------------------------- GET /v2/boards/{id}/items (cursor)

    @app.get("/v2/boards/{board_id}/items")
    async def list_items(request: Request, board_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _not_found()
        board = await s.pool.fetchrow(
            "SELECT id FROM app_miro.boards WHERE org_pk = $1 AND board_id = $2",
            org["id"], board_id)
        if board is None:
            return _not_found()
        qp = request.query_params

        limit = _ITEMS_DEFAULT_LIMIT
        if qp.get("limit") is not None:
            li = _int_param(qp.get("limit"))
            if li is None:
                return _error(400, "parametersValidationError", "Invalid limit")
            limit = max(_ITEMS_MIN_LIMIT, min(li, _ITEMS_MAX_LIMIT))
        item_type = qp.get("type")
        if item_type is not None and item_type not in _dto.ITEM_TYPES:
            return _error(400, "parametersValidationError",
                          f"Invalid item type: {item_type}")
        floor = _dto.decode_cursor(qp.get("cursor"))

        clauses = ["board_pk = $1"]
        params: list[Any] = [board["id"]]
        if floor is not None:
            params.append(floor)
            clauses.append(f"item_seq > ${len(params)}")
        if item_type is not None:
            params.append(item_type)
            clauses.append(f"item_type = ${len(params)}")
        where = " AND ".join(clauses)

        total_clauses = ["board_pk = $1"]
        total_params: list[Any] = [board["id"]]
        if item_type is not None:
            total_params.append(item_type)
            total_clauses.append(f"item_type = ${len(total_params)}")
        total = await s.pool.fetchval(
            f"SELECT count(*) FROM app_miro.items WHERE {' AND '.join(total_clauses)}",
            *total_params)
        total = int(total or 0)

        # Fetch one extra row to detect a further page.
        rows = await s.pool.fetch(
            f"SELECT * FROM app_miro.items WHERE {where} "
            f"ORDER BY item_seq ASC LIMIT {limit + 1}", *params)
        window = [dict(r) for r in rows[:limit]]
        data = []
        for r in window:
            data.append(_dto.item_dto(
                r,
                created_by=await _user(s, r.get("created_by_user_pk")),
                modified_by=await _user(s, r.get("modified_by_user_pk"))))

        body: dict[str, Any] = {"data": data, "total": total, "size": len(data),
                                "limit": limit}
        links: dict[str, str] = {"self": str(request.url)}
        has_more = len(rows) > limit
        if has_more and window:
            cursor = _dto.encode_cursor(window[-1]["item_seq"])
            body["cursor"] = cursor   # ABSENT on the last page
            # include_query_params merges — keeps any `type` filter across the walk.
            links["next"] = str(request.url.include_query_params(
                limit=limit, cursor=cursor))
        body["links"] = links
        return JSONResponse(body)

    return app


app = create_app()
