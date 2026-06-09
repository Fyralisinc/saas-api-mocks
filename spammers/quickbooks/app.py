"""QuickBooks Online (QBO) Accounting API v3 mock — FastAPI app.

The real surface a connector hits is the **query** endpoint and CompanyInfo:

    GET /v3/company/{realmId}/query?query=<SQL>&minorversion=75
    GET /v3/company/{realmId}/companyinfo/{realmId}

Fyralis ingests the four transactional entities Invoice / Bill / BillPayment /
Payment via the query endpoint; we project the corpus finance data into those
QBO shapes at read time (see dto.py). Auth is OAuth ``Authorization: Bearer``;
errors are QBO ``Fault`` envelopes; rate limiting is HTTP 429 ThrottleExceeded.

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s (not part of
the real API; mirrors the notion/github controls).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.quickbooks import dto as _dto
from spammers.quickbooks import state as _state
from spammers.quickbooks.auth import is_authed
from spammers.quickbooks.query import parse_query

_FORCED_429 = {"count": 0}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _fault(status: int, message: str, *, code: str, fault_type: str,
           detail: str = "") -> JSONResponse:
    err: dict[str, Any] = {"Message": message, "code": code}
    if detail:
        err["Detail"] = detail
    return JSONResponse(
        {"Fault": {"Error": [err], "type": fault_type}, "time": _now_iso()},
        status_code=status,
    )


def _unauthorized() -> JSONResponse:
    return _fault(401, "AuthenticationFailed", code="3200",
                  fault_type="AUTHENTICATION", detail="Token expired or invalid")


def _envelope(entity: str, items: list[dict], *, start_position: int) -> dict:
    return {
        "QueryResponse": {
            entity: items,
            "startPosition": start_position,
            "maxResults": len(items),
        },
        "time": _now_iso(),
    }


# ---- source fetchers (project corpus finance rows -> QBO entities) ----------

_PURCHASE_COLS = """p.purchase_id, p.txn_date, p.amount_cents, p.created_at,
           v.vendor_id, v.display_name AS vendor_name,
           ea.account_number AS expense_acct_num, ea.name AS expense_acct_name,
           pa.account_number AS pay_acct_num, pa.name AS pay_acct_name"""
_PURCHASE_FROM = """FROM app_quickbooks.purchases p
      LEFT JOIN app_quickbooks.vendors  v  ON v.id  = p.vendor_pk
      LEFT JOIN app_quickbooks.accounts ea ON ea.id = p.expense_account_pk
      LEFT JOIN app_quickbooks.accounts pa ON pa.id = p.payment_account_pk
     WHERE p.company_pk = $1"""
_PURCHASE_ORDER = "ORDER BY p.created_at, p.purchase_id"

_GRANT_COLS = """d.deposit_id, d.txn_date, d.amount_cents, d.created_at, d.lead,
           da.account_number AS dep_acct_num, da.name AS dep_acct_name"""
_GRANT_FROM = """FROM app_quickbooks.deposits d
      LEFT JOIN app_quickbooks.accounts da ON da.id = d.deposit_to_account_pk
     WHERE d.company_pk = $1 AND d.round_kind = 'grant'"""
_GRANT_ORDER = "ORDER BY d.created_at, d.deposit_id"

# entity -> (cols, from, order, updated_col, id_col, id_prefix, DTO)
_ENTITY_SOURCES = {
    "Bill":        (_PURCHASE_COLS, _PURCHASE_FROM, _PURCHASE_ORDER, "p.created_at",
                    "p.purchase_id", "", _dto.bill_dto),
    "BillPayment": (_PURCHASE_COLS, _PURCHASE_FROM, _PURCHASE_ORDER, "p.created_at",
                    "p.purchase_id", "BP-", _dto.bill_payment_dto),
    "Invoice":     (_GRANT_COLS, _GRANT_FROM, _GRANT_ORDER, "d.created_at",
                    "d.deposit_id", "", _dto.invoice_dto),
    "Payment":     (_GRANT_COLS, _GRANT_FROM, _GRANT_ORDER, "d.created_at",
                    "d.deposit_id", "P-", _dto.payment_dto),
}


def _where(entity: str, *, updated_after, id_equals, params: list) -> str:
    _, _, _, ucol, id_col, prefix, _ = _ENTITY_SOURCES[entity]
    clauses = []
    if id_equals is not None:
        src_id = (id_equals[len(prefix):]
                  if prefix and id_equals.startswith(prefix) else id_equals)
        params.append(src_id)
        clauses.append(f"{id_col} = ${len(params)}")
    if updated_after is not None:
        params.append(updated_after)
        clauses.append(f"{ucol} > ${len(params)}")
    return (" AND " + " AND ".join(clauses)) if clauses else ""


async def _fetch_entity(pool, company_pk, entity: str, *, updated_after, id_equals,
                        offset, limit):
    cols, frm, order, *_rest = _ENTITY_SOURCES[entity]
    dto_fn = _ENTITY_SOURCES[entity][-1]
    params: list = [company_pk]
    flt = _where(entity, updated_after=updated_after, id_equals=id_equals, params=params)
    params.append(limit)
    params.append(offset)
    sql = (f"SELECT {cols} {frm}{flt} {order} "
           f"LIMIT ${len(params)-1} OFFSET ${len(params)}")
    rows = await pool.fetch(sql, *params)
    return [dto_fn(dict(r)) for r in rows]


async def _count_entity(pool, company_pk, entity: str, *, updated_after) -> int:
    cols, frm, order, *_ = _ENTITY_SOURCES[entity]
    params: list = [company_pk]
    flt = _where(entity, updated_after=updated_after, id_equals=None, params=params)
    sql = f"SELECT count(*) {frm}{flt}"
    return int(await pool.fetchval(sql, *params))


def create_app() -> FastAPI:
    app = FastAPI(title="QuickBooks Online mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v3/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            return _fault(429, "ThrottleExceeded", code="003001",
                          fault_type="THROTTLE",
                          detail="The request limit for this resource has been reached.")
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        realm_id = await _state.realm_id_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "quickbooks-mock",
                "run_id": str(s.run_id), "realm_id": realm_id}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    @app.get("/v3/company/{realm_id}/companyinfo/{realm_id_again}")
    async def company_info(request: Request, realm_id: str, realm_id_again: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        company_pk = await _state.company_pk_for_realm(s.pool, s.run_id, realm_id)
        if company_pk is None:
            return _fault(404, "Object Not Found", code="610", fault_type="ValidationFault",
                          detail=f"company {realm_id} not found")
        row = await s.pool.fetchrow(
            "SELECT realm_id, company_name, legal_name, country, currency, "
            "fiscal_year_start, created_at FROM app_quickbooks.companies WHERE id=$1",
            company_pk)
        return JSONResponse({"CompanyInfo": _dto.company_info_dto(dict(row)),
                             "time": _now_iso()})

    @app.get("/v3/company/{realm_id}/query")
    async def query(request: Request, realm_id: str):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        company_pk = await _state.company_pk_for_realm(s.pool, s.run_id, realm_id)
        if company_pk is None:
            return _fault(404, "Object Not Found", code="610", fault_type="ValidationFault",
                          detail=f"company {realm_id} not found")
        raw = request.query_params.get("query")
        if not raw:
            return _fault(400, "Required param query is missing", code="4000",
                          fault_type="ValidationFault")
        q = parse_query(raw)
        if q.entity is None or q.entity not in _ENTITY_SOURCES:
            return _fault(400, "QueryParserError", code="4000", fault_type="ValidationFault",
                          detail=f"unsupported entity in query: {raw[:120]}")
        if q.is_count:
            total = await _count_entity(s.pool, company_pk, q.entity,
                                        updated_after=q.updated_after)
            return JSONResponse(
                {"QueryResponse": {"totalCount": total}, "time": _now_iso()})
        items = await _fetch_entity(
            s.pool, company_pk, q.entity, updated_after=q.updated_after,
            id_equals=q.id_equals, offset=q.start_position - 1, limit=q.max_results)
        return JSONResponse(_envelope(q.entity, items, start_position=q.start_position))

    return app


app = create_app()
