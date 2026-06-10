"""Brex (corporate cards + cash management) mock — FastAPI app.

Serves the REAL ``api.brex.com`` ``/v2/`` read surface a connector hits for
ingestion (the Fyralis flow doc's Mercury-cloned ``/account/{id}/transactions``
shape is UNVERIFIED and DIVERGES — see the brex-fidelity-audit memory):

    GET /v2/accounts/cash                         (CURSOR page {next_cursor, items})
    GET /v2/accounts/cash/primary                 (single CashAccount, bare)
    GET /v2/accounts/cash/{id}                     (single CashAccount, bare)
    GET /v2/accounts/card                          (BARE ARRAY of CardAccount)
    GET /v2/transactions/cash/{id}                 (CURSOR page; posted_at_start filter)
    GET /v2/transactions/card/primary              (CURSOR page; posted_at_start filter)

Auth is a user/OAuth token via ``Authorization: Bearer <token>`` (see auth.py).
The mock is single-tenant per run and accepts any non-empty Bearer token; a
missing/blank one is 401.

Pagination is an OPAQUE cursor (the mock encodes a base64url offset, matching
Brex's own ``eyJsaW1pdCI6Miwib2Zmc2V0IjoyfQ==`` = ``{"limit":2,"offset":2}``
cursors); ``limit`` default **100**, max **1000** (clamped). ``next_cursor`` is
present-with-value while more pages remain, ``null`` on the last page. The
``posted_at_start`` filter (date-time) bounds the window below; there is NO
default window (a no-filter probe returns ALL history). Money is SIGNED INTEGER
CENTS, emitted verbatim; transaction dates are DATE-only ``YYYY-MM-DD``.

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. Brex
documents 429 but publishes no Retry-After guarantee / error-body schema
(UNCONFIRMED); the mock's ``{errors:{type,message}}`` envelope follows the
documented shape (developer.brex.com/docs/error_codes) and the forced-429
Retry-After is a mock-only convenience flagged UNCONFIRMED.
"""
from __future__ import annotations

import base64
import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.brex import dto as _dto
from spammers.brex import state as _state
from spammers.brex.auth import is_authed

_FORCED_429 = {"count": 0}
_DEFAULT_LIMIT = 100
_MAX_LIMIT = 1000


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _error(status: int, type_: str, message: str, code: str | None = None) -> JSONResponse:
    # developer.brex.com/docs/error_codes: a failed request returns an `errors`
    # object with `type`, `message`, optional `code`. (The exact JSON example is
    # UNCONFIRMED — the OpenAPI spec attaches no body schema to its 4xx responses.)
    err: dict[str, Any] = {"type": type_, "message": message}
    if code is not None:
        err["code"] = code
    return JSONResponse({"errors": err}, status_code=status)


def _unauthorized() -> JSONResponse:
    return _error(401, "UNAUTHENTICATED", "Missing or invalid access token.")


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


def _limit_param(qp) -> int | None:
    """Resolve ``limit`` to [1, _MAX_LIMIT], default 100, clamping a too-large value."""
    raw = qp.get("limit")
    if raw is None or raw == "":
        return _DEFAULT_LIMIT
    try:
        v = int(raw)
    except ValueError:
        return None  # caller -> 400
    if v < 1:
        return None
    return min(v, _MAX_LIMIT)


