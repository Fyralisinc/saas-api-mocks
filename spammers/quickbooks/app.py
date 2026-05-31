"""QuickBooks Online mock — FastAPI app.

Exposes the read endpoints a real connector hits against the QBO REST API v3:

    GET /v3/company/{realmId}/companyinfo/{realmId}
    GET /v3/company/{realmId}/account            — list accounts
    GET /v3/company/{realmId}/vendor             — list vendors
    GET /v3/company/{realmId}/employee           — list employees
    GET /v3/company/{realmId}/deposit            — list deposits
    GET /v3/company/{realmId}/purchase           — list purchases

Pagination uses ``startposition`` + ``maxresults`` (QBO native). Responses are
shaped as ``{QueryResponse: {<Resource>: [...], startPosition, maxResults}}``
matching QBO's envelope so a connector can iterate without special-casing.

Writes happen via the corpus replay layer, not these endpoints — there is no
``POST`` path here. (A real connector for QuickBooks is read-only for ingest.)

OAuth: this mock accepts any ``Authorization: Bearer …`` header. The corpus
provisions credentials in oauth.installs that match a real QBO connection.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from spammers.quickbooks import state as _state


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="QuickBooks Online mock", lifespan=_lifespan)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request, exc: StarletteHTTPException):
        # QBO returns errors as Fault objects.
        return JSONResponse({
            "Fault": {
                "Error": [{
                    "Message": exc.detail if isinstance(exc.detail, str) else "Error",
                    "code": str(exc.status_code),
                }],
                "type": "ValidationFault" if exc.status_code == 400 else "SystemFault",
            },
            "time": _now_iso(),
        }, status_code=exc.status_code)

    @app.get("/_health")
    async def health():
        s = _state.state()
        realm_id = await _state.realm_id_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "quickbooks-mock",
                "run_id": str(s.run_id), "realm_id": realm_id}

    # ---- CompanyInfo --------------------------------------------------------

    @app.get("/v3/company/{realm_id}/companyinfo/{realm_id_again}")
    async def company_info(realm_id: str, realm_id_again: str):
        s = _state.state()
        company_pk = await _state.company_pk_for_realm(s.pool, s.run_id, realm_id)
        if company_pk is None:
            raise HTTPException(404, f"company {realm_id} not found")
        row = await s.pool.fetchrow(
            "SELECT realm_id, company_name, legal_name, country, currency, "
            "fiscal_year_start, created_at "
            "FROM app_quickbooks.companies WHERE id = $1", company_pk)
        return _envelope("CompanyInfo", [{
            "Id":               row["realm_id"],
            "CompanyName":      row["company_name"],
            "LegalName":        row["legal_name"],
            "Country":          row["country"],
            "DefaultCurrencyRef": {"value": row["currency"]},
            "FiscalYearStartMonth": row["fiscal_year_start"],
            "MetaData": {"CreateTime": row["created_at"].isoformat()},
        }])

    # ---- List endpoints (paginated) ----------------------------------------

    def _paged_list(resource: str, sql: str, *, json_keys: dict | None = None):
        async def handler(
            realm_id: str,
            startposition: int = Query(1, ge=1),
            maxresults: int = Query(100, ge=1, le=1000),
        ):
            s = _state.state()
            company_pk = await _state.company_pk_for_realm(s.pool, s.run_id, realm_id)
            if company_pk is None:
                raise HTTPException(404, f"company {realm_id} not found")
            rows = await s.pool.fetch(
                sql, company_pk, maxresults, startposition - 1,
            )
            return _envelope(resource, [_row_to_qbo(r, resource, json_keys or {}) for r in rows],
                             startposition=startposition, maxresults=len(rows))
        return handler

    # IMPORTANT: each /v3/company/{realm_id}/{resource} listing also has a
    # canonical query form via the QBO `?query=SELECT * FROM <Resource>` endpoint,
    # but real connectors generally use the resource-listing URLs against the
    # community libraries — both shapes here.

    app.get("/v3/company/{realm_id}/account")(
        _paged_list("Account",
            """SELECT account_id, account_number, name, type, subtype,
                      description, currency, current_balance_cents, created_at
                 FROM app_quickbooks.accounts WHERE company_pk = $1
                ORDER BY account_number LIMIT $2 OFFSET $3"""))

    app.get("/v3/company/{realm_id}/vendor")(
        _paged_list("Vendor",
            """SELECT vendor_id, display_name, active, currency, created_at
                 FROM app_quickbooks.vendors WHERE company_pk = $1
                ORDER BY display_name LIMIT $2 OFFSET $3"""))

    app.get("/v3/company/{realm_id}/employee")(
        _paged_list("Employee",
            """SELECT employee_id, display_name, email, title, team,
                      location_bucket, annual_salary_cents, active,
                      hired_at, released_at, created_at
                 FROM app_quickbooks.employees WHERE company_pk = $1
                ORDER BY hired_at LIMIT $2 OFFSET $3"""))

    app.get("/v3/company/{realm_id}/deposit")(
        _paged_list("Deposit",
            """SELECT deposit_id, txn_date, amount_cents,
                      round_id, round_kind, lead, participants, memo, created_at
                 FROM app_quickbooks.deposits WHERE company_pk = $1
                ORDER BY txn_date LIMIT $2 OFFSET $3""",
            json_keys={"participants"}))

    app.get("/v3/company/{realm_id}/purchase")(
        _paged_list("Purchase",
            """SELECT purchase_id, txn_date, amount_cents, category,
                      memo, payload, created_at
                 FROM app_quickbooks.purchases WHERE company_pk = $1
                ORDER BY txn_date LIMIT $2 OFFSET $3""",
            json_keys={"payload"}))

    # ---- Minimal query endpoint (SQL-like SELECT) --------------------------

    @app.get("/v3/company/{realm_id}/query")
    async def query(realm_id: str,
                    query: str = Query(..., description="QBO SQL-like SELECT")):
        # Just enough to honor `SELECT * FROM <Resource>` — the real QBO query
        # language is much richer; a connector should fall back to resource
        # endpoints for anything non-trivial.
        q = (query or "").strip().lower()
        if not q.startswith("select"):
            raise HTTPException(400, "only SELECT supported")
        for resource, endpoint in (
            ("account", "/v3/company/{}/account"),
            ("vendor",  "/v3/company/{}/vendor"),
            ("employee", "/v3/company/{}/employee"),
            ("deposit", "/v3/company/{}/deposit"),
            ("purchase", "/v3/company/{}/purchase"),
        ):
            if f"from {resource}" in q:
                # Route into the corresponding list endpoint by calling the
                # handler directly — simplest way to share pagination logic.
                from fastapi import Request
                # Minimal forward: re-call the list handler with default paging.
                # Real implementations would parse SELECT clauses; we do not.
                from starlette.requests import Request as _Req
                # Easiest: re-execute the SQL directly.
                s = _state.state()
                company_pk = await _state.company_pk_for_realm(s.pool, s.run_id, realm_id)
                if company_pk is None:
                    raise HTTPException(404, f"company {realm_id} not found")
                limit = 1000
                table = {
                    "account":  ("Account",   "SELECT * FROM app_quickbooks.accounts  WHERE company_pk = $1 ORDER BY account_number LIMIT $2"),
                    "vendor":   ("Vendor",    "SELECT * FROM app_quickbooks.vendors   WHERE company_pk = $1 ORDER BY display_name LIMIT $2"),
                    "employee": ("Employee",  "SELECT * FROM app_quickbooks.employees WHERE company_pk = $1 ORDER BY hired_at LIMIT $2"),
                    "deposit":  ("Deposit",   "SELECT * FROM app_quickbooks.deposits  WHERE company_pk = $1 ORDER BY txn_date LIMIT $2"),
                    "purchase": ("Purchase",  "SELECT * FROM app_quickbooks.purchases WHERE company_pk = $1 ORDER BY txn_date LIMIT $2"),
                }[resource]
                qb_resource, sql = table
                rows = await s.pool.fetch(sql, company_pk, limit)
                return _envelope(qb_resource, [_row_to_qbo(r, qb_resource, {}) for r in rows])
        raise HTTPException(400, "unrecognized resource in query")

    return app


# ---- helpers --------------------------------------------------------------

def _envelope(resource: str, items: list[dict],
              startposition: int = 1, maxresults: int | None = None) -> dict:
    return {
        "QueryResponse": {
            resource: items,
            "startPosition": startposition,
            "maxResults": maxresults if maxresults is not None else len(items),
            "totalCount": len(items),
        },
        "time": _now_iso(),
    }


def _row_to_qbo(row, resource: str, json_keys: set) -> dict:
    """Map a DB row to the QBO API JSON shape. Best-effort field renames."""
    import json as _json
    d = {}
    for k, v in dict(row).items():
        if k in json_keys and isinstance(v, str):
            try: v = _json.loads(v)
            except (ValueError, TypeError): pass
        # Cents → decimal-2 USD for Amount fields.
        if k.endswith("_cents") and isinstance(v, int):
            d[_qbo_name(k.removesuffix("_cents"))] = round(v / 100, 2)
            continue
        d[_qbo_name(k)] = v.isoformat() if hasattr(v, "isoformat") else v
    return d


def _qbo_name(k: str) -> str:
    # snake_case → QBO's PascalCase, plus a few known exceptions.
    overrides = {
        "deposit_id": "Id", "purchase_id": "Id", "employee_id": "Id",
        "vendor_id":  "Id", "account_id":   "Id",
        "display_name": "DisplayName", "company_name": "CompanyName",
    }
    if k in overrides:
        return overrides[k]
    return "".join(part.capitalize() for part in k.split("_"))


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


app = create_app()
