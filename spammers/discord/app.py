"""Discord mock — FastAPI app factory.

Usage:
    python -m spammers.discord run --port 7002

The lifespan boots per-process state and starts the in-process
``GatewayDispatcher`` (the background task that turns live ``discord.message``
timeline events into ``MESSAGE_CREATE`` dispatches over connected Gateway
WebSockets). Tests build the app with :func:`create_app` and drive state +
dispatcher by hand (ASGITransport skips lifespan).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from spammers.discord import state as _state
from spammers.discord.gateway.connection import gateway_endpoint
from spammers.discord.gateway.dispatcher import GatewayDispatcher
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.routes import (
    channels as _channels,
    commands as _commands,
    gateway_http as _gateway_http,
    guilds as _guilds,
    interactions_in as _interactions_in,
    oauth as _oauth,
    users as _users,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    st = await _state.startup()
    st.dispatcher = GatewayDispatcher(st.pool, st.run_id, st.hub)
    st.dispatcher.start()
    yield
    await _state.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="Discord mock", lifespan=_lifespan, default_response_class=DiscordJSONResponse)
    app.include_router(_oauth.router)
    app.include_router(_gateway_http.router)
    app.include_router(_users.router)
    app.include_router(_guilds.router)
    app.include_router(_channels.router)
    app.include_router(_commands.router)
    app.include_router(_interactions_in.router)
    app.add_api_websocket_route("/gateway", gateway_endpoint)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "discord-mock"}

    return app


app = create_app()
