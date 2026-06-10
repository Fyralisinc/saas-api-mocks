"""Deel (global payroll / contractor payments) mock — FastAPI app.

Serves the REAL ``api.letsdeel.com/rest/v2/`` read surface a connector hits for
ingestion (the Fyralis flow doc's Mercury-cloned ``/contract/{id}/payments`` shape
is UNVERIFIED and DIVERGES — see the deel-fidelity-audit memory):

    GET /rest/v2/contracts              (CURSOR page {data, page:{cursor, total_rows}})
    GET /rest/v2/contracts/{id}         (single Contract, wrapped {data:{…}})
    GET /rest/v2/invoices               (HYBRID page {data, page:{offset, total_rows,
                                         items_per_page, cursor}}; the real "payments"
                                         stream — each invoice carries contract_id)

Auth is a long-lived org/personal token via ``Authorization: Bearer <token>`` (see
auth.py); the mock is single-tenant per run and accepts any non-empty Bearer token,
a missing/blank one is 401.

Pagination differs by endpoint (a real Deel wrinkle): **contracts are CURSOR-only**
(``limit`` + ``after_cursor`` → ``page:{cursor, total_rows}``); **invoices are
HYBRID** (``limit`` + ``offset`` + ``cursor`` → ``page:{offset, total_rows,
items_per_page, cursor}``). The cursor is an OPAQUE base64url offset. Money is a
decimal STRING in major units; timestamps RFC3339 ms+Z; ``status`` on invoices
defaults to **paid-only** unless ``status=all`` (a full backfill MUST pass it).

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s (Deel documents
429 + Retry-After at ~5 rps). The error envelope is ``{request:{…}, errors:[{…}]}``.
"""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.deel import dto as _dto
from spammers.deel import state as _state
from spammers.deel.auth import is_authed

_FORCED_429 = {"count": 0}
_CONTRACTS_DEFAULT_LIMIT = 50      # contracts limit default is UNCONFIRMED in docs
_INVOICES_DEFAULT_LIMIT = 25       # invoices default IS documented as 25
_MAX_LIMIT = 100
_DEFAULT_VERSION = "2026-01-01"


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _error(request: Request, status: int, message: str, *,
           code: int | None = None, path: str | None = None) -> JSONResponse:
    # Deel 4xx/5xx bodies use ``{request:{method,url,status,…}, errors:[{message,path}]}``
    # (schema ApiErrorContainer / RequestNotFoundError). Concrete field values beyond
    # method/url/status are UNCONFIRMED — the mock follows the documented schema.
    err: dict[str, Any] = {"message": message}
    if path is not None:
        err["path"] = path
    return JSONResponse(
        {"request": {"method": request.method, "url": str(request.url),
                     "status": status, "code": code if code is not None else status},
         "errors": [err]},
        status_code=status)


def _unauthorized(request: Request) -> JSONResponse:
    return _error(request, 401, "Missing or invalid access token.")


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


def _limit_param(qp, default: int) -> int | None:
    """Resolve ``limit`` to [1, _MAX_LIMIT], clamping a too-large value (no error)."""
    raw = qp.get("limit")
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        return None  # caller -> 400
    if v < 1:
        return None
    return min(v, _MAX_LIMIT)


