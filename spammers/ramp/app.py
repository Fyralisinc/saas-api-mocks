"""Ramp (corporate cards + bill-pay + reimbursements) mock — FastAPI app.

Serves the REAL ``api.ramp.com`` ``/developer/v1`` read surface a connector hits
for ingestion (the Fyralis flow doc's QuickBooks-Online SQL-query clone —
``GET /v3/company/{businessId}/query?query=SELECT…STARTPOSITION…`` — is
UNVERIFIED and DIVERGES ENTIRELY; see the ramp-fidelity-audit memory):

    POST /developer/v1/token                       (OAuth client-credentials mint)
    GET  /developer/v1/transactions                (KEYSET page {data, page:{next}})
    GET  /developer/v1/transactions/{id}           (single Transaction, bare)
    GET  /developer/v1/reimbursements              (KEYSET page {data, page:{next}})
    GET  /developer/v1/cards                        (KEYSET page {data, page:{next}})
    GET  /developer/v1/users                        (KEYSET page {data, page:{next}})

Auth: an OAuth 2.0 access token via ``Authorization: Bearer <ramp_business_tok_…>``
(minted at the token endpoint). The mock is single-tenant per run and accepts any
non-empty Bearer; a missing/blank one is 401.

Pagination is KEYSET: ``page.next`` is a FULL URL embedding ``start=<id of the
last entity on this page>`` (a bare wire id), and ``null`` at EOF. ``page_size``
default **20**, max **100** (clamped). Money is DUAL — the top-level ``amount`` is
dollars, nested ``CurrencyAmount`` fields are integer cents.

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. Ramp's real
rate limit is 200 req/10s and the docs publish **NO Retry-After / X-RateLimit-***
headers — so the mock's forced 429 carries NONE either (a real divergence from
the Brex/QBO archetype, which Fyralis assumes; honoured, not softened). Every
response carries an ``x-trace-id`` header (Ramp's debug id).
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.ramp import dto as _dto
from spammers.ramp import state as _state
from spammers.ramp.auth import is_authed

_FORCED_429 = {"count": 0}
_DEFAULT_PAGE_SIZE = 20
_MAX_PAGE_SIZE = 100
_TRACE = "trace_0000000000000000"


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _trace_headers() -> dict[str, str]:
    # Ramp returns an x-trace-id on every response (debugging). One per request.
    return {"x-trace-id": "trace_" + secrets.token_hex(8)}


def _error(status: int, error_code: str, message: str,
           additional_info: Any = None) -> JSONResponse:
    # Ramp's documented error fields: `error_v2` {error_code, additional_info} +
    # a top-level human `message`. (The exact JSON nesting literal is UNCONFIRMED —
    # the docs render it only as a code block; this follows the documented fields.)
    body: dict[str, Any] = {
        "error_v2": {"error_code": error_code, "additional_info": additional_info},
        "message": message,
    }
    return JSONResponse(body, status_code=status, headers=_trace_headers())


def _unauthorized() -> JSONResponse:
    return _error(401, "unauthorized", "Unauthorized – Invalid or missing API token.")


def _page_size_param(qp) -> int | None:
    """Resolve ``page_size`` to [2, _MAX_PAGE_SIZE], default 20, clamping too-large."""
    raw = qp.get("page_size")
    if raw is None or raw == "":
        return _DEFAULT_PAGE_SIZE
    try:
        v = int(raw)
    except ValueError:
        return None  # caller -> 400
    if v < 1:
        return None
    return min(v, _MAX_PAGE_SIZE)


def _next_url(request: Request, last_id: str, page_size: int) -> str:
    """Build the FULL ``page.next`` URL: the current URL with ``start=<last_id>``
    (and a normalised ``page_size``), preserving all other query params — exactly
    what Ramp returns (``<BASE_URL>?<new_params>``)."""
    parts = urlsplit(str(request.url))
    params = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
              if k not in ("start", "page_size")]
    params.append(("page_size", str(page_size)))
    params.append(("start", last_id))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(params), ""))


def create_app() -> FastAPI:
    app = FastAPI(title="Ramp mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/developer/v1/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            # Ramp's real 429 carries NO Retry-After / X-RateLimit-* header
            # (docs prescribe only client-side exponential backoff). The mock
            # emits none either — faithfully unlike the Brex/QBO archetype.
            return _error(429, "rate_limit_exceeded", "Too Many Requests")
        resp = await call_next(request)
        resp.headers.setdefault("x-trace-id", _TRACE)
        return resp

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "ramp-mock", "run_id": str(s.run_id),
                "legal_business_name": org["legal_business_name"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # -------------------------------------------------------------- OAuth token

    @app.post("/developer/v1/token")
    async def mint_token(request: Request):
        """OAuth 2.0 token endpoint — client-credentials grant.

        Client auth is HTTP Basic ``base64(client_id:client_secret)`` OR the
        creds in the request body. Returns ``{access_token, token_type:"Bearer",
        expires_in, scope}`` with the seed-stable token. Single-tenant: any
        non-empty client creds mint a token; missing → 401 ``invalid_client``."""
        client_id = client_secret = None
        h = request.headers.get("authorization") or request.headers.get("Authorization")
        if h and h.strip().lower().startswith("basic "):
            try:
                decoded = base64.b64decode(h.strip()[6:]).decode("utf-8")
                client_id, _, client_secret = decoded.partition(":")
            except (ValueError, binascii.Error):
                client_id = client_secret = None
        raw = await request.body()
        body: dict[str, Any] = {}
        if raw:
            try:
                body = json.loads(raw)
            except (ValueError, json.JSONDecodeError):
                body = dict(parse_qsl(raw.decode("utf-8", "ignore")))
        grant_type = body.get("grant_type")
        scope = body.get("scope") or "transactions:read reimbursements:read cards:read users:read"
        if not client_id:
            client_id = body.get("client_id")
            client_secret = body.get("client_secret")
        if grant_type and grant_type != "client_credentials":
            return _error(400, "invalid_request",
                          f"unsupported grant_type {grant_type!r}")
        if not (client_id and client_secret):
            return _error(401, "invalid_client", "Client authentication failed.")
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        token = org["access_token"] if org else "ramp_business_tok_" + secrets.token_hex(16)
        return JSONResponse({
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 864000,  # client-credentials tokens live 10 days
            "scope": scope,
        }, headers=_trace_headers())

    # ------------------------------------------------ generic keyset list helper

    async def _keyset_list(request: Request, table: str, id_col: str, dto_fn,
                           cols: str, extra_where: Optional[list] = None,
                           extra_params: Optional[list] = None) -> JSONResponse:
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse({"data": [], "page": {"next": None}},
                                headers=_trace_headers())
        qp = request.query_params
        page_size = _page_size_param(qp)
        if page_size is None:
            return _error(400, "invalid_request", "Invalid `page_size`.")

        clauses = ["org_pk = $1"]
        params: list[Any] = [org["id"]]
        if extra_where:
            for clause, val in zip(extra_where, extra_params or []):
                params.append(val)
                clauses.append(clause.replace("$$", f"${len(params)}"))

        start = qp.get("start")
        if start:
            start_sort = await s.pool.fetchval(
                f"SELECT sort_key FROM {table} WHERE org_pk = $1 AND {id_col} = $2",
                org["id"], start)
            if start_sort is None:
                return _error(400, "invalid_request",
                              "Invalid `start` cursor (unknown entity id).")
            params.append(start_sort)
            clauses.append(f"sort_key > ${len(params)}")
        where = " AND ".join(clauses)

        params.append(page_size + 1)  # fetch one extra to decide has_more
        rows = await s.pool.fetch(
            f"SELECT {cols} FROM {table} WHERE {where} "
            f"ORDER BY sort_key ASC, {id_col} ASC LIMIT ${len(params)}", *params)
        has_more = len(rows) > page_size
        window = [dict(r) for r in rows[:page_size]]
        nxt = None
        if has_more and window:
            nxt = _next_url(request, window[-1][id_col], page_size)
        return JSONResponse({
            "data": [dto_fn(r) for r in window],
            "page": {"next": nxt},
        }, headers=_trace_headers())

    # ------------------------------------------------------------- transactions

    _TXN_COLS = ("txn_id, amount_cents, currency_code, state, sync_status, card_id, "
                 "card_present, user_id, cardholder_name, merchant_id, merchant_name, "
                 "merchant_category_code, sk_category_id, sk_category_name, memo, entity_id, "
                 "user_transaction_time, accounting_date, settlement_date, synced_at, sort_key")

    @app.get("/developer/v1/transactions")
    async def list_transactions(request: Request):
        qp = request.query_params
        extra_where, extra_params = [], []
        state_f = qp.get("state")
        if state_f and state_f != "ALL":
            extra_where.append("state = $$"); extra_params.append(state_f)
        if qp.get("from_date"):
            dt = _parse_dt(qp["from_date"])
            if dt is None:
                return _error(400, "invalid_request", "Invalid `from_date`.")
            extra_where.append("user_transaction_time >= $$"); extra_params.append(dt)
        if qp.get("to_date"):
            dt = _parse_dt(qp["to_date"])
            if dt is None:
                return _error(400, "invalid_request", "Invalid `to_date`.")
            extra_where.append("user_transaction_time <= $$"); extra_params.append(dt)
        return await _keyset_list(request, "app_ramp.transactions", "txn_id",
                                  _dto.transaction_dto, _TXN_COLS,
                                  extra_where, extra_params)

    @app.get("/developer/v1/transactions/{transaction_id}")
    async def get_transaction(request: Request, transaction_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, "not_found", f"transaction {transaction_id} not found.")
        row = await s.pool.fetchrow(
            f"SELECT {_TXN_COLS} FROM app_ramp.transactions "
            f"WHERE org_pk = $1 AND txn_id = $2", org["id"], transaction_id)
        if row is None:
            return _error(404, "not_found", f"transaction {transaction_id} not found.")
        # The single-read returns the BARE Transaction object (NOT wrapped in {data}).
        return JSONResponse(_dto.transaction_dto(dict(row)), headers=_trace_headers())

    # ----------------------------------------------------------- reimbursements

    _REIMB_COLS = ("reimb_id, amount_cents, currency, state, type, direction, user_id, "
                   "user_email, user_full_name, merchant, merchant_id, transaction_date, "
                   "sync_status, memo, created_at, updated_at, submitted_at, approved_at, "
                   "synced_at, sort_key")

    @app.get("/developer/v1/reimbursements")
    async def list_reimbursements(request: Request):
        qp = request.query_params
        extra_where, extra_params = [], []
        state_f = qp.get("state")
        if state_f and state_f != "ALL":
            extra_where.append("state = $$"); extra_params.append(state_f)
        return await _keyset_list(request, "app_ramp.reimbursements", "reimb_id",
                                  _dto.reimbursement_dto, _REIMB_COLS,
                                  extra_where, extra_params)

    # ------------------------------------------------------------------- cards

    _CARD_COLS = ("card_id, display_name, last_four, cardholder_id, cardholder_name, "
                  "card_program_id, entity_id, expiration, is_physical, state, created_at, "
                  "sort_key")

    @app.get("/developer/v1/cards")
    async def list_cards(request: Request):
        return await _keyset_list(request, "app_ramp.cards", "card_id",
                                  _dto.card_dto, _CARD_COLS)

    # ------------------------------------------------------------------- users

    _USER_COLS = ("user_id, first_name, last_name, email, role, status, department_id, "
                  "location_id, manager_id, is_manager, employee_id, business_id, entity_id, "
                  "sort_key")

    @app.get("/developer/v1/users")
    async def list_users(request: Request):
        return await _keyset_list(request, "app_ramp.users", "user_id",
                                  _dto.user_dto, _USER_COLS)

    return app


def _parse_dt(raw: str) -> Optional[datetime]:
    """Parse an ISO-8601 ``from_date``/``to_date`` (with ``Z`` or offset) or a bare date."""
    raw = raw.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


app = create_app()
