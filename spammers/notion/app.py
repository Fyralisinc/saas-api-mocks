"""Notion mock — FastAPI app factory.

Usage:
    python -m spammers.notion run --port 7006
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from spammers.common.errors import notion_error
from spammers.notion import state as _state
from spammers.notion.responses import NotionJSONResponse
from spammers.notion.routes import (
    blocks as _blocks,
    comments as _comments,
    databases as _databases,
    oauth as _oauth,
    pages as _pages,
    search as _search,
    users as _users,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


async def _ratelimit(request, call_next):
    from spammers.notion.ratelimit import guard
    bad_version = _require_version(request)
    if bad_version is not None:
        return bad_version
    resp = await guard(request)
    return resp if resp is not None else await call_next(request)


# status code -> Notion error `code`, so even unhandled paths/methods/validation
# failures return Notion's `{"object":"error",...}` envelope, not FastAPI's `{"detail":…}`.
_NOTION_CODE = {
    400: "invalid_request", 401: "unauthorized", 403: "restricted_resource",
    404: "object_not_found", 405: "invalid_request", 409: "conflict_error",
    429: "rate_limited", 500: "internal_server_error", 503: "service_unavailable",
}


def _notion_envelope(status: int, message: str, code: str | None = None) -> NotionJSONResponse:
    return NotionJSONResponse(
        notion_error(status, code or _NOTION_CODE.get(status, "internal_server_error"), message),
        status_code=status,
    )


# Real Notion REST requires the `Notion-Version` header on every request and
# returns 400 `missing_version` when it is absent (mandatory since 2021-06-01).
# OAuth + health don't need it.
_MISSING_VERSION_MSG = (
    "Notion-Version header failed validation: Notion-Version header should be "
    "defined, instead was undefined."
)


def _require_version(request) -> NotionJSONResponse | None:
    path = request.url.path
    if not path.startswith("/v1/") or path.startswith("/v1/oauth"):
        return None
    if not request.headers.get("notion-version"):
        return _notion_envelope(400, _MISSING_VERSION_MSG, code="missing_version")
    return None


def create_app() -> FastAPI:
    app = FastAPI(title="Notion mock", lifespan=_lifespan,
                  default_response_class=NotionJSONResponse)
    app.middleware("http")(_ratelimit)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) else "Not Found"
        return _notion_envelope(exc.status_code, msg)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request, exc: RequestValidationError):
        return _notion_envelope(400, "The request body or query is not valid.")

    app.include_router(_oauth.router)
    app.include_router(_search.router)
    app.include_router(_databases.router)
    app.include_router(_blocks.router)
    app.include_router(_comments.router)
    app.include_router(_pages.router)
    app.include_router(_users.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "notion-mock"}

    @app.post("/_control/rate_limit")
    async def arm_rate_limit(request: Request):
        """Mock-only: arm the next ``count`` authed /v1 requests to return
        429 + Retry-After (to exercise a consumer's backoff). Not a Notion API."""
        from spammers.notion.ratelimit import arm_rate_limit as _arm
        from spammers.notion.state import state
        qs = request.query_params
        count = int(qs.get("count", "5"))
        retry_after = int(qs.get("retry_after", "1"))
        _arm(state().integration_pk, count, retry_after)
        return {"armed": count, "retry_after": retry_after}

    @app.post("/_control/revoke")
    async def revoke(request: Request):
        """Mock-only: revoke/restore the integration token. While revoked, every
        authed /v1 call returns 401 unauthorized (real Notion's behavior for a
        removed integration). Not a Notion API."""
        from spammers.notion.auth import set_revoked
        revoked = (request.query_params.get("revoked", "true").lower()
                   in ("1", "true", "yes"))
        set_revoked(revoked)
        return {"revoked": revoked}

    return app


app = create_app()