def _parse_date(raw: str) -> Optional[datetime]:
    """Parse a ``YYYY-MM-DD`` filter value to a UTC datetime at midnight."""
    raw = raw.strip()
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Deel mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit_and_version(request: Request, call_next):
        if request.url.path.startswith("/rest/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            resp = _error(request, 429, "Too Many Requests")
            # Deel documents 429 + Retry-After (~5 rps).
            resp.headers["Retry-After"] = "1"
            resp.headers["X-Version"] = request.headers.get("X-Version") or _DEFAULT_VERSION
            return resp
        resp = await call_next(request)
        if request.url.path.startswith("/rest/"):
            # Deel resolves the API version from the X-Version header (date-based),
            # echoing the resolved version. Default the current stable baseline.
            resp.headers["X-Version"] = request.headers.get("X-Version") or _DEFAULT_VERSION
        return resp

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "deel-mock", "run_id": str(s.run_id),
                "legal_business_name": org["legal_business_name"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # --------------------------------------------------------------- contracts

    @app.get("/rest/v2/contracts")
    async def list_contracts(request: Request):
        if not is_authed(request):
            return _unauthorized(request)
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse({"data": [], "page": {"cursor": None, "total_rows": 0}})
        qp = request.query_params
        limit = _limit_param(qp, _CONTRACTS_DEFAULT_LIMIT)
        if limit is None:
            return _error(request, 400, "Invalid `limit`.", path="limit")
        # Contracts paginate CURSOR-only: `after_cursor` (opaque) → offset.
        offset = 0
        if qp.get("after_cursor"):
            offset = _decode_cursor(qp["after_cursor"])
            if offset is None:
                return _error(request, 400, "Invalid `after_cursor`.", path="after_cursor")

        clauses = ["org_pk = $1"]
        params: list[Any] = [org["id"]]
        statuses = qp.getlist("statuses[]") or qp.getlist("statuses")
        if statuses:
            params.append(statuses)
            clauses.append(f"status = ANY(${len(params)})")
        types = qp.getlist("types[]") or qp.getlist("types")
        if types:
            params.append(types)
            clauses.append(f"type = ANY(${len(params)})")
        where = " AND ".join(clauses)

        total = int(await s.pool.fetchval(
            f"SELECT count(*) FROM app_deel.contracts WHERE {where}", *params) or 0)
        rows = await s.pool.fetch(
            f"SELECT contract_id, type, title, status, worker_name, worker_email, "
            f"worker_country, client_name, job_title, comp_amount_cents, comp_currency, "
            f"comp_frequency, comp_scale, external_id, is_archived, start_date, "
            f"termination_date, created_at, updated_at "
            f"FROM app_deel.contracts WHERE {where} "
            f"ORDER BY created_at ASC, sort_key ASC, contract_id ASC "
            f"LIMIT {limit + 1} OFFSET {offset}", *params)
        has_more = len(rows) > limit
        window = [dict(r) for r in rows[:limit]]
        nxt = _encode_cursor(offset + limit) if has_more else None
        return JSONResponse({
            "data": [_dto.contract_dto(r) for r in window],
            "page": {"cursor": nxt, "total_rows": total},
        })

    @app.get("/rest/v2/contracts/{contract_id}")
    async def get_contract(request: Request, contract_id: str):
        if not is_authed(request):
            return _unauthorized(request)
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(request, 404, f"Contract {contract_id} not found.")
        row = await s.pool.fetchrow(
            "SELECT contract_id, type, title, status, worker_name, worker_email, "
            "worker_country, client_name, job_title, comp_amount_cents, comp_currency, "
            "comp_frequency, comp_scale, external_id, is_archived, start_date, "
            "termination_date, created_at, updated_at "
            "FROM app_deel.contracts WHERE org_pk = $1 AND contract_id = $2",
            org["id"], contract_id)
        if row is None:
            return _error(request, 404, f"Contract {contract_id} not found.")
        return JSONResponse({"data": _dto.contract_dto(dict(row))})

    # ---------------------------------------------------------------- invoices

    @app.get("/rest/v2/invoices")
    async def list_invoices(request: Request):
        if not is_authed(request):
            return _unauthorized(request)
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse({"data": [], "page": {
                "offset": 0, "total_rows": 0, "items_per_page": _INVOICES_DEFAULT_LIMIT,
                "cursor": None}})
        qp = request.query_params
        limit = _limit_param(qp, _INVOICES_DEFAULT_LIMIT)
        if limit is None:
            return _error(request, 400, "Invalid `limit`.", path="limit")
        # Invoices are HYBRID: explicit `offset`, OR an opaque `cursor` (offset).
        offset = 0
        if qp.get("cursor"):
            offset = _decode_cursor(qp["cursor"])
            if offset is None:
                return _error(request, 400, "Invalid `cursor`.", path="cursor")
        elif qp.get("offset"):
            try:
                offset = int(qp["offset"])
                if offset < 0:
                    raise ValueError
            except ValueError:
                return _error(request, 400, "Invalid `offset`.", path="offset")

        clauses = ["org_pk = $1"]
        params: list[Any] = [org["id"]]
        # status: "all" returns every status; anything else / absent → PAID ONLY.
        if (qp.get("status") or "").lower() != "all":
            params.append("paid")
            clauses.append(f"status = ${len(params)}")
        if qp.get("issued_from_date"):
            frm = _parse_date(qp["issued_from_date"])
            if frm is None:
                return _error(request, 400, "Invalid `issued_from_date`.",
                              path="issued_from_date")
            params.append(frm)
            clauses.append(f"issued_at >= ${len(params)}")
        if qp.get("issued_to_date"):
            to = _parse_date(qp["issued_to_date"])
            if to is None:
                return _error(request, 400, "Invalid `issued_to_date`.",
                              path="issued_to_date")
            params.append(to)
            clauses.append(f"issued_at < ${len(params)} + interval '1 day'")
        where = " AND ".join(clauses)

        total = int(await s.pool.fetchval(
            f"SELECT count(*) FROM app_deel.invoices WHERE {where}", *params) or 0)
        rows = await s.pool.fetch(
            f"SELECT invoice_id, contract_id, label, total_cents, amount_cents, "
            f"vat_cents, deel_fee_cents, currency, status, issued_at, due_date, "
            f"paid_at, created_at, is_overdue, recipient_legal_entity_id "
            f"FROM app_deel.invoices WHERE {where} "
            f"ORDER BY issued_at ASC, sort_key ASC, invoice_id ASC "
            f"LIMIT {limit + 1} OFFSET {offset}", *params)
        has_more = len(rows) > limit
        window = [dict(r) for r in rows[:limit]]
        nxt = _encode_cursor(offset + limit) if has_more else None
        return JSONResponse({
            "data": [_dto.invoice_dto(r) for r in window],
            "page": {"offset": offset, "total_rows": total,
                     "items_per_page": limit, "cursor": nxt},
        })

    return app


app = create_app()
