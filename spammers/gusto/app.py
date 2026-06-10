"""Gusto (payroll + HR) mock — FastAPI app.

Serves the REAL ``api.gusto.com`` ``/v1`` read surface a connector hits for
ingestion. The Fyralis flow doc's QuickBooks-Online SQL-``query`` clone
(``GET /v3/company/{co}/query?query=SELECT…STARTPOSITION…&minorversion=75``) is
UNVERIFIED and DIVERGES ENTIRELY (its own docstrings admit the read surface is a
QBO placeholder); see the gusto-fidelity-audit memory:

    POST /oauth/token                                   (OAuth token mint)
    GET  /v1/companies/{company_uuid}                    (single Company)
    GET  /v1/companies/{company_uuid}/employees          (BARE ARRAY + X-* headers)
    GET  /v1/companies/{company_uuid}/payrolls           (BARE ARRAY + X-* headers)
    GET  /v1/companies/{company_uuid}/payrolls/{uuid}     (single Payroll + comps)

Auth: an OAuth 2.0 access token via ``Authorization: Bearer <token>`` (minted at
the token endpoint). Single-tenant per run, accepts any non-empty Bearer; a
missing/blank one is 401 ``invalid_token``.

**Pagination returns a BARE JSON ARRAY at the top level** (NO body envelope),
with metadata in RESPONSE HEADERS: ``X-Page`` / ``X-Total-Count`` /
``X-Total-Pages`` / ``X-Per-Page`` (``page``/``per`` query params, ``per`` default
25 / max 100). **No ``Link`` header.** Money is a decimal STRING in dollars.

The payrolls list defaults to a 6-month window and rejects a span > 1 year (422),
so a full backfill walks ≤1-year windows. Every response echoes
``X-Gusto-API-Version``. Rate limit: ``POST /_control/rate_limit?count=N`` arms N
forced 429s — Gusto DOES document ``Retry-After`` + ``X-RateLimit-*`` (a real
contrast with hibob/ramp/miro), so the forced 429 carries them.
"""
from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import parse_qsl

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.gusto import dto as _dto
from spammers.gusto import state as _state
from spammers.gusto.auth import api_version, is_authed

_FORCED_429 = {"count": 0}
_DEFAULT_PER = 25
_MAX_PER = 100
_DEFAULT_WINDOW_DAYS = 183  # ~6 months
_MAX_SPAN_DAYS = 366        # payroll start/end span must be ≤ 1 year


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _errors(status: int, category: str, message: str,
            error_key: str = "base", *, version: str = "2024-04-01",
            metadata: Any = None) -> JSONResponse:
    """Gusto's error envelope: ``{"errors":[{error_key, category, message, metadata?}]}``."""
    item: dict[str, Any] = {"error_key": error_key, "category": category,
                            "message": message}
    if metadata is not None:
        item["metadata"] = metadata
    return JSONResponse({"errors": [item]}, status_code=status,
                        headers={"X-Gusto-API-Version": version})


def _unauthorized(version: str) -> JSONResponse:
    return _errors(401, "invalid_token",
                   "Your access token is invalid or has expired.",
                   error_key="request", version=version)


def _ok(body: Any, version: str, headers: Optional[dict[str, str]] = None) -> JSONResponse:
    h = {"X-Gusto-API-Version": version}
    if headers:
        h.update(headers)
    return JSONResponse(body, headers=h)


def _per_page(qp) -> Optional[int]:
    raw = qp.get("per")
    if raw is None or raw == "":
        return _DEFAULT_PER
    try:
        v = int(raw)
    except ValueError:
        return None
    if v < 1:
        return None
    return min(v, _MAX_PER)


def _page_num(qp) -> Optional[int]:
    raw = qp.get("page")
    if raw is None or raw == "":
        return 1
    try:
        v = int(raw)
    except ValueError:
        return None
    return v if v >= 1 else None


