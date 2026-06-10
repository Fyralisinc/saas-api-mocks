"""Carta (cap-table / equity-management) mock — FastAPI app.

Serves the REAL ``api.carta.com`` ``/v1alpha1`` issuer cap-table read surface a
connector hits for ingestion. The Fyralis flow doc's client is a QuickBooks-Online/
Gusto SQL-``query`` clone (``GET /v1/firms/{firm_id}/query?query=SELECT…STARTPOSITION``)
which it flags TODO(human)/UNVERIFIED throughout — the REAL surface is REST
collections under the ISSUER suite (divergences LOGGED in carta-fidelity-audit):

    POST /o/access_token/                                (OAuth client-credentials mint)
    GET  /v1alpha1/issuers                               (AIP page {issuers, nextPageToken})
    GET  /v1alpha1/issuers/{id}                          (single {issuer:{…}}, singular wrap)
    GET  /v1alpha1/issuers/{id}/stakeholders             (AIP page, max pageSize 100)
    GET  /v1alpha1/issuers/{id}/shareClasses             (AIP page, max pageSize 50)
    GET  /v1alpha1/issuers/{id}/optionGrants             (AIP page, max 50; lastModified* filter)
    GET  /v1alpha1/issuers/{id}/convertibleNotes         (AIP page, max 50 — SAFEs)

Pagination is Google **AIP-158 token style**: ``pageSize`` (default 25, per-endpoint
max; over-max is COERCED, not rejected) + opaque ``pageToken`` → the response wraps
the list under its PLURAL key alongside ``nextPageToken``; ``nextPageToken`` is
ABSENT on the last page (the EOF signal). Money + every decimal/quantity is a
PROTOBUF WRAPPER (``{currencyCode:{value},amount:{value}}`` / ``{value:"<dec>"}``).

Auth: an OAuth 2.0 access token via ``Authorization: Bearer <token>`` (opaque,
minted at ``/o/access_token/``). Single-tenant per run; any non-empty Bearer reads,
a missing/blank one → **401** google.rpc.Status (reason MISSING_OR_INVALID_ACCESS_TOKEN).

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. Carta's real
429 body is a FLAT ``{message:"API rate limit exceeded"}`` (NOT the google.rpc.Status
envelope) + ``RateLimit-*`` / ``X-RateLimit-*-Second`` / ``-Minute`` headers and **NO
Retry-After** (Carta exposes ``RateLimit-Reset`` instead).

**POLL-ONLY.** Carta has NO webhook / push of any kind — so there is no webhooks
module, no live emit, and no live ingest slice.
"""
from __future__ import annotations

import base64
import binascii
import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.carta import dto as _dto
from spammers.carta import state as _state
from spammers.carta.auth import is_authed

_FORCED_429 = {"count": 0}
_RL_LIMIT_SECOND = 10
_RL_LIMIT_MINUTE = 300

# google.rpc canonical codes (AIP-193) we use.
_RPC_INVALID_ARGUMENT = 3
_RPC_NOT_FOUND = 5
_RPC_PERMISSION_DENIED = 7
_RPC_UNAUTHENTICATED = 16


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _rl_headers() -> dict[str, str]:
    """Carta's rate-limit signalling — present on every response. NO Retry-After;
    Carta exposes ``RateLimit-Reset`` (seconds) + per-second/per-minute splits."""
    reset = 60
    return {
        "RateLimit-Limit": str(_RL_LIMIT_MINUTE),
        "RateLimit-Remaining": str(_RL_LIMIT_MINUTE - 1),
        "RateLimit-Reset": str(reset),
        "X-RateLimit-Limit-Second": str(_RL_LIMIT_SECOND),
        "X-RateLimit-Remaining-Second": str(_RL_LIMIT_SECOND - 1),
        "X-RateLimit-Limit-Minute": str(_RL_LIMIT_MINUTE),
        "X-RateLimit-Remaining-Minute": str(_RL_LIMIT_MINUTE - 1),
    }


def _status_error(http_status: int, rpc_code: int, message: str, reason: str,
                  metadata: Optional[dict] = None) -> JSONResponse:
    """Carta's normal error body = google.rpc.Status (AIP-193): a top-level
    ``code`` (int), ``message``, and ``details:[{@type, reason, metadata}]``."""
    body = {
        "code": rpc_code,
        "message": message,
        "details": [{
            "@type": "type.googleapis.com/carta.proto.publicapi.errors.v1alpha1.ErrorInfo",
            "reason": reason,
            "metadata": metadata or {},
        }],
    }
    return JSONResponse(body, status_code=http_status, headers=_rl_headers())


def _unauthorized() -> JSONResponse:
    return _status_error(401, _RPC_UNAUTHENTICATED, "Unauthorized",
                         "MISSING_OR_INVALID_ACCESS_TOKEN")


def _not_found(message: str = "Resource not found") -> JSONResponse:
    return _status_error(404, _RPC_NOT_FOUND, message, "RESOURCE_NOT_FOUND")


def _bad_request(message: str) -> JSONResponse:
    return _status_error(400, _RPC_INVALID_ARGUMENT, message, "INVALID_ARGUMENT")


