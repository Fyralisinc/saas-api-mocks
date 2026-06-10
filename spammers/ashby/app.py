"""Ashby (recruiting / ATS) mock — FastAPI app.

Ashby is an **RPC-style** API: every read is an HTTP ``POST`` to
``/<category>.<verb>`` (NOT REST, NOT GraphQL). The surface a connector hits for
ingestion is one ``.list`` per entity category (cursor-paginated, with an
incremental ``syncToken``) plus ``.info`` for a single entity:

    POST /candidate.list      POST /candidate.info
    POST /application.list     POST /application.info
    POST /job.list             POST /job.info
    POST /interview.list       POST /interview.info
    POST /offer.list           POST /offer.info

Auth is the org API key as the HTTP Basic **username** with an EMPTY password
(see auth.py). The mock is single-tenant per run and accepts any non-empty key.

**Response envelope** (CONFIRMED from Ashby's OpenAPI):
  * ``.list``/``.search`` → ``{"success": true, "results": [ … ],
    "moreDataAvailable": bool, "nextCursor": "…", "syncToken": "…"}`` — ``results``
    is an ARRAY; ``nextCursor`` is present ONLY when ``moreDataAvailable`` is true;
    ``syncToken`` is returned ONLY on the terminal page (``moreDataAvailable``
    false), a fresh opaque token to persist for the next incremental sync.
  * ``.info`` → ``{"success": true, "results": { … }}`` — ``results`` is an OBJECT.
  * **business errors** (bad cursor, unknown id) → HTTP **200** with
    ``{"success": false, "errors": ["<code>"], "errorInfo": {"code": "…", …}}``.
    Only auth (401) and the forced 429 are HTTP-level errors; an unknown
    ``/<category>.<verb>`` path is a genuine 404.

**Pagination + syncToken.** The cursor is an opaque page token (the mock encodes
a base64url offset); ``limit`` default AND max is **100**. A ``syncToken`` carries
an opaque ``updatedAt`` floor — when supplied, ``.list`` returns only entities
changed since (``updated_at > floor``); cursor + syncToken compose (both ride a
paged sync). Sort order is UNCONFIRMED in Ashby's docs; the mock uses a stable
``(updated_at ASC, entity_id ASC)`` walk so the syncToken floor is a contiguous
suffix and the minted token = the kind's high-water ``updated_at``.

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. Ashby's docs
describe a 429 (rate/concurrent limit) but pin no ``Retry-After``/``X-RateLimit-*``
headers (UNCONFIRMED) — the mock emits ``Retry-After`` only on this mock-only path.
"""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.ashby import dto as _dto
from spammers.ashby import state as _state
from spammers.ashby.auth import is_authed

_FORCED_429 = {"count": 0}
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 100

