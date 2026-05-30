"""Google Drive mock — FastAPI app factory.

Usage:
    python -m spammers.drive run --port 7007
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from spammers.common.errors import google_error
from spammers.drive import state as _state
from spammers.drive.responses import GoogleJSONResponse
from spammers.drive.routes import (
    changes as _changes,
    drives as _drives,
    files as _files,
    token as _token,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


async def _ratelimit(request, call_next):
    from spammers.drive.ratelimit import guard
    resp = await guard(request)
    return resp if resp is not None else await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="Google Drive mock", lifespan=_lifespan,
                  default_response_class=GoogleJSONResponse)
    app.middleware("http")(_ratelimit)

    # Unhandled paths/methods/validation return Google's error envelope, not
    # FastAPI's {"detail":…} (mirrors how real Drive v3 reports 404/405).
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) else "Not Found"
        return GoogleJSONResponse(google_error(exc.status_code, msg), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request, exc: RequestValidationError):
        return GoogleJSONResponse(google_error(400, "Invalid query parameter."), status_code=400)

    app.include_router(_token.router)
    app.include_router(_drives.router)
    app.include_router(_changes.router)
    app.include_router(_files.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "drive-mock"}

    return app


app = create_app()
