"""Mercury (business banking) mock — FastAPI app.

The surface a connector hits for ingestion is the accounts list, the per-account
transactions list, and the single-resource reads used for fetch-on-notify:

    GET /api/v1/accounts                              (UUID-cursor page, order asc)
    GET /api/v1/account/{accountId}                   (single account, bare object)
    GET /api/v1/account/{accountId}/transactions      (offset page, 30-day default window)
    GET /api/v1/account/{accountId}/transaction/{transactionId}   (single txn, bare)

Auth is the org API token via ``Authorization: Bearer <token>`` OR
``Authorization: Basic base64(<token>:)`` (token-as-username, empty password) —
see auth.py. The mock is single-tenant per run and accepts any non-empty token.

Two pagination schemes, faithful to the real API:
  * **/accounts** — cursor pagination by account UUID (``start_after``/``end_before``
    exclusive), ``order`` default **asc**, ``limit`` default/max **1000**. Envelope
    ``{"accounts":[…], "page":{"nextPage":…, "previousPage":…}}``.
  * **/transactions** — OFFSET pagination (``limit``/``offset``), ``order`` default
    **desc** (newest-first), ``status`` filter, and a ``start``/``end`` date window
    that **DEFAULTS to the last 30 days** (Mercury's documented default — a probe
    with no ``start`` only sees recent activity). Envelope
    ``{"total":N, "transactions":[…]}`` (``total`` = full match count, not the page).

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. NOTE: Mercury
publishes NO rate-limit contract (no 429 body / Retry-After / X-RateLimit-* —
UNCONFIRMED), and NO error-body shape; the mock's error envelope
``{"errors":{"message":…}}`` and the forced-429 path are mock choices flagged
UNCONFIRMED, not on the validated read path.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.mercury import dto as _dto
from spammers.mercury import state as _state
from spammers.mercury.auth import is_authed

_FORCED_429 = {"count": 0}
_DEFAULT_LIMIT = 1000
_MAX_LIMIT = 1000


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _error(status: int, message: str) -> JSONResponse:
    # Mercury publishes no error-body schema (UNCONFIRMED); this is the mock's
    # chosen shape. Only the HTTP status is contractually meaningful.
    return JSONResponse({"errors": {"message": message}}, status_code=status)


def _unauthorized() -> JSONResponse:
    return _error(401, "Unauthorized")


def _int_param(qp, name: str) -> Optional[int]:
    raw = qp.get(name)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _parse_when(raw: str, *, end: bool) -> Optional[datetime]:
    """Parse a ``start``/``end`` value: ``YYYY-MM-DD`` or an ISO-8601 string.

    A bare date is widened to the whole day in UTC: start-of-day for ``start``,
    end-of-day for ``end`` (so the window is inclusive of that calendar date).
    """
    raw = raw.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            d = datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
            return d + timedelta(hours=23, minutes=59, seconds=59) if end else d
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Mercury mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/api/v1/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            resp = _error(429, "Too Many Requests")
            # Mercury does NOT document Retry-After on its own API (only treats a
            # 429 from YOUR webhook endpoint as retryable). Mock-only knob: emit a
            # Retry-After so a consumer's retry budget is exercised deterministically.
            resp.headers["Retry-After"] = "1"
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "mercury-mock",
                "run_id": str(s.run_id),
                "legal_business_name": org["legal_business_name"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ------------------------------------------------------------------ accounts

    @app.get("/api/v1/accounts")
    async def list_accounts(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return JSONResponse({"accounts": [], "page": {}})

        qp = request.query_params
        order = (qp.get("order") or "asc").strip().lower()
        if order not in ("asc", "desc"):
            return _error(400, "Invalid `order` (expected asc|desc)")
        limit = _int_param(qp, "limit")
        if limit is None:
            limit = _DEFAULT_LIMIT
        if limit < 1 or limit > _MAX_LIMIT:
            return _error(400, "Invalid `limit` (1..1000)")
        start_after = qp.get("start_after")
        end_before = qp.get("end_before")
        if start_after and end_before:
            return _error(400, "`start_after` and `end_before` are mutually exclusive")

        direction = "ASC" if order == "asc" else "DESC"
        rows = await s.pool.fetch(
            f"SELECT id, account_id, name, nickname, account_number, routing_number, "
            f"status, type, kind, available_balance_cents, current_balance_cents, "
            f"legal_business_name, dashboard_link, can_receive_transactions, sort_key, "
            f"created_at FROM app_mercury.accounts WHERE org_pk = $1 "
            f"ORDER BY sort_key {direction}, account_id {direction}",
            org["id"])
        ordered = [dict(r) for r in rows]

        # UUID-cursor pagination (exclusive). The cursor is an account_id.
        def _idx_of(cursor: str) -> Optional[int]:
            for i, r in enumerate(ordered):
                if str(r["account_id"]) == cursor:
                    return i
            return None

        start = 0
        if start_after:
            i = _idx_of(start_after)
            start = (i + 1) if i is not None else 0
        elif end_before:
            i = _idx_of(end_before)
            ordered = ordered[:i] if i is not None else ordered

        window = ordered[start:start + limit]
        page: dict[str, Any] = {}
        if start + limit < len(ordered) and window:
            page["nextPage"] = str(window[-1]["account_id"])
        if start > 0 and window:
            page["previousPage"] = str(window[0]["account_id"])
        return JSONResponse(
            {"accounts": [_dto.account_dto(r) for r in window], "page": page})

    async def _account_row(pool, org_pk, account_id: str):
        try:
            UUID(account_id)
        except ValueError:
            return None
        return await pool.fetchrow(
            "SELECT id, account_id, name, nickname, account_number, routing_number, "
            "status, type, kind, available_balance_cents, current_balance_cents, "
            "legal_business_name, dashboard_link, can_receive_transactions, created_at "
            "FROM app_mercury.accounts WHERE org_pk = $1 AND account_id = $2",
            org_pk, UUID(account_id))

    @app.get("/api/v1/account/{account_id}")
    async def get_account(request: Request, account_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, f"`accountId` {account_id} not found")
        row = await _account_row(s.pool, org["id"], account_id)
        if row is None:
            return _error(404, f"`accountId` {account_id} not found")
        return JSONResponse(_dto.account_dto(dict(row)))

    # -------------------------------------------------------------- transactions

    @app.get("/api/v1/account/{account_id}/transactions")
    async def list_transactions(request: Request, account_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, f"`accountId` {account_id} not found")
        acct = await _account_row(s.pool, org["id"], account_id)
        if acct is None:
            return _error(404, f"`accountId` {account_id} not found")

        qp = request.query_params
        order = (qp.get("order") or "desc").strip().lower()
        if order not in ("asc", "desc"):
            return _error(400, "Invalid `order` (expected asc|desc)")
        limit = _int_param(qp, "limit")
        if limit is None:
            limit = _DEFAULT_LIMIT
        if limit < 1 or limit > _MAX_LIMIT:
            return _error(400, "Invalid `limit` (1..1000)")
        offset = _int_param(qp, "offset") or 0
        if offset < 0:
            return _error(400, "Invalid `offset` (>= 0)")
        status_filter = qp.get("status")
        if status_filter and status_filter not in _dto.TXN_STATUSES:
            return _error(400, f"Invalid `status` {status_filter!r}")

        # Default window: the last 30 days relative to the run's virtual clock.
        run = await s.pool.fetchrow("SELECT virtual_now FROM org.runs WHERE id = $1", s.run_id)
        now = (run and run["virtual_now"]) or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        start_raw, end_raw = qp.get("start"), qp.get("end")
        start_dt = _parse_when(start_raw, end=False) if start_raw else (now - timedelta(days=30))
        end_dt = _parse_when(end_raw, end=True) if end_raw else now
        if start_dt is None or end_dt is None:
            return _error(400, "Invalid `start` or `end` (YYYY-MM-DD or ISO-8601)")

        clauses = ["account_pk = $1", "created_at >= $2", "created_at <= $3"]
        params: list[Any] = [acct["id"], start_dt, end_dt]
        if status_filter:
            params.append(status_filter)
            clauses.append(f"status = ${len(params)}")
        where = " AND ".join(clauses)

        total = int(await s.pool.fetchval(
            f"SELECT count(*) FROM app_mercury.transactions WHERE {where}", *params))
        direction = "ASC" if order == "asc" else "DESC"
        params.append(limit)
        params.append(offset)
        rows = await s.pool.fetch(
            f"SELECT id, txn_id, amount_cents, status, kind, counterparty_id, "
            f"counterparty_name, counterparty_nickname, note, external_memo, "
            f"bank_description, reason_for_failure, check_number, dashboard_link, "
            f"created_at, posted_at, estimated_delivery_date, failed_at "
            f"FROM app_mercury.transactions WHERE {where} "
            f"ORDER BY created_at {direction}, txn_id {direction} "
            f"LIMIT ${len(params)-1} OFFSET ${len(params)}",
            *params)
        acct_uuid = acct["account_id"]
        return JSONResponse({
            "total": total,
            "transactions": [_dto.transaction_dto({**dict(r), "account_id": acct_uuid})
                             for r in rows],
        })

    @app.get("/api/v1/account/{account_id}/transaction/{transaction_id}")
    async def get_transaction(request: Request, account_id: str, transaction_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        if org is None:
            return _error(404, f"`accountId` {account_id} not found")
        acct = await _account_row(s.pool, org["id"], account_id)
        if acct is None:
            return _error(404, f"`accountId` {account_id} not found")
        try:
            txn_uuid = UUID(transaction_id)
        except ValueError:
            return _error(404, f"`transactionId` {transaction_id} not found")
        row = await s.pool.fetchrow(
            "SELECT t.id, t.txn_id, a.account_id AS account_id, t.amount_cents, t.status, "
            "t.kind, t.counterparty_id, t.counterparty_name, t.counterparty_nickname, "
            "t.note, t.external_memo, t.bank_description, t.reason_for_failure, "
            "t.check_number, t.dashboard_link, t.created_at, t.posted_at, "
            "t.estimated_delivery_date, t.failed_at "
            "FROM app_mercury.transactions t JOIN app_mercury.accounts a ON a.id = t.account_pk "
            "WHERE t.account_pk = $1 AND t.txn_id = $2",
            acct["id"], txn_uuid)
        if row is None:
            return _error(404, f"`transactionId` {transaction_id} not found")
        return JSONResponse(_dto.transaction_dto(dict(row)))

    return app


app = create_app()
