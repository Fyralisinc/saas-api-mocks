"""Application (slash) command registration.

``GET/POST /applications/{app}/commands`` (global) and the guild-scoped
``GET/POST /applications/{app}/guilds/{gid}/commands``. Registration upserts
``app_discord.commands`` by name (mirroring Discord's create-or-update by name).
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request

from spammers.common.errors import discord_error
from spammers.common.ids import discord_snowflake
from spammers.discord.dto import command_dto
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.routes._deps import authed
from spammers.discord.state import state

router = APIRouter()


async def _list_commands(application_pk, application_id: str) -> list[dict[str, Any]]:
    rows = await state().pool.fetch(
        "SELECT command_id, name, description, type, options "
        "FROM app_discord.commands WHERE application_pk = $1 ORDER BY name",
        application_pk,
    )
    return [command_dto(dict(r), application_id) for r in rows]


async def _upsert(application_pk, body: dict[str, Any]) -> dict[str, Any]:
    name = str(body.get("name", "")).strip()
    description = str(body.get("description", "") or "")
    cmd_type = int(body.get("type", 1) or 1)
    options = body.get("options") or []
    command_id = discord_snowflake()
    row = await state().pool.fetchrow(
        """
        INSERT INTO app_discord.commands
            (id, application_pk, command_id, name, description, type, options)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (application_pk, name) DO UPDATE
          SET description = EXCLUDED.description,
              type = EXCLUDED.type,
              options = EXCLUDED.options
        RETURNING command_id, name, description, type, options
        """,
        uuid4(), application_pk, command_id, name, description, cmd_type, json.dumps(options),
    )
    return dict(row)


@router.get("/api/v10/applications/{app_id}/commands")
async def list_global(request: Request, app_id: str):
    app, headers, err = await authed(request, "applications/{app}/commands")
    if err is not None:
        return err
    return DiscordJSONResponse(await _list_commands(app["application_pk"], app["application_id"]), headers=headers)


@router.post("/api/v10/applications/{app_id}/commands")
async def create_global(request: Request, app_id: str):
    app, headers, err = await authed(request, "applications/{app}/commands")
    if err is not None:
        return err
    body = await _json_body(request)
    if not body.get("name"):
        return DiscordJSONResponse(
            discord_error(50035, "Invalid Form Body", errors={"name": {"_errors": [{"code": "BASE_TYPE_REQUIRED", "message": "This field is required"}]}}),
            status_code=400, headers=headers,
        )
    row = await _upsert(app["application_pk"], body)
    return DiscordJSONResponse(command_dto(row, app["application_id"]), status_code=201, headers=headers)


@router.get("/api/v10/applications/{app_id}/guilds/{guild_id}/commands")
async def list_guild(request: Request, app_id: str, guild_id: str):
    app, headers, err = await authed(request, "applications/{app}/guilds/{gid}/commands")
    if err is not None:
        return err
    return DiscordJSONResponse(await _list_commands(app["application_pk"], app["application_id"]), headers=headers)


@router.post("/api/v10/applications/{app_id}/guilds/{guild_id}/commands")
async def create_guild(request: Request, app_id: str, guild_id: str):
    app, headers, err = await authed(request, "applications/{app}/guilds/{gid}/commands")
    if err is not None:
        return err
    body = await _json_body(request)
    if not body.get("name"):
        return DiscordJSONResponse(discord_error(50035, "Invalid Form Body"), status_code=400, headers=headers)
    row = await _upsert(app["application_pk"], body)
    return DiscordJSONResponse(command_dto(row, app["application_id"]), status_code=201, headers=headers)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
