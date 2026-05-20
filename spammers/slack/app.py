"""Slack mock — FastAPI app factory.

Usage:
    uvicorn spammers.slack.app:app --port 7001
or
    python -m spammers.slack run --port 7001
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from spammers.slack import state as _state
from spammers.slack.responses import SlackJSONResponse
from spammers.slack.routes import (
    auth_test as _auth_test,
    chat as _chat,
    conversations as _conversations,
    oauth as _oauth,
    team as _team,
    users as _users,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Slack mock", lifespan=_lifespan, default_response_class=SlackJSONResponse)
    app.include_router(_oauth.router)
    app.include_router(_auth_test.router)
    app.include_router(_users.router)
    app.include_router(_team.router)
    app.include_router(_conversations.router)
    app.include_router(_chat.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "slack-mock"}

    return app


app = create_app()
