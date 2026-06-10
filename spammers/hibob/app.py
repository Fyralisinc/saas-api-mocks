"""HiBob ("Bob") HR-platform mock — FastAPI app.

Serves the REAL ``api.hibob.com`` ``/v1/`` read surface a connector hits for HR
ingestion (the Fyralis flow doc's Gusto/Brex-cloned offset/limit GET shapes are
UNVERIFIED and DIVERGE — see the hibob-fidelity-audit memory). Three endpoints,
each a DIFFERENT, faithfully-modelled pagination mode:

    POST /v1/people/search             ({employees:[…]}; returns ALL — NO pagination)
    GET  /v1/timeoff/requests/changes  (BARE ARRAY; since/to date window, ≤6 months)
    GET  /v1/bulk/people/salaries       ({results, response_metadata:{next_cursor}};
                                         CURSOR pagination, limit default 50 / max 200)

Auth is a service-user HTTP Basic credential ``base64(service_user_id:token)`` (see
auth.py); the mock is single-tenant per run and accepts any well-formed Basic
header, a missing/blank one is 401. Rate limiting is 429 + ``X-RateLimit-*``
headers (NO ``Retry-After`` — HiBob signals via ``X-RateLimit-Reset``, a real
divergence from the Brex archetype the Fyralis client clones). The error envelope
is ``{error, message, statusCode, timestamp}``.
"""
from __future__ import annotations

import base64
import json
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.hibob import dto as _dto
from spammers.hibob import state as _state
from spammers.hibob.auth import is_authed

_FORCED_429 = {"count": 0}
_SALARIES_DEFAULT_LIMIT = 50
_SALARIES_MAX_LIMIT = 200
_MAX_TIMEOFF_WINDOW = timedelta(days=186)   # ~6 months (HiBob's documented max lookback)
_RL_LIMIT = 50                              # /v1/people/search documents 50 req/min


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _error(status: int, message: str, *, error: str | None = None) -> JSONResponse:
    # HiBob's error body varies slightly by module; the closest-to-standard shape
    # documented is {error, message, statusCode, timestamp} (apidocs error-handling).
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") \
        + f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
    return JSONResponse(
        {"error": error or message, "message": message, "statusCode": status,
         "timestamp": now},
        status_code=status)


def _unauthorized() -> JSONResponse:
    return _error(401, "The request could not be authenticated. Check your "
                       "service user credentials.", error="Unauthorized")


# --------------------------------------------------------------- opaque cursor

