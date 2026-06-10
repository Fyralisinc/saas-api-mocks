"""Figma design-tool mock — FastAPI app.

Serves the REAL ``api.figma.com`` ``/v1/`` read surface a connector hits for design
ingestion. The Fyralis flow doc's Brex-Bearer-cloned single ``GET /v1/files/{key}/events``
is UNVERIFIED and DIVERGES — there is NO ``/events`` endpoint and NO ``/v1/files``
list. A real backfill ENUMERATES files then MERGES versions + comments:

    GET /v1/me                          (auth probe — the ONLY User carrying email)
    GET /v1/teams/{team_id}/projects    ({name, projects:[{id,name}]})
    GET /v1/projects/{project_id}/files ({name, files:[{key,name,thumbnail_url,last_modified}]})
    GET /v1/files/{key}/meta            ({file:{…}} — lightweight metadata)
    GET /v1/files/{key}/versions        ({versions:[…], pagination:{prev_page,next_page}};
                                         CURSOR page_size(def30/max50)+before/after, FULL-URL links)
    GET /v1/files/{key}/comments        ({comments:[…]} — NO pagination, all in one array)

Auth is ``X-Figma-Token`` OR ``Authorization: Bearer`` (both accepted; see auth.py);
the mock is single-tenant per run and accepts any non-empty token. A missing/blank
token on these file-scoped reads is **403** (NOT 401) with the ``{status, err}``
err-message envelope (the documented per-endpoint behaviour). Rate limiting is 429
+ ``Retry-After`` (Figma DOES document Retry-After — unlike HiBob) + ``X-Figma-*``
headers. Timestamps are UTC ISO-8601 with ``Z``.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from spammers.figma import dto as _dto
from spammers.figma import state as _state
from spammers.figma.auth import is_authed

_FORCED_429 = {"count": 0}
_VERSIONS_DEFAULT_PAGE = 30
_VERSIONS_MAX_PAGE = 50


@asynccontextmanager
async def _lifespan(app: FastAPI):  # pragma: no cover
    await _state.startup()
    yield
    await _state.shutdown()


def _error(status: int, err: str) -> JSONResponse:
    # Figma's file/version/comment read endpoints use the err-message envelope:
    # {status: <int>, err: "<message>"} (NOT {error, status, message}).
    return JSONResponse({"status": status, "err": err}, status_code=status)


def _forbidden() -> JSONResponse:
    # File-scoped reads document 403 (not 401) for an invalid/expired/missing token.
    return _error(403, "Invalid token. Not authorized to access this resource.")


def _int_param(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def create_app() -> FastAPI:
    app = FastAPI(title="Figma mock", lifespan=_lifespan)

    @app.middleware("http")
    async def _rate_limit(request: Request, call_next):
        if request.url.path.startswith("/v1/") and _FORCED_429["count"] > 0:
            _FORCED_429["count"] -= 1
            resp = _error(429, "Rate limit exceeded.")
            # Figma DOES document Retry-After (seconds) + plan/tier headers — a real
            # divergence from HiBob (which has none). NOT an HMAC/X-RateLimit-Reset scheme.
            resp.headers["Retry-After"] = "1"
            resp.headers["X-Figma-Plan-Tier"] = "org"
            resp.headers["X-Figma-Rate-Limit-Type"] = "high"
            resp.headers["X-Figma-Upgrade-Link"] = "https://www.figma.com/pricing/"
            return resp
        return await call_next(request)

    @app.get("/_health")
    async def health():
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        return {"ok": True, "service": "figma-mock", "run_id": str(s.run_id),
                "team_id": team["team_id"] if team else None}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(count: int = 1):
        _FORCED_429["count"] = max(0, count)
        return {"armed": _FORCED_429["count"]}

    # ----------------------------------------------------------------- /v1/me

    @app.get("/v1/me")
    async def me(request: Request):
        if not is_authed(request):
            return _forbidden()
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        if team is None:
            return _error(404, "No user.")
        row = await s.pool.fetchrow(
            "SELECT figma_user_id, handle, img_url, email FROM app_figma.users "
            "WHERE team_pk = $1 AND is_me = TRUE LIMIT 1", team["id"])
        if row is None:
            return _error(404, "No user.")
        return JSONResponse(_dto.me_dto(dict(row)))

    # -------------------------------------------------- teams -> projects

    @app.get("/v1/teams/{team_id}/projects")
    async def team_projects(request: Request, team_id: str):
        if not is_authed(request):
            return _forbidden()
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        if team is None or team["team_id"] != team_id:
            return _error(404, "Team not found.")
        rows = await s.pool.fetch(
            "SELECT project_id, name FROM app_figma.projects WHERE team_pk = $1 "
            "ORDER BY sort_key ASC, project_id ASC", team["id"])
        return JSONResponse({"name": team["team_name"],
                             "projects": [_dto.project_dto(dict(r)) for r in rows]})

    # -------------------------------------------------- projects -> files

    @app.get("/v1/projects/{project_id}/files")
    async def project_files(request: Request, project_id: str):
        if not is_authed(request):
            return _forbidden()
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        if team is None:
            return _error(404, "Project not found.")
        proj = await s.pool.fetchrow(
            "SELECT id, name FROM app_figma.projects WHERE team_pk = $1 AND project_id = $2",
            team["id"], project_id)
        if proj is None:
            return _error(404, "Project not found.")
        rows = await s.pool.fetch(
            "SELECT file_key, name, thumbnail_url, last_modified FROM app_figma.files "
            "WHERE project_pk = $1 ORDER BY sort_key ASC, file_key ASC", proj["id"])
        return JSONResponse({"name": proj["name"],
                             "files": [_dto.file_listing_dto(dict(r)) for r in rows]})

    # ------------------------------------------------------ file metadata

    async def _file_for_key(s, team_pk, file_key) -> Optional[dict]:
        row = await s.pool.fetchrow(
            "SELECT id, file_key, name, thumbnail_url, editor_type, folder_name, "
            "creator_pk, current_version_id, last_modified FROM app_figma.files "
            "WHERE team_pk = $1 AND file_key = $2", team_pk, file_key)
        return dict(row) if row else None

    async def _user(s, user_pk) -> Optional[dict]:
        if user_pk is None:
            return None
        row = await s.pool.fetchrow(
            "SELECT figma_user_id, handle, img_url FROM app_figma.users WHERE id = $1",
            user_pk)
        return dict(row) if row else None

    @app.get("/v1/files/{file_key}/meta")
    async def file_meta(request: Request, file_key: str):
        if not is_authed(request):
            return _forbidden()
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        if team is None:
            return _error(404, "File not found.")
        f = await _file_for_key(s, team["id"], file_key)
        if f is None:
            return _error(404, "File not found.")
        creator = await _user(s, f.get("creator_pk"))
        # last_touched_by = the author of the newest version (best-effort).
        lt = await s.pool.fetchrow(
            "SELECT u.figma_user_id, u.handle, u.img_url FROM app_figma.versions v "
            "JOIN app_figma.users u ON u.id = v.user_pk WHERE v.file_pk = $1 "
            "ORDER BY v.version_seq DESC LIMIT 1", f["id"])
        return JSONResponse({"file": _dto.file_meta_dto(
            f, creator, dict(lt) if lt else creator)})

    # --------------------------------------------------------- versions

    @app.get("/v1/files/{file_key}/versions")
    async def file_versions(request: Request, file_key: str):
        if not is_authed(request):
            return _forbidden()
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        if team is None:
            return _error(404, "File not found.")
        f = await _file_for_key(s, team["id"], file_key)
        if f is None:
            return _error(404, "File not found.")
        qp = request.query_params

        page_size = _VERSIONS_DEFAULT_PAGE
        if qp.get("page_size") is not None:
            ps = _int_param(qp.get("page_size"))
            if ps is None or ps < 1:
                return _error(400, "Invalid page_size.")
            page_size = min(ps, _VERSIONS_MAX_PAGE)   # clamp to 50 (no error)
        before = after = None
        if qp.get("before") is not None:
            before = _int_param(qp.get("before"))
            if before is None:
                return _error(400, "Invalid before.")
        if qp.get("after") is not None:
            after = _int_param(qp.get("after"))
            if after is None:
                return _error(400, "Invalid after.")

        clauses = ["v.file_pk = $1"]
        params: list[Any] = [f["id"]]
        if before is not None:
            params.append(before)
            clauses.append(f"v.version_seq < ${len(params)}")
        if after is not None:
            params.append(after)
            clauses.append(f"v.version_seq > ${len(params)}")
        where = " AND ".join(clauses)
        # Newest-first, one extra row to detect a further page.
        rows = await s.pool.fetch(
            f"SELECT v.*, u.figma_user_id, u.handle, u.img_url "
            f"FROM app_figma.versions v JOIN app_figma.users u ON u.id = v.user_pk "
            f"WHERE {where} ORDER BY v.version_seq DESC LIMIT {page_size + 1}", *params)
        window = [dict(r) for r in rows[:page_size]]
        versions = [_dto.version_dto(r, r) for r in window]

        pagination: dict[str, str] = {}
        if window:
            min_seq = window[-1]["version_seq"]
            max_seq = window[0]["version_seq"]
            # next_page (older) — present if any version is older than this page's tail.
            has_older = await s.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM app_figma.versions "
                "WHERE file_pk = $1 AND version_seq < $2)", f["id"], min_seq)
            if has_older:
                pagination["next_page"] = str(
                    request.url.replace_query_params(page_size=page_size, before=min_seq))
            # prev_page (newer) — present once we've paged off the head.
            has_newer = await s.pool.fetchval(
                "SELECT EXISTS(SELECT 1 FROM app_figma.versions "
                "WHERE file_pk = $1 AND version_seq > $2)", f["id"], max_seq)
            if has_newer:
                pagination["prev_page"] = str(
                    request.url.replace_query_params(page_size=page_size, after=max_seq))
        return JSONResponse({"versions": versions, "pagination": pagination})

    # --------------------------------------------------------- comments

    @app.get("/v1/files/{file_key}/comments")
    async def file_comments(request: Request, file_key: str):
        if not is_authed(request):
            return _forbidden()
        s = _state.state()
        team = await _state.team_for_run(s.pool, s.run_id)
        if team is None:
            return _error(404, "File not found.")
        f = await _file_for_key(s, team["id"], file_key)
        if f is None:
            return _error(404, "File not found.")
        # `as_md` is accepted (markdown rendering); our messages are plain so it is a
        # no-op. There is NO pagination — every comment returns in one array.
        rows = await s.pool.fetch(
            "SELECT c.*, c.file_pk, u.figma_user_id, u.handle, u.img_url, $2::text AS file_key "
            "FROM app_figma.comments c JOIN app_figma.users u ON u.id = c.user_pk "
            "WHERE c.file_pk = $1 ORDER BY c.sort_key ASC, c.comment_id ASC",
            f["id"], file_key)
        return JSONResponse({"comments": [_dto.comment_dto(dict(r), dict(r)) for r in rows]})

    return app


app = create_app()