def _pagination_headers(total: int, page: int, per: int) -> dict[str, str]:
    total_pages = max(1, (total + per - 1) // per)
    return {
        "X-Page": str(page),
        "X-Per-Page": str(per),
        "X-Total-Count": str(total),
        "X-Total-Pages": str(total_pages),
    }


def _parse_date(raw: str) -> Optional[date]:
    raw = (raw or "").strip()
    try:
        if "T" in raw:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
        return date.fromisoformat(raw)
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Gusto mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v1/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            # Gusto's real 429 DOES carry Retry-After + X-RateLimit-* (docs:
            # 200 req/min; the reset is published). A real contrast with
            # hibob/ramp/miro, which publish no Retry-After.
            reset = (datetime.now(timezone.utc) + timedelta(seconds=30))
            resp = _errors(429, "rate_limit_exceeded",
                           "Your request has been rate limited; retry shortly.",
                           error_key="request")
            resp.headers["Retry-After"] = "30"
            resp.headers["X-RateLimit-Limit"] = "200"
            resp.headers["X-RateLimit-Remaining"] = "0"
            resp.headers["X-RateLimit-Reset"] = reset.strftime("%Y-%m-%dT%H:%M:%SZ")
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "gusto-mock", "run_id": str(s.run_id),
                "company": co["name"] if co else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ------------------------------------------------------------- OAuth token

    @app.post("/oauth/token")
    async def mint_token(request: Request):
        """OAuth 2.0 token endpoint.

        Gusto's grant is ``authorization_code`` (initial) / ``refresh_token``
        (renewal) — NOT client-credentials. Returns ``{access_token,
        token_type:"Bearer", expires_in:7200, created_at, refresh_token}``. The
        mock is single-tenant: any well-formed request mints the seed-stable
        token; an unsupported grant_type → 400 ``invalid_request``."""
        version = api_version(request)
        raw = await request.body()
        body: dict[str, Any] = {}
        if raw:
            try:
                body = json.loads(raw)
            except (ValueError, json.JSONDecodeError):
                body = dict(parse_qsl(raw.decode("utf-8", "ignore")))
        grant_type = body.get("grant_type")
        if grant_type and grant_type not in ("authorization_code", "refresh_token",
                                             "system_access"):
            return _errors(400, "invalid_request",
                           f"unsupported grant_type {grant_type!r}", version=version)
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        token = co["access_token"] if co else secrets.token_urlsafe(32)[:43]
        refresh = co["refresh_token"] if co else secrets.token_urlsafe(32)[:43]
        created = int(datetime.now(timezone.utc).timestamp())
        return _ok({
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 7200,
            "created_at": created,
            "refresh_token": refresh,
            "scope": "public",
        }, version)

    # ---------------------------------------------------------------- company

    async def _resolve_company(request: Request, company_id: str):
        """Auth + tenant resolution: returns (company_row, version) or a JSONResponse."""
        version = api_version(request)
        if not is_authed(request):
            return _unauthorized(version)
        s = _state.state()
        co = await _state.company_for_run(s.pool, s.run_id)
        if co is None or co["company_uuid"] != company_id:
            return _errors(404, "not_found",
                           f"Company {company_id} not found.",
                           error_key="company", version=version)
        return co, version

    @app.get("/v1/companies/{company_id}")
    async def get_company(request: Request, company_id: str):
        resolved = await _resolve_company(request, company_id)
        if isinstance(resolved, JSONResponse):
            return resolved
        co, version = resolved
        return _ok(_dto.company_dto(dict(co)), version)

    # -------------------------------------------------------------- employees

    @app.get("/v1/companies/{company_id}/employees")
    async def list_employees(request: Request, company_id: str):
        resolved = await _resolve_company(request, company_id)
        if isinstance(resolved, JSONResponse):
            return resolved
        co, version = resolved
        qp = request.query_params
        per = _per_page(qp)
        page = _page_num(qp)
        if per is None or page is None:
            return _errors(422, "invalid_parameter",
                           "`page`/`per` must be positive integers.", version=version)
        s = _state.state()
        clauses = ["company_pk = $1"]
        params: list[Any] = [co["id"]]
        if qp.get("terminated") in ("true", "false"):
            params.append(qp["terminated"] == "true")
            clauses.append(f"terminated = ${len(params)}")
        where = " AND ".join(clauses)
        total = int(await s.pool.fetchval(
            f"SELECT count(*) FROM app_gusto.employees WHERE {where}", *params) or 0)
        offset = (page - 1) * per
        rows = await s.pool.fetch(
            f"SELECT e.*, c.company_uuid FROM app_gusto.employees e "
            f"JOIN app_gusto.companies c ON c.id = e.company_pk "
            f"WHERE {where.replace('company_pk', 'e.company_pk')} "
            f"ORDER BY e.sort_key ASC, e.employee_uuid ASC "
            f"OFFSET {offset} LIMIT {per}", *params)
        body = [_dto.employee_dto(dict(r)) for r in rows]
        # BARE ARRAY body; pagination metadata lives in the headers.
        return _ok(body, version, _pagination_headers(total, page, per))

    # --------------------------------------------------------------- payrolls

    def _payroll_window(qp, co_join, vnow_date) -> tuple:
        """Resolve the [start_date, end_date] window. Defaults to the last 6
        months (relative to the run's virtual_now); span > 1 year → 422."""
        end_raw = qp.get("end_date")
        start_raw = qp.get("start_date")
        end_d = _parse_date(end_raw) if end_raw else vnow_date
        if end_raw and end_d is None:
            return None, None, "bad_end"
        start_d = _parse_date(start_raw) if start_raw else (end_d - timedelta(days=_DEFAULT_WINDOW_DAYS))
        if start_raw and start_d is None:
            return None, None, "bad_start"
        if (end_d - start_d).days > _MAX_SPAN_DAYS:
            return None, None, "span"
        return start_d, end_d, None

    @app.get("/v1/companies/{company_id}/payrolls")
    async def list_payrolls(request: Request, company_id: str):
        resolved = await _resolve_company(request, company_id)
        if isinstance(resolved, JSONResponse):
            return resolved
        co, version = resolved
        qp = request.query_params
        per = _per_page(qp)
        page = _page_num(qp)
        if per is None or page is None:
            return _errors(422, "invalid_parameter",
                           "`page`/`per` must be positive integers.", version=version)
        s = _state.state()
        vnow = await _state.virtual_now(s.pool, s.run_id)
        vnow_date = (vnow or datetime.now(timezone.utc)).date()
        start_d, end_d, err = _payroll_window(qp, co.get("join_date"), vnow_date)
        if err in ("bad_end", "bad_start"):
            return _errors(422, "invalid_parameter",
                           "`start_date`/`end_date` must be YYYY-MM-DD.", version=version)
        if err == "span":
            return _errors(422, "invalid_parameter",
                           "The date range cannot exceed one year.", version=version)

        # processing_statuses default `processed`; payroll_types default `regular`.
        statuses = qp.get("processing_statuses") or "processed"
        types_f = qp.get("payroll_types") or "regular"
        date_field = "check_date" if qp.get("date_filter_by") == "check_date" else "pay_period_start"
        include = qp.get("include") or ""
        include_totals = "totals" in include.split(",")

        status_list = [s.strip() for s in statuses.split(",") if s.strip()]
        clauses = ["company_pk = $1", f"{date_field} >= $2", f"{date_field} <= $3"]
        params: list[Any] = [co["id"], start_d, end_d]
        if "processed" in status_list and "unprocessed" not in status_list:
            params.append(True); clauses.append(f"processed = ${len(params)}")
        elif "unprocessed" in status_list and "processed" not in status_list:
            params.append(False); clauses.append(f"processed = ${len(params)}")
        type_list = [t.strip() for t in types_f.split(",") if t.strip()]
        if type_list:
            params.append(type_list); clauses.append(f"payroll_type = ANY(${len(params)})")
        where = " AND ".join(clauses)

        total = int(await s.pool.fetchval(
            f"SELECT count(*) FROM app_gusto.payrolls WHERE {where}", *params) or 0)
        offset = (page - 1) * per
        rows = await s.pool.fetch(
            f"SELECT p.*, c.company_uuid FROM app_gusto.payrolls p "
            f"JOIN app_gusto.companies c ON c.id = p.company_pk "
            f"WHERE {where.replace('company_pk', 'p.company_pk')} "
            f"ORDER BY p.sort_key ASC, p.payroll_uuid ASC "
            f"OFFSET {offset} LIMIT {per}", *params)
        body = [_dto.payroll_dto(dict(r), include_totals=include_totals) for r in rows]
        return _ok(body, version, _pagination_headers(total, page, per))

    @app.get("/v1/companies/{company_id}/payrolls/{payroll_uuid}")
    async def get_payroll(request: Request, company_id: str, payroll_uuid: str):
        resolved = await _resolve_company(request, company_id)
        if isinstance(resolved, JSONResponse):
            return resolved
        co, version = resolved
        s = _state.state()
        row = await s.pool.fetchrow(
            "SELECT p.*, c.company_uuid FROM app_gusto.payrolls p "
            "JOIN app_gusto.companies c ON c.id = p.company_pk "
            "WHERE p.company_pk = $1 AND p.payroll_uuid = $2", co["id"], payroll_uuid)
        if row is None:
            return _errors(404, "not_found",
                           f"Payroll {payroll_uuid} not found.",
                           error_key="payroll", version=version)
        # employee_compensations: per-employee gross/net, deterministic & summing
        # to the payroll totals (the single-GET projection real Gusto returns).
        emps = await s.pool.fetch(
            "SELECT employee_uuid, rate_cents, payment_unit FROM app_gusto.employees "
            "WHERE company_pk = $1 AND terminated = FALSE ORDER BY sort_key", co["id"])
        comps = []
        for e in emps:
            gross = int(e["rate_cents"]) // 24  # semi-monthly
            net = int(gross * 0.72)
            comps.append({
                "employee_uuid": e["employee_uuid"],
                "gross_pay": _dto.money(gross),
                "net_pay": _dto.money(net),
                "payment_method": "Direct Deposit",
                "fixed_compensations": [],
                "hourly_compensations": [],
            })
        return _ok(_dto.payroll_detail_dto(dict(row), comps), version)

    return app


app = create_app()