def _encode_token(sort_key: int) -> str:
    """Carta's opaque AIP ``nextPageToken`` — base64 of the last row's sort_key
    (the real tokens look like ``ODMxMw==`` = base64 of a number)."""
    return base64.b64encode(str(sort_key).encode()).decode()


def _decode_token(token: Optional[str]) -> Optional[int]:
    """Decode an opaque pageToken back to its sort_key floor, or None."""
    if not token:
        return None
    try:
        raw = base64.b64decode(token).decode()
        return int(raw)
    except (ValueError, binascii.Error, TypeError):
        return None


def _page_size(qp, endpoint: str) -> int:
    """Resolve ``pageSize``: default 25; over the per-endpoint max is COERCED down
    (Carta does not reject it). A non-numeric/<=0 value falls back to the default."""
    raw = qp.get("pageSize")
    cap = _dto.MAX_PAGE_SIZE.get(endpoint, 50)
    if raw is None or raw == "":
        return _dto.DEFAULT_PAGE_SIZE
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return _dto.DEFAULT_PAGE_SIZE
    if v < 1:
        return _dto.DEFAULT_PAGE_SIZE
    return min(v, cap)


def _parse_dt(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Carta mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v1alpha1/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            # Carta's 429 body is a FLAT {message} (NOT google.rpc.Status), with the
            # RateLimit-* / X-RateLimit-*-Second/-Minute headers and NO Retry-After.
            headers = _rl_headers()
            headers["RateLimit-Remaining"] = "0"
            headers["X-RateLimit-Remaining-Minute"] = "0"
            return JSONResponse({"message": "API rate limit exceeded"},
                                status_code=429, headers=headers)
        resp = await call_next(request)
        for k, v in _rl_headers().items():
            resp.headers.setdefault(k, v)
        return resp

    @app.get("/_health")
    async def health():
        s = _state.state()
        issuer = await _state.issuer_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "carta-mock", "run_id": str(s.run_id),
                "issuer_id": issuer["issuer_id"] if issuer else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ---------------------------------------------------------- OAuth token mint

    @app.post("/o/access_token/")
    async def mint_token(request: Request):
        """OAuth 2.0 token endpoint (Carta's IdP login.app.carta.com path, served
        here on the same port). Client auth is HTTP Basic ``base64(client_id:
        client_secret)`` (``client_secret_basic``) OR creds in the form body.
        grant_type is documented UPPERCASE (``CLIENT_CREDENTIALS`` /
        ``AUTHORIZATION_CODE``); we accept those + the lowercase forms. The
        client-credentials response carries NO ``refresh_token`` (you re-mint)."""
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
                from urllib.parse import parse_qsl
                body = dict(parse_qsl(raw.decode("utf-8", "ignore")))
        grant_type = (body.get("grant_type") or "").upper()
        scope = body.get("scope") or ("read_issuer_info read_issuer_stakeholders "
                                      "read_issuer_shareclasses read_issuer_securities")
        if not client_id:
            client_id = body.get("client_id")
            client_secret = body.get("client_secret")
        if grant_type and grant_type not in ("CLIENT_CREDENTIALS", "AUTHORIZATION_CODE",
                                             "REFRESH_TOKEN"):
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400,
                                headers=_rl_headers())
        if not (client_id and client_secret):
            return JSONResponse({"error": "invalid_client"}, status_code=401,
                                headers=_rl_headers())
        s = _state.state()
        issuer = await _state.issuer_for_run(s.pool, s.run_id)
        token = issuer["access_token"] if issuer else secrets.token_urlsafe(32)
        resp = {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,  # Carta access tokens live ~1 hour
            "scope": scope,
        }
        # AUTHORIZATION_CODE issues a refresh_token; CLIENT_CREDENTIALS does NOT.
        if grant_type == "AUTHORIZATION_CODE":
            resp["refresh_token"] = "carta_rt_" + secrets.token_urlsafe(24)
        return JSONResponse(resp, headers=_rl_headers())

    # ----------------------------------------------- generic AIP list helper

    async def _aip_list(request: Request, table: str, id_col: str, dto_fn,
                        endpoint: str, issuer_pk, issuer_id: str,
                        extra_where: Optional[list] = None,
                        extra_params: Optional[list] = None) -> JSONResponse:
        qp = request.query_params
        page_size = _page_size(qp, endpoint)
        floor = _decode_token(qp.get("pageToken"))

        clauses = ["issuer_pk = $1"]
        params: list[Any] = [issuer_pk]
        if extra_where:
            for clause, val in zip(extra_where, extra_params or []):
                params.append(val)
                clauses.append(clause.replace("$$", f"${len(params)}"))
        if floor is not None:
            params.append(floor)
            clauses.append(f"sort_key > ${len(params)}")
        where = " AND ".join(clauses)

        params.append(page_size + 1)  # fetch one extra to detect a further page
        rows = await s_pool().fetch(
            f"SELECT * FROM {table} WHERE {where} "
            f"ORDER BY sort_key ASC, {id_col} ASC LIMIT ${len(params)}", *params)
        has_more = len(rows) > page_size
        window = []
        for r in rows[:page_size]:
            rd = dict(r)
            rd["issuer_id"] = issuer_id  # the DTO needs the wire issuer id
            window.append(rd)
        body: dict[str, Any] = {endpoint: [dto_fn(r) for r in window]}
        if has_more and window:
            # nextPageToken is PRESENT only when there is a further page; ABSENT at EOF.
            body["nextPageToken"] = _encode_token(window[-1]["sort_key"])
        return JSONResponse(body, headers=_rl_headers())

    def s_pool():
        return _state.state().pool

    async def _resolve_issuer(request: Request, issuer_id: str):
        """Return (issuer_pk, JSONResponse|None). The mock is single-tenant per run —
        the path issuer must match the provisioned one (else 404)."""
        s = _state.state()
        issuer = await _state.issuer_for_run(s.pool, s.run_id)
        if issuer is None or issuer["issuer_id"] != issuer_id:
            return None, _not_found(f"Issuer {issuer_id} not found")
        return issuer, None

    # --------------------------------------------------- GET /v1alpha1/issuers

    @app.get("/v1alpha1/issuers")
    async def list_issuers(request: Request):
        if not is_authed(request):
            return _unauthorized()
        s = _state.state()
        issuer = await _state.issuer_for_run(s.pool, s.run_id)
        items = [_dto.issuer_dto(dict(issuer))] if issuer else []
        # AIP list: plural key + (here) no further page → no nextPageToken.
        return JSONResponse({"issuers": items}, headers=_rl_headers())

    @app.get("/v1alpha1/issuers/{issuer_id}")
    async def get_issuer(request: Request, issuer_id: str):
        if not is_authed(request):
            return _unauthorized()
        issuer, err = await _resolve_issuer(request, issuer_id)
        if err is not None:
            return err
        # Single-object GET wraps under the SINGULAR key.
        return JSONResponse({"issuer": _dto.issuer_dto(dict(issuer))},
                            headers=_rl_headers())

    # --------------------------- GET /v1alpha1/issuers/{id}/stakeholders

    @app.get("/v1alpha1/issuers/{issuer_id}/stakeholders")
    async def list_stakeholders(request: Request, issuer_id: str):
        if not is_authed(request):
            return _unauthorized()
        issuer, err = await _resolve_issuer(request, issuer_id)
        if err is not None:
            return err
        return await _aip_list(request, "app_carta.stakeholders", "stakeholder_id",
                               _dto.stakeholder_dto, "stakeholders",
                               issuer["id"], issuer_id)

    # --------------------------- GET /v1alpha1/issuers/{id}/shareClasses

    @app.get("/v1alpha1/issuers/{issuer_id}/shareClasses")
    async def list_share_classes(request: Request, issuer_id: str):
        if not is_authed(request):
            return _unauthorized()
        issuer, err = await _resolve_issuer(request, issuer_id)
        if err is not None:
            return err
        return await _aip_list(request, "app_carta.share_classes", "share_class_id",
                               _dto.share_class_dto, "shareClasses",
                               issuer["id"], issuer_id)

    # --------------------------- GET /v1alpha1/issuers/{id}/optionGrants

    @app.get("/v1alpha1/issuers/{issuer_id}/optionGrants")
    async def list_option_grants(request: Request, issuer_id: str):
        if not is_authed(request):
            return _unauthorized()
        issuer, err = await _resolve_issuer(request, issuer_id)
        if err is not None:
            return err
        qp = request.query_params
        extra_where, extra_params = [], []
        # Carta's incremental knob: lastModifiedDatetimeAfter/Before (ISO-8601 UTC).
        if qp.get("lastModifiedDatetimeAfter"):
            dt = _parse_dt(qp["lastModifiedDatetimeAfter"])
            if dt is None:
                return _bad_request("Invalid `lastModifiedDatetimeAfter`.")
            extra_where.append("last_modified > $$"); extra_params.append(dt)
        if qp.get("lastModifiedDatetimeBefore"):
            dt = _parse_dt(qp["lastModifiedDatetimeBefore"])
            if dt is None:
                return _bad_request("Invalid `lastModifiedDatetimeBefore`.")
            extra_where.append("last_modified < $$"); extra_params.append(dt)
        return await _aip_list(request, "app_carta.option_grants", "grant_id",
                               _dto.option_grant_dto, "optionGrants",
                               issuer["id"], issuer_id, extra_where, extra_params)

    # ------------------------- GET /v1alpha1/issuers/{id}/convertibleNotes

    @app.get("/v1alpha1/issuers/{issuer_id}/convertibleNotes")
    async def list_convertible_notes(request: Request, issuer_id: str):
        if not is_authed(request):
            return _unauthorized()
        issuer, err = await _resolve_issuer(request, issuer_id)
        if err is not None:
            return err
        return await _aip_list(request, "app_carta.convertible_notes", "note_id",
                               _dto.convertible_note_dto, "convertibleNotes",
                               issuer["id"], issuer_id)

    return app


app = create_app()