def _parse_dt(raw: str) -> Optional[datetime]:
    """Parse a ``posted_at_start`` date-time (RFC3339) or a bare ``YYYY-MM-DD``."""
    raw = raw.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Brex mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v2/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            resp = _error(429, "RATE_LIMITED", "Too Many Requests")
            # Brex documents 429 but does NOT publish a Retry-After guarantee
            # (UNCONFIRMED). Mock-only: emit one so a retry budget is exercised.
            resp.headers["Retry-After"] = "1"
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "brex-mock", "run_id": str(s.run_id),
                "legal_business_name": org["legal_business_name"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ----------------------------------------------------------- cash accounts

    @app.get("/v2/accounts/cash")
    async def list_cash_accounts(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse({"next_cursor": None, "items": []})
        qp = request.query_params
        limit = _limit_param(qp)
        if limit is None:
            return _error(400, "VALIDATION_ERROR", "Invalid `limit`.")
        offset = 0
        if qp.get("cursor"):
            offset = _decode_cursor(qp["cursor"])
            if offset is None:
                return _error(400, "VALIDATION_ERROR", "Invalid `cursor`.")

        rows = [dict(r) for r in await s.pool.fetch(
            "SELECT account_id, kind, name, status, account_number, routing_number, "
            "currency, current_balance_cents, available_balance_cents, is_primary "
            "FROM app_brex.accounts WHERE org_pk = $1 AND kind = 'cash' "
            "ORDER BY sort_key ASC, account_id ASC", org["id"])]
        window = rows[offset:offset + limit]
        nxt = _encode_cursor(offset + limit) if offset + limit < len(rows) and window else None
        return JSONResponse({
            "next_cursor": nxt,
            "items": [_dto.cash_account_dto(r) for r in window],
        })

    async def _cash_account(pool, org_pk, account_id: str):
        return await pool.fetchrow(
            "SELECT account_id, kind, name, status, account_number, routing_number, "
            "currency, current_balance_cents, available_balance_cents, is_primary "
            "FROM app_brex.accounts WHERE org_pk = $1 AND kind = 'cash' AND account_id = $2",
            org_pk, account_id)

    # NOTE: /primary is registered BEFORE /{account_id} so "primary" is not
    # captured as an account id (FastAPI matches routes in declaration order).
    @app.get("/v2/accounts/cash/primary")
    async def get_primary_cash_account(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, "NOT_FOUND", "No primary cash account.")
        row = await s.pool.fetchrow(
            "SELECT account_id, kind, name, status, account_number, routing_number, "
            "currency, current_balance_cents, available_balance_cents, is_primary "
            "FROM app_brex.accounts WHERE org_pk = $1 AND kind = 'cash' AND is_primary "
            "ORDER BY sort_key ASC LIMIT 1", org["id"])
        if row is None:
            return _error(404, "NOT_FOUND", "No primary cash account.")
        return JSONResponse(_dto.cash_account_dto(dict(row)))

    @app.get("/v2/accounts/cash/{account_id}")
    async def get_cash_account(request: Request, account_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, "NOT_FOUND", f"Cash account {account_id} not found.")
        row = await _cash_account(s.pool, org["id"], account_id)
        if row is None:
            return _error(404, "NOT_FOUND", f"Cash account {account_id} not found.")
        return JSONResponse(_dto.cash_account_dto(dict(row)))

    # ----------------------------------------------------------- card accounts

    @app.get("/v2/accounts/card")
    async def list_card_accounts(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse([])  # BARE ARRAY, not a page envelope
        rows = await s.pool.fetch(
            "SELECT account_id, kind, status, currency, current_balance_cents, "
            "available_balance_cents, account_limit_cents, statement_start, statement_end "
            "FROM app_brex.accounts WHERE org_pk = $1 AND kind = 'card' "
            "ORDER BY sort_key ASC, account_id ASC", org["id"])
        return JSONResponse([_dto.card_account_dto(dict(r)) for r in rows])

    # ------------------------------------------------------------ transactions

    async def _page_transactions(pool, request: Request, account_pk, dto_fn) -> JSONResponse:
        qp = request.query_params
        limit = _limit_param(qp)
        if limit is None:
            return _error(400, "VALIDATION_ERROR", "Invalid `limit`.")
        offset = 0
        if qp.get("cursor"):
            offset = _decode_cursor(qp["cursor"])
            if offset is None:
                return _error(400, "VALIDATION_ERROR", "Invalid `cursor`.")
        clauses = ["account_pk = $1"]
        params: list[Any] = [account_pk]
        if qp.get("posted_at_start"):
            start = _parse_dt(qp["posted_at_start"])
            if start is None:
                return _error(400, "VALIDATION_ERROR", "Invalid `posted_at_start`.")
            params.append(start)
            clauses.append(f"posted_at >= ${len(params)}")
        where = " AND ".join(clauses)

        # Fetch one extra row to decide has_more without a separate count query.
        params.append(limit + 1)
        params.append(offset)
        rows = await pool.fetch(
            f"SELECT txn_id, description, amount_cents, currency, txn_type, "
            f"initiated_at, posted_at, transfer_id, card_id, merchant_raw_descriptor, "
            f"merchant_mcc, merchant_country, expense_id "
            f"FROM app_brex.transactions WHERE {where} "
            f"ORDER BY posted_at ASC, sort_key ASC "
            f"LIMIT ${len(params)-1} OFFSET ${len(params)}", *params)
        has_more = len(rows) > limit
        window = [dict(r) for r in rows[:limit]]
        nxt = _encode_cursor(offset + limit) if has_more else None
        return JSONResponse({
            "next_cursor": nxt,
            "items": [dto_fn(r) for r in window],
        })

    @app.get("/v2/transactions/cash/{account_id}")
    async def list_cash_transactions(request: Request, account_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, "NOT_FOUND", f"Cash account {account_id} not found.")
        acct = await _cash_account(s.pool, org["id"], account_id)
        if acct is None:
            return _error(404, "NOT_FOUND", f"Cash account {account_id} not found.")
        acct_pk = await s.pool.fetchval(
            "SELECT id FROM app_brex.accounts WHERE org_pk = $1 AND account_id = $2",
            org["id"], account_id)
        return await _page_transactions(s.pool, request, acct_pk, _dto.cash_transaction_dto)

    @app.get("/v2/transactions/card/primary")
    async def list_primary_card_transactions(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, "NOT_FOUND", "No primary card account.")
        acct_pk = await s.pool.fetchval(
            "SELECT id FROM app_brex.accounts WHERE org_pk = $1 AND kind = 'card' AND is_primary "
            "ORDER BY sort_key ASC LIMIT 1", org["id"])
        if acct_pk is None:
            return _error(404, "NOT_FOUND", "No primary card account.")
        return await _page_transactions(s.pool, request, acct_pk, _dto.card_transaction_dto)

    return app


app = create_app()
