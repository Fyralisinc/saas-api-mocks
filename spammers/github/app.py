"""GitHub mock — FastAPI app factory.

Usage:
    python -m spammers.github run --port 7003
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from spammers.github import state as _state
from spammers.github.responses import GitHubJSONResponse
from spammers.github.routes import (
    app_api as _app_api,
    install as _install,
    installation as _installation,
    repo_content as _repo_content,
    repos as _repos,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await _state.startup()
    yield
    await _state.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="GitHub mock", lifespan=_lifespan, default_response_class=GitHubJSONResponse)
    app.include_router(_install.router)
    app.include_router(_app_api.router)
    app.include_router(_installation.router)
    app.include_router(_repo_content.router)
    app.include_router(_repos.router)

    @app.get("/_health")
    async def health():
        return {"ok": True, "service": "github-mock"}

    return app


app = create_app()
