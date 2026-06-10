"""LinkedIn (organization marketing / Community Management) mock — FastAPI app.

Serves the REAL ``api.linkedin.com`` ``/rest/`` Community-Management read surface a
connector hits for organization-page ingestion. The Fyralis flow doc's client is a
QuickBooks-Online / Carta SQL-``query`` clone (``GET /v1/organizations/{org}/query?
query=SELECT…STARTPOSITION``) that it flags TODO(human)/UNVERIFIED throughout —
"cloned wholesale from the Carta OAuth2 archetype". The REAL surface is Rest.li
FINDER collections (divergences LOGGED in linkedin-fidelity-audit):

    GET /rest/organizations/{id}                       (org lookup / connectivity probe)
    GET /rest/posts?q=author&author={orgURN}           (OFFSET start/count; the share stream)
    GET /rest/organizationalEntityShareStatistics?q=organizationalEntity&organizationalEntity={orgURN}
    GET /rest/organizationalEntityFollowerStatistics?q=organizationalEntity&organizationalEntity={orgURN}

Every versioned ``/rest/`` call requires a ``Linkedin-Version: YYYYMM`` header
(missing → 400 VERSION_MISSING; out-of-window → 426 NONEXISTENT_VERSION) plus
``X-Restli-Protocol-Version: 2.0.0``; auth is ``Authorization: Bearer``. Collections
are Rest.li envelopes ``{elements:[…], paging:{start,count,links:[…]}}``; the posts
finder pages by OFFSET (``start``/``count``, default 10 / max 100; EOF = a page with
fewer elements than ``count``). Timestamps are epoch-MILLIS integers.

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. LinkedIn's real
429 body is the CLASSIC ``{message, serviceErrorCode, status}`` with the throttle
message and **NO Retry-After / NO X-RateLimit-*** headers (research-corrected: the
Fyralis client's "honours 429 Retry-After" assumption is unsupported by the docs).

**POLL-ONLY.** LinkedIn org data has NO webhook / push of any kind (partner-gated, no
webhook entitlement) — so there is no webhooks module, no live emit, no live ingest
slice.
"""
from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.linkedin import dto as _dto
from spammers.linkedin import state as _state
from spammers.linkedin.auth import bearer, linkedin_version, version_ok

_FORCED_429 = {"count": 0}
_THROTTLE_MSG = "Resource level throttle limit for calls to this resource is reached."


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _li_headers() -> dict[str, str]:
    """LinkedIn emits opaque request-trace headers on every response (x-li-uuid /
    x-li-fabric / x-li-request-id). NB: there are NO documented X-RateLimit-* headers
    and NO Retry-After — rate-limit budget is only visible in the Developer Portal."""
    return {
        "x-li-uuid": secrets.token_hex(16),
        "x-li-fabric": "prod-lor1",
        "x-li-request-id": secrets.token_hex(8).upper(),
    }


def _error(http_status: int, message: str, *, service_error_code: Optional[int] = None,
           code: Optional[str] = None) -> JSONResponse:
    """LinkedIn's CLASSIC error envelope: ``{message, serviceErrorCode, status}``
    (NOT google.rpc.Status, NOT a Fault). Version errors additionally carry a string
    ``code`` (VERSION_MISSING / NONEXISTENT_VERSION)."""
    body: dict[str, Any] = {
        "message": message,
        "serviceErrorCode": service_error_code if service_error_code is not None else http_status,
        "status": http_status,
    }
    if code is not None:
        body["code"] = code
    return JSONResponse(body, status_code=http_status, headers=_li_headers())


def _unauthorized() -> JSONResponse:
    # Mirrors the documented sample: {"message":"Empty oauth2_access_token",
    # "serviceErrorCode":401,"status":401}.
    return _error(401, "Empty oauth2_access_token", service_error_code=401)


def _bad_request(message: str, *, code: Optional[str] = None) -> JSONResponse:
    return _error(400, message, code=code)