def _encode_cursor(offset: int) -> str:
    raw = json.dumps({"offset": offset}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(tok: str) -> Optional[int]:
    try:
        pad = "=" * (-len(tok) % 4)
        obj = json.loads(base64.urlsafe_b64decode(tok + pad))
    except (ValueError, json.JSONDecodeError):
        return None
    off = obj.get("offset") if isinstance(obj, dict) else None
    return off if isinstance(off, int) and off >= 0 else None


def _parse_dt(raw: str) -> Optional[datetime]:
    """Parse an ISO-8601 ``since``/``to`` value (accepts trailing ``Z`` or offset)."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def _read_json_body(request: Request) -> dict:
    """Read a JSON request body (HiBob's people/search is a POST with a JSON body)."""
    raw = await request.body()
    if not raw:
        return {}
    try:
        obj = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def create_app() -> FastAPI:
    app = FastAPI(title="HiBob mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v1/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            resp = _error(429, "Rate limit exceeded.", error="Too Many Requests")
            # HiBob signals the window via X-RateLimit-* (Reset = Unix epoch) — and
            # does NOT document Retry-After. Emit the real headers, not Retry-After.
            resp.headers["X-RateLimit-Limit"] = str(_RL_LIMIT)
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["X-RateLimit-Reset"] = str(int(time.time()) + 60)
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "hibob-mock", "run_id": str(s.run_id),
                "legal_business_name": co["legal_business_name"] if co else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ----------------------------------------------------------- people search

    @app.post("/v1/people/search")
    async def people_search(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        if co is None:
            return JSONResponse({"employees": []})
        body = await _read_json_body(request)
        show_inactive = bool(body.get("showInactive", False))

        # Filters: HiBob supports only root.id / root.email with operator `equals`.
        id_vals: set[str] = set()
        email_vals: set[str] = set()
        for f in (body.get("filters") or []):
            if not isinstance(f, dict):
                continue
            fp = f.get("fieldPath")
            vals = f.get("values") or []
            if fp == "root.id":
                id_vals |= {str(v) for v in vals}
            elif fp == "root.email":
                email_vals |= {str(v) for v in vals}

        clauses = ["company_pk = $1"]
        params: list[Any] = [co["id"]]
        if not show_inactive:
            clauses.append("is_active = TRUE")
        if id_vals:
            params.append(list(id_vals))
            clauses.append(f"employee_id = ANY(${len(params)})")
        if email_vals:
            params.append(list(email_vals))
            clauses.append(f"email = ANY(${len(params)})")
        where = " AND ".join(clauses)

        rows = await s.pool.fetch(
            f"SELECT e.*, c.company_id FROM app_hibob.employees e "
            f"JOIN app_hibob.companies c ON c.id = e.company_pk "
            f"WHERE {where} ORDER BY sort_key ASC, employee_id ASC", *params)
        # No pagination: every matching employee returns in one array.
        return JSONResponse({"employees": [_dto.employee_dto(dict(r)) for r in rows]})

    # ----------------------------------------------------- timeoff changes feed

    @app.get("/v1/timeoff/requests/changes")
    async def timeoff_changes(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        if co is None:
            return JSONResponse([])
        qp = request.query_params
        # `since` is REQUIRED; `to` defaults to now. Window must be <= ~6 months.
        if not qp.get("since"):
            return _error(400, "`since` is a required parameter.", error="Bad Request")
        since = _parse_dt(qp["since"])
        if since is None:
            return _error(400, "`since` must be an ISO-8601 date-time.", error="Bad Request")
        to = _parse_dt(qp["to"]) if qp.get("to") else datetime.now(timezone.utc)
        if to is None:
            return _error(400, "`to` must be an ISO-8601 date-time.", error="Bad Request")
        if to - since > _MAX_TIMEOFF_WINDOW:
            return _error(400, "The maximum lookback window is 6 months.",
                          error="Bad Request")

        rows = await s.pool.fetch(
            "SELECT * FROM app_hibob.timeoff_changes "
            "WHERE company_pk = $1 AND created_on >= $2 AND created_on < $3 "
            "ORDER BY created_on ASC, sort_key ASC",
            co["id"], since, to)
        # Filtered by CHANGE date; the response is a BARE ARRAY (no envelope).
        return JSONResponse([_dto.timeoff_change_dto(dict(r)) for r in rows])

    # --------------------------------------------------------- bulk salaries

    @app.get("/v1/bulk/people/salaries")
    async def bulk_salaries(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        if co is None:
            return JSONResponse({"results": [], "response_metadata": {"next_cursor": None},
                                 "errors": []})
        qp = request.query_params
        limit = _SALARIES_DEFAULT_LIMIT
        if qp.get("limit"):
            try:
                limit = int(qp["limit"])
            except ValueError:
                return _error(400, "`limit` must be an integer.", error="Bad Request")
            if limit < 1:
                return _error(400, "`limit` must be >= 1.", error="Bad Request")
            limit = min(limit, _SALARIES_MAX_LIMIT)   # clamp, not error
        offset = 0
        if qp.get("cursor"):
            offset = _decode_cursor(qp["cursor"])
            if offset is None:
                return _error(400, "`cursor` is invalid.", error="Bad Request")

        clauses = ["company_pk = $1"]
        params: list[Any] = [co["id"]]
        emp_ids = qp.get("employeeIds")
        if emp_ids:
            params.append([e.strip() for e in emp_ids.split(",") if e.strip()])
            clauses.append(f"employee_id = ANY(${len(params)})")
        # ``includeArchived`` is accepted but does not filter — the mock models
        # current + historical (raise) salary entries, none "archived/deleted".
        where = " AND ".join(clauses)

        rows = await s.pool.fetch(
            f"SELECT * FROM app_hibob.salaries WHERE {where} "
            f"ORDER BY sort_key ASC, salary_id ASC LIMIT {limit + 1} OFFSET {offset}",
            *params)
        has_more = len(rows) > limit
        window = [dict(r) for r in rows[:limit]]
        nxt = _encode_cursor(offset + limit) if has_more else None
        return JSONResponse({
            "results": [_dto.salary_dto(r) for r in window],
            "response_metadata": {"next_cursor": nxt},
            "errors": [],
        })

    return app


app = create_app()
