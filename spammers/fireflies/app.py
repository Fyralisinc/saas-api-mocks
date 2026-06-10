"""Fireflies.ai (AI meeting-notetaker) mock — FastAPI GraphQL app.

Serves the REAL ``api.fireflies.ai`` GraphQL surface a connector hits for
ingestion — a single ``POST /graphql`` exposing:

    transcripts(skip, limit≤50, fromDate, toDate, …)  → [Transcript]  (newest-first
        plain array under data.transcripts; NO total/pageInfo — short page = EOF)
    transcript(id: String!)                           → Transcript    (single hydrate)
    user(id: String)                                  → User          (no id = the
        API-key owner — Fireflies' real "verify my token"; there is NO workspace id)
    users                                             → [User]         (the team)

The Fyralis flow doc clones a FAKE Brex REST surface (``GET /workspace``,
``GET /transcripts?limit&offset&start``, ``GET /transcript/{id}``) — none of those
exist on the real API; they 404/405. The mock honours the REAL GraphQL contract;
the Fyralis-vs-real divergences are LOGGED in the fireflies-fidelity-audit memory.

Auth: ``Authorization: Bearer <api_key>``; a missing/blank one is the documented
``auth_failed`` error (HTTP 401). Errors are GraphQL ``{data:null, errors:[{message,
extensions:{code,…}}]}`` with the documented per-code HTTP status (auth_failed→401,
object_not_found→404, too_many_requests→429, invalid_arguments/args_required→400).

Mock-only: ``POST /_control/rate_limit?count=N`` arms N forced 429s. Fireflies'
real 429 carries the retry hint as a GraphQL ``extensions.metadata.retryAfter``
(a UTC TIMESTAMP) and NO ``Retry-After`` HTTP header — so the mock emits none
either (a real divergence from the Brex archetype Fyralis clones).
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.fireflies import dto as _dto
from spammers.fireflies import graphql as _gql
from spammers.fireflies import state as _state
from spammers.fireflies.auth import is_authed

_FORCED_429 = {"count": 0}
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


class _FieldError(Exception):
    """A GraphQL field-resolution error carrying a documented code."""

    def __init__(self, code: str, message: str, metadata: Optional[dict] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.metadata = metadata


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _gql_error_response(code: str, message: str,
                        metadata: Optional[dict] = None) -> JSONResponse:
    """A GraphQL error envelope + the documented per-code HTTP status."""
    ext: dict[str, Any] = {"code": code}
    if metadata is not None:
        ext["metadata"] = metadata
    status = _dto.ERROR_HTTP_STATUS.get(code, 400)
    return JSONResponse(
        {"data": None, "errors": [{"message": message, "extensions": ext}]},
        status_code=status,
    )


def create_app() -> FastAPI:
    app = FastAPI(title="Fireflies mock", lifespan=_lifespan)

    @app.get("/_health")
    async def health():
        s = _state.state()
        ws = await _state.workspace_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "fireflies-mock", "run_id": str(s.run_id),
                "team_name": ws["team_name"] if ws else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ------------------------------------------------------------------ resolvers

    async def _resolve_transcripts(args: dict, ws: dict, s) -> list[dict]:
        limit = args.get("limit")
        if limit is None:
            limit = _DEFAULT_LIMIT
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise _FieldError("invalid_arguments", "`limit` must be a non-negative Int")
        if limit > _MAX_LIMIT:
            raise _FieldError("invalid_arguments",
                              f"`limit` cannot exceed {_MAX_LIMIT}")
        skip = args.get("skip") or 0
        if not isinstance(skip, int) or isinstance(skip, bool) or skip < 0:
            raise _FieldError("invalid_arguments", "`skip` must be a non-negative Int")

        clauses = ["workspace_pk = $1"]
        params: list[Any] = [ws["id"]]

        def _add_dt(key: str, op: str) -> None:
            raw = args.get(key)
            if raw is None:
                return
            dt = _parse_dt(raw)
            if dt is None:
                raise _FieldError("invalid_arguments",
                                  f"`{key}` must be an ISO-8601 DateTime")
            params.append(dt)
            clauses.append(f"meeting_date {op} ${len(params)}")

        _add_dt("fromDate", ">=")
        _add_dt("toDate", "<=")

        # email/keyword filters (a subset of the documented args).
        for key, col in (("host_email", "host_email"),
                         ("organizer_email", "organizer_email")):
            v = args.get(key)
            if v:
                params.append(v)
                clauses.append(f"{col} = ${len(params)}")
        kw = args.get("keyword")
        if kw:
            params.append(f"%{kw}%")
            clauses.append(f"title ILIKE ${len(params)}")

        where = " AND ".join(clauses)
        params.append(limit)
        params.append(skip)
        rows = await s.pool.fetch(
            f"SELECT * FROM app_fireflies.transcripts WHERE {where} "
            f"ORDER BY sort_key DESC, transcript_id DESC "
            f"LIMIT ${len(params) - 1} OFFSET ${len(params)}", *params)
        return [_dto.transcript_dto(dict(r)) for r in rows]

    async def _resolve_transcript(args: dict, ws: dict, s) -> dict:
        tid = args.get("id")
        if not tid or not isinstance(tid, str):
            raise _FieldError("args_required", "`id` is a required String argument")
        row = await s.pool.fetchrow(
            "SELECT * FROM app_fireflies.transcripts "
            "WHERE workspace_pk = $1 AND transcript_id = $2", ws["id"], tid)
        if row is None:
            raise _FieldError("object_not_found",
                              "object_not_found (transcript): the transcript ID you "
                              "are trying to query does not exist or you do not have "
                              "access to it.")
        return _dto.transcript_dto(dict(row))

    async def _resolve_user(args: dict, ws: dict, s) -> dict:
        uid = args.get("id")
        if uid and uid != ws["owner_user_id"]:
            raise _FieldError("object_not_found", "object_not_found (user)")
        n = await s.pool.fetchval(
            "SELECT count(*) FROM app_fireflies.transcripts WHERE workspace_pk = $1",
            ws["id"])
        return _dto.user_dto(ws, num_transcripts=int(n or 0))

    async def _resolve_users(args: dict, ws: dict, s) -> list[dict]:
        n = await s.pool.fetchval(
            "SELECT count(*) FROM app_fireflies.transcripts WHERE workspace_pk = $1",
            ws["id"])
        return [_dto.user_dto(ws, num_transcripts=int(n or 0))]

    _RESOLVERS = {
        "transcripts": _resolve_transcripts,
        "transcript": _resolve_transcript,
        "user": _resolve_user,
        "users": _resolve_users,
    }

    @app.post("/graphql")
    async def graphql(request: Request):
        # Auth first — the documented auth_failed (401) is independent of the query.
        if not is_authed(request):
            return _gql_error_response(
                "auth_failed",
                "Please ensure that you are including the Authorization header with "
                "the word Bearer and your API key.")
        # Forced rate-limit (mock knob): GraphQL too_many_requests + 429, the retry
        # hint as extensions.metadata.retryAfter (a UTC timestamp) — NO Retry-After header.
        if _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            retry_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            return _gql_error_response(
                "too_many_requests",
                "Too many requests. Please retry after the time indicated (UTC).",
                metadata={"retryAfter": retry_at})

        raw = await request.body()
        try:
            payload = json.loads(raw or b"{}")
        except (ValueError, json.JSONDecodeError):
            return _gql_error_response("invalid_arguments", "request body is not JSON")
        if not isinstance(payload, dict):
            return _gql_error_response("invalid_arguments", "request body must be a JSON object")
        query = payload.get("query")
        variables = payload.get("variables") or {}

        try:
            fields = _gql.parse(query, variables)
        except _gql.GraphQLSyntaxError as exc:
            return _gql_error_response("invalid_arguments", f"GraphQL parse error: {exc}")

        s = _state.state()
        ws = await _state.workspace_for_run(s.pool, s.run_id)
        if ws is None:
            # No workspace provisioned for this run — return null data per field.
            data = {f.key: None for f in fields}
            return JSONResponse({"data": data})
        wsd = dict(ws)

        data: dict[str, Any] = {}
        errors: list[dict] = []
        first_error_code: Optional[str] = None
        for f in fields:
            resolver = _RESOLVERS.get(f.name)
            if resolver is None:
                errors.append({"message": f"Cannot query field '{f.name}'",
                               "extensions": {"code": "invalid_arguments"}})
                first_error_code = first_error_code or "invalid_arguments"
                continue
            try:
                value = await resolver(f.args, wsd, s)
            except _FieldError as fe:
                ext: dict[str, Any] = {"code": fe.code}
                if fe.metadata is not None:
                    ext["metadata"] = fe.metadata
                errors.append({"message": fe.message, "extensions": ext,
                               "path": [f.key]})
                first_error_code = first_error_code or fe.code
                data[f.key] = None
                continue
            data[f.key] = _gql.project(value, f.selections)

        if first_error_code is not None:
            status = _dto.ERROR_HTTP_STATUS.get(first_error_code, 400)
            return JSONResponse({"data": data or None, "errors": errors},
                                status_code=status)
        return JSONResponse({"data": data})

    return app


def _parse_dt(raw: Any) -> Optional[datetime]:
    """Parse an ISO-8601 ``fromDate``/``toDate`` (``Z`` or offset) or a bare date."""
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    try:
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


app = create_app()