# Every paginated ``.list`` in Ashby's OpenAPI that carries a ``syncToken`` request
# param is sync-capable; all five ingestion categories are.
_SYNC_CAPABLE = set(_dto.CATEGORIES)


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _ok(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse({"success": True, **payload})


def _app_error(code: str, message: str, *, status: int = 200) -> JSONResponse:
    """An Ashby application error — ``success:false`` at HTTP 200 (default).

    ``errors`` (the deprecated array-of-codes) + the structured ``errorInfo`` are
    both present per Ashby's OpenAPI error branch.
    """
    return JSONResponse(
        {
            "success": False,
            "errors": [code],
            "errorInfo": {"code": code, "message": message, "requestId": None, "meta": {}},
        },
        status_code=status,
    )


def _unauthorized() -> JSONResponse:
    # Ashby returns 401 for a missing key (body shape UNCONFIRMED — docs call it a
    # "human-readable response"); the mock reuses the success:false envelope.
    return _app_error("unauthorized", "Missing or invalid API key.", status=401)


# --------------------------------------------------------------- opaque tokens

def _b64u(obj: dict) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64u(tok: str) -> Optional[dict]:
    try:
        pad = "=" * (-len(tok) % 4)
        raw = base64.urlsafe_b64decode(tok + pad)
        obj = json.loads(raw)
        return obj if isinstance(obj, dict) else None
    except (ValueError, json.JSONDecodeError):
        return None


def _encode_cursor(offset: int) -> str:
    return _b64u({"o": offset})


def _decode_cursor(tok: str) -> Optional[int]:
    obj = _unb64u(tok)
    if obj is None or not isinstance(obj.get("o"), int) or obj["o"] < 0:
        return None
    return obj["o"]


def _encode_sync(floor: datetime) -> str:
    return _b64u({"u": floor.astimezone(timezone.utc).isoformat()})


def _decode_sync(tok: str) -> Optional[datetime]:
    obj = _unb64u(tok)
    if obj is None or not isinstance(obj.get("u"), str):
        return None
    try:
        dt = datetime.fromisoformat(obj["u"])
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Ashby mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        path = request.url.path
        if (path not in ("/_health",) and not path.startswith("/_control")
                and _FORCED_429["count"] > 0):
            _FORCED_429["count"] -= 1
            resp = _app_error("rate_limit_exceeded", "Rate limit or concurrent limit "
                              "exceeded.", status=429)
            # Ashby pins no Retry-After (UNCONFIRMED); mock-only knob emits one so a
            # consumer's retry budget is exercised deterministically.
            resp.headers["Retry-After"] = "1"
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "ashby-mock", "run_id": str(s.run_id),
                "legal_business_name": org["legal_business_name"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    async def _read_body(request: Request) -> dict:
        raw = await request.body()
        if not raw:
            return {}
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return obj if isinstance(obj, dict) else {}

    @app.post("/{method}")
    async def rpc(request: Request, method: str):
        if not is_authed(request):
            return _unauthorized()
        # method = "<category>.<verb>"
        if "." not in method:
            return _app_error("not_found", f"Unknown endpoint /{method}", status=404)
        category, _, verb = method.partition(".")
        if category not in _dto.CATEGORIES or verb not in ("list", "info"):
            return _app_error("not_found", f"Unknown endpoint /{method}", status=404)

        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            # No provisioned org for this run → behave as an empty tenant.
            if verb == "info":
                return _app_error("not_found", "Entity not found.")
            return _ok({"results": [], "moreDataAvailable": False})

        body = await _read_body(request)

        if verb == "info":
            return await _info(s.pool, org["id"], category, body)
        return await _list(s.pool, org["id"], category, body, s.run_id)

    return app


async def _info(pool, org_pk, category: str, body: dict) -> JSONResponse:
    ent_id = body.get("id")
    if not ent_id or not isinstance(ent_id, str):
        return _app_error("invalid_request", "`id` is required.")
    try:
        ent_uuid = UUID(ent_id)
    except (ValueError, TypeError):
        return _app_error("not_found", f"No {category} with id {ent_id!r}.")
    row = await pool.fetchrow(
        "SELECT data FROM app_ashby.entities "
        "WHERE org_pk = $1 AND kind = $2 AND entity_id = $3",
        org_pk, category, ent_uuid)
    if row is None:
        return _app_error("not_found", f"No {category} with id {ent_id!r}.")
    data = row["data"]
    return _ok({"results": data if isinstance(data, dict) else json.loads(data)})


async def _list(pool, org_pk, category: str, body: dict, run_id) -> JSONResponse:
    # limit: default + max 100 (clamped, never an error for >100 — Ashby's "the
    # maximum and default value is 100").
    limit = body.get("limit")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        limit = _DEFAULT_LIMIT
    limit = min(limit, _MAX_LIMIT)

    offset = 0
    cur = body.get("cursor")
    if cur not in (None, ""):
        if not isinstance(cur, str) or (_decode_cursor(cur)) is None:
            return _app_error("invalid_cursor", "Malformed `cursor`.")
        offset = _decode_cursor(cur)

    floor: Optional[datetime] = None
    sync = body.get("syncToken")
    if sync not in (None, ""):
        if not isinstance(sync, str) or _decode_sync(sync) is None:
            return _app_error("invalid_sync_token", "Malformed `syncToken`.")
        floor = _decode_sync(sync)

    where = ["org_pk = $1", "kind = $2"]
    params: list[Any] = [org_pk, category]
    if floor is not None:
        params.append(floor)
        where.append(f"updated_at > ${len(params)}")
    where_sql = " AND ".join(where)

    total = int(await pool.fetchval(
        f"SELECT count(*) FROM app_ashby.entities WHERE {where_sql}", *params))
    params.append(limit)
    params.append(offset)
    rows = await pool.fetch(
        f"SELECT data FROM app_ashby.entities WHERE {where_sql} "
        f"ORDER BY updated_at ASC, entity_id ASC "
        f"LIMIT ${len(params)-1} OFFSET ${len(params)}",
        *params)
    results = [r["data"] if isinstance(r["data"], dict) else json.loads(r["data"])
               for r in rows]

    more = (offset + len(rows)) < total
    payload: dict[str, Any] = {"results": results, "moreDataAvailable": more}
    if more:
        payload["nextCursor"] = _encode_cursor(offset + len(rows))
    elif category in _SYNC_CAPABLE:
        # Terminal page: mint a fresh syncToken = this kind's high-water updated_at
        # (so the next incremental sync sees only entities changed after it).
        hw = await pool.fetchval(
            "SELECT max(updated_at) FROM app_ashby.entities "
            "WHERE org_pk = $1 AND kind = $2", org_pk, category)
        if hw is None:
            run = await pool.fetchrow("SELECT virtual_now FROM org.runs WHERE id = $1", run_id)
            hw = (run and run["virtual_now"]) or datetime.now(timezone.utc)
        if hw.tzinfo is None:
            hw = hw.replace(tzinfo=timezone.utc)
        payload["syncToken"] = _encode_sync(hw)
    return _ok(payload)


app = create_app()
