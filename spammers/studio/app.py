"""Studio FastAPI app — control + observe the mocks behind a single page.

Isolated from the mock runtime: the slack/discord/github apps never import
this module. It owns a DB pool (read-only queries + the public inject
helpers) and a process Controller that drives reset/prepare and the mock
server processes.
"""
from __future__ import annotations

import contextlib
import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI, Body
from fastapi.responses import FileResponse, JSONResponse

from spammers.common.db import create_pool
from spammers.studio import companies, narrative, queries
from spammers.studio.controller import Controller

_STATIC = Path(__file__).parent / "static"

controller = Controller()
_pool: asyncpg.Pool | None = None


def _pool_or_none() -> asyncpg.Pool | None:
    return _pool


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _pool
    with contextlib.suppress(Exception):
        _pool = await create_pool()
    try:
        yield
    finally:
        controller.shutdown()
        if _pool is not None:
            with contextlib.suppress(Exception):
                await _pool.close()


app = FastAPI(title="Spammer Studio", lifespan=_lifespan)


@app.get("/")
async def index():
    # No-cache so the single-page UI always reflects the latest build (the file
    # changes as providers/panels are added); avoids stale cached JS.
    return FileResponse(
        _STATIC / "index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/api/companies")
async def api_companies():
    return {"companies": companies.all_companies()}


@app.get("/api/status")
async def api_status():
    controller.refresh_server_health()
    return controller.state.public()


@app.post("/api/start")
async def api_start(body: dict = Body(...)):
    key = (body or {}).get("company")
    if not key:
        return JSONResponse({"error": "company is required"}, status_code=400)
    try:
        return await controller.start(key)
    except Exception as e:
        return JSONResponse({"error": str(e), **controller.state.public()}, status_code=500)


@app.post("/api/stop")
async def api_stop():
    try:
        return await controller.stop()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/state")
async def api_state():
    """Full dossier + per-provider status + actors, for the running company."""
    if not controller.state.running or not controller.state.company_key:
        return {"running": False}
    pool = _pool_or_none()
    if pool is None:
        return JSONResponse({"error": "no db pool"}, status_code=503)
    try:
        run_id = await queries.current_run_id(pool)
        if run_id is None:
            return {"running": False}
        company = companies.get(controller.state.company_key)
        dossier = await narrative.build(pool, run_id, company)
        status = await queries.provider_status(pool, run_id)
        people = await queries.list_people(pool, run_id)
        channels = {
            "slack": await queries.list_channels(pool, run_id, "slack"),
            "discord": await queries.list_channels(pool, run_id, "discord"),
        }
        repos = await queries.list_repos(pool, run_id)
        notion_databases = await queries.list_notion_databases(pool, run_id)
        return {
            "running": True, "company": company.as_dict(), "dossier": dossier,
            "status": status, "people": people, "channels": channels, "repos": repos,
            "notion_databases": notion_databases,
        }
    except asyncpg.PostgresError as e:
        return JSONResponse({"error": f"db: {e}"}, status_code=503)


@app.get("/api/credentials")
async def api_credentials():
    """Current run's tokens/IDs + base URLs, for pointing Fyralis at it."""
    if not controller.state.running:
        return {"running": False}
    pool = _pool_or_none()
    if pool is None:
        return JSONResponse({"error": "no db pool"}, status_code=503)
    run_id = await queries.current_run_id(pool)
    if run_id is None:
        return {"running": False}
    try:
        return {"running": True, "credentials": await queries.credentials(pool, run_id)}
    except asyncpg.PostgresError as e:
        return JSONResponse({"error": f"db: {e}"}, status_code=503)


@app.get("/api/suggest")
async def api_suggest(handle: str, provider: str):
    pool = _pool_or_none()
    if pool is None or not controller.state.running:
        return {"suggestions": []}
    run_id = await queries.current_run_id(pool)
    if run_id is None:
        return {"suggestions": []}
    with contextlib.suppress(asyncpg.PostgresError):
        return {"suggestions": await queries.suggestions(pool, run_id, handle, provider)}
    return {"suggestions": []}


@app.post("/api/inject")
async def api_inject(body: dict = Body(...)):
    if not controller.state.running:
        return JSONResponse({"error": "spammer is not running"}, status_code=409)
    pool = _pool_or_none()
    if pool is None:
        return JSONResponse({"error": "no db pool"}, status_code=503)
    provider = (body or {}).get("provider")
    handle = (body or {}).get("handle")
    text = (body or {}).get("text")
    if not (provider and handle and text):
        return JSONResponse({"error": "provider, handle and text are required"}, status_code=400)
    run_id = await queries.current_run_id(pool)
    if run_id is None:
        return JSONResponse({"error": "no active run"}, status_code=409)
    try:
        if provider == "slack":
            res = await queries.inject_slack(pool, run_id, handle=handle, channel=body.get("channel", "general"), text=text)
        elif provider == "discord":
            res = await queries.inject_discord(pool, run_id, handle=handle, channel=body.get("channel", "general"), text=text)
        elif provider == "github":
            res = await queries.inject_github(pool, run_id, handle=handle, repo=body.get("repo", ""), text=text)
        elif provider == "gmail":
            res = await queries.inject_gmail(pool, run_id, handle=handle, recipient=body.get("recipient", ""), text=text)
        elif provider == "calendar":
            res = await queries.inject_calendar(pool, run_id, handle=handle, attendee=body.get("attendee", ""), text=text)
        elif provider == "notion":
            res = await queries.inject_notion(pool, run_id, handle=handle, database=body.get("database", ""), text=text)
        else:
            return JSONResponse({"error": f"unknown provider: {provider}"}, status_code=400)
        return {"ok": True, "injected": res}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)
