"""Jira Cloud mock — FastAPI app factory.

Usage:
    python -m spammers.jira run --port 7008
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from spammers.common.errors import jira_error
from spammers.jira import state as _state
from spammers.jira.routes import (
    myself as _myself,
    projects as _projects,
    search as _search,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


async def _ratelimit(request, call_next):
    from spammers.jira.ratelimit import guard
    resp = await guard(request)
    return resp if resp is not None else await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="Jira Cloud mock", lifespan=_lifespan)
    app.middleware("http")(_ratelimit)

    # Even unhandled paths/methods/validation failures return Jira's
    # {"errorMessages":[...],"errors":{}} envelope, not FastAPI's {"detail":…}.
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request, exc: StarletteHTTPException):
        msg = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(jira_error(msg), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request, exc: RequestValidationError):
        return JSONResponse(jira_error("The request is not valid."), status_code=400)

    app.include_router(_search.router)
    app.include_router(_projects.router)
    app.include_router(_myself.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "jira-mock"}

    return app


app = create_app()
