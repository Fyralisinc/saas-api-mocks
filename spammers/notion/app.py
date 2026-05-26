"""Notion mock — FastAPI app factory.

Usage:
    python -m spammers.notion run --port 7006
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
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
    resp = await guard(request)
    return resp if resp is not None else await call_next(request)


# status code -> Notion error `code`, so even unhandled paths/methods/validation
# failures return Notion's `{"object":"error",...}` envelope, not FastAPI's `{"detail":…}`.
_NOTION_CODE = {
    400: "invalid_request", 401: "unauthorized", 403: "restricted_resource",
    404: "object_not_found", 405: "invalid_request", 409: "conflict_error",
    429: "rate_limited", 500: "internal_server_error", 503: "service_unavailable",
}


def _notion_envelope(status: int, message: str) -> NotionJSONResponse:
    return NotionJSONResponse(
        notion_error(status, _NOTION_CODE.get(status, "internal_server_error"), message),
        status_code=status,
    )


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

    return app


app = create_app()
