"""Gmail mock — FastAPI app factory.

Usage:
    python -m spammers.gmail run --port 7004
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from spammers.gmail import state as _state
from spammers.gmail.responses import GoogleJSONResponse
from spammers.gmail.routes import (
    directory as _directory,
    history as _history,
    jwks as _jwks,
    mailbox as _mailbox,
    messages as _messages,
    threads as _threads,
    token as _token,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


async def _ratelimit(request, call_next):
    from spammers.gmail.ratelimit import guard
    resp = await guard(request)
    return resp if resp is not None else await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="Gmail mock", lifespan=_lifespan,
                  default_response_class=GoogleJSONResponse)
    app.middleware("http")(_ratelimit)
    app.include_router(_token.router)
    app.include_router(_jwks.router)
    app.include_router(_messages.router)
    app.include_router(_threads.router)
    app.include_router(_history.router)
    app.include_router(_mailbox.router)
    app.include_router(_directory.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "gmail-mock"}

    return app


app = create_app()