def _gate(request: Request) -> Optional[JSONResponse]:
    """The per-request auth + protocol-header gate. Returns an error response, or
    None when the request is allowed to proceed.

    Order matters and is faithful: a versioned call with NO Linkedin-Version is a
    400 VERSION_MISSING even before token checks (the version is structurally
    required); an out-of-window version is a 426 NONEXISTENT_VERSION; a
    missing/blank Bearer is a 401."""
    ver = linkedin_version(request)
    if ver is None:
        return _bad_request(
            "A version must be present. Please specify a version by adding the "
            "Linkedin-Version header.", code="VERSION_MISSING")
    if not version_ok(ver):
        return _error(426, f"Requested version {ver} is not active",
                      service_error_code=426, code="NONEXISTENT_VERSION")
    if bearer(request) is None:
        return _unauthorized()
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="LinkedIn mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/rest/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            # The 429 body is the CLASSIC {message,serviceErrorCode,status}; there is
            # NO Retry-After and NO X-RateLimit-* header (documented absence).
            return _error(429, _THROTTLE_MSG, service_error_code=429)
        resp = await call_next(request)
        for k, v in _li_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    @app.get("/_health")
    async def health():
        s = _state.state()
        org = await _state.org_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "linkedin-mock", "run_id": str(s.run_id),
                "org_urn": org["org_urn"] if org else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    def s_pool():
        return _state.state().pool

    async def _resolve_org(request: Request):
        s = _state.state()
        return await _state.org_for_run(s.pool, s.run_id)

    # -------------------------------------- GET /rest/organizations/{id}

    @app.get("/rest/organizations/{org_id}")
    async def get_organization(request: Request, org_id: str):
        err = _gate(request)
        if err is not None:
            return err
        org = await _resolve_org(request)
        if org is None or str(org["org_id"]) != str(org_id):
            return _error(404, "Not Found", service_error_code=404, code="NOT_FOUND")
        return JSONResponse(_dto.organization_dto(dict(org)), headers=_li_headers())

    # -------------------------------------- GET /rest/posts (finder q=author)

    @app.get("/rest/posts")
    async def list_posts(request: Request):
        err = _gate(request)
        if err is not None:
            return err
        qp = request.query_params
        if qp.get("q") != "author":
            return _bad_request("The finder `q` must be `author` for /rest/posts.")
        author = qp.get("author")
        if not author:
            return _bad_request("The `author` query parameter is required.")
        start = _int_param(qp.get("start"), 0)
        count = _int_param(qp.get("count"), _dto.DEFAULT_COUNT)
        if start < 0:
            start = 0
        count = max(1, min(count, _dto.MAX_COUNT))
        sort_by = qp.get("sortBy") or "LAST_MODIFIED"

        org = await _resolve_org(request)
        elements: list[dict] = []
        if org is not None and author == org["org_urn"]:
            order = ("created_at_ms" if sort_by == "CREATED" else "last_modified_ms")
            rows = await s_pool().fetch(
                f"SELECT * FROM app_linkedin.posts WHERE org_pk = $1 "
                f"ORDER BY {order} DESC, post_id DESC LIMIT $2 OFFSET $3",
                org["id"], count, start)
            elements = [_dto.post_dto(dict(r), author) for r in rows]
        # Rest.li FINDER envelope: {elements, paging:{start,count,links}}. links stays
        # [] (matches the documented org samples); EOF = a page with < count elements.
        return JSONResponse(
            {"elements": elements, "paging": {"start": start, "count": count, "links": []}},
            headers=_li_headers())

    # ------------------------ GET /rest/organizationalEntityShareStatistics

    @app.get("/rest/organizationalEntityShareStatistics")
    async def share_statistics(request: Request):
        err = _gate(request)
        if err is not None:
            return err
        entity, gerr = _require_entity(request)
        if gerr is not None:
            return gerr
        org = await _resolve_org(request)
        elements: list[dict] = []
        if org is not None and entity == org["org_urn"]:
            row = await s_pool().fetchrow(
                "SELECT * FROM app_linkedin.share_stats WHERE org_pk = $1", org["id"])
            if row is not None:
                elements = [_dto.share_statistics_dto(dict(row), entity)]
        return JSONResponse(
            {"elements": elements, "paging": {"start": 0, "count": 10}},
            headers=_li_headers())

    # ---------------------- GET /rest/organizationalEntityFollowerStatistics

    @app.get("/rest/organizationalEntityFollowerStatistics")
    async def follower_statistics(request: Request):
        err = _gate(request)
        if err is not None:
            return err
        entity, gerr = _require_entity(request)
        if gerr is not None:
            return gerr
        org = await _resolve_org(request)
        elements: list[dict] = []
        if org is not None and entity == org["org_urn"]:
            row = await s_pool().fetchrow(
                "SELECT * FROM app_linkedin.follower_stats WHERE org_pk = $1", org["id"])
            if row is not None:
                elements = [_dto.follower_statistics_dto(dict(row), entity)]
        return JSONResponse(
            {"elements": elements, "paging": {"start": 0, "count": 10}},
            headers=_li_headers())

    def _require_entity(request: Request):
        """Validate the two stats finders' shared params: q=organizationalEntity +
        a required organizationalEntity URN. Returns (entity_urn, error|None)."""
        qp = request.query_params
        if qp.get("q") != "organizationalEntity":
            return None, _bad_request("The finder `q` must be `organizationalEntity`.")
        entity = qp.get("organizationalEntity")
        if not entity:
            return None, _bad_request("The `organizationalEntity` parameter is required.")
        return entity, None

    return app


def _int_param(raw: Optional[str], default: int) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


app = create_app()
