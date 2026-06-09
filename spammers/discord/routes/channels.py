"""Channel + message endpoints.

``GET /channels/{id}``;
``GET/POST /channels/{id}/messages``;
``GET/PATCH/DELETE /channels/{id}/messages/{mid}``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query, Request

from spammers.common.clock import get_clock
from spammers.common.errors import discord_error
from spammers.common.ids import discord_snowflake
from spammers.common.pagination import discord_filter_by_snowflake
from spammers.discord.dto import bot_user_dto, channel_dto, message_dto, user_dto
from spammers.discord.messages import project_message
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.routes._deps import authed
from spammers.discord.state import state

router = APIRouter()


async def _channel(application_pk, channel_id: str) -> Optional[dict]:
    row = await state().pool.fetchrow(
        """
        SELECT c.id AS channel_pk, c.channel_id, c.name, c.type, c.parent_id,
               c.topic, c.nsfw, c.created_at, g.guild_id
          FROM app_discord.channels c
          JOIN app_discord.guilds g ON g.id = c.guild_pk
         WHERE g.application_pk = $1 AND c.channel_id = $2
        """,
        application_pk, channel_id,
    )
    return dict(row) if row else None


def _author_obj(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("discord_user_id"):
        return user_dto(row)
    return {
        "id": "0", "username": "deleted_user", "discriminator": "0000",
        "global_name": None, "avatar": None, "bot": False, "system": False,
        "public_flags": 0, "flags": 0, "primary_guild": None,
    }


def _row_to_msg(row: dict[str, Any], *, channel_id: str, guild_id: str) -> dict[str, Any]:
    return message_dto(row, author=_author_obj(row), channel_id=channel_id, guild_id=guild_id)


@router.get("/api/v10/channels/{channel_id}")
async def get_channel(request: Request, channel_id: str):
    app, headers, err = await authed(request, "channels/{id}")
    if err is not None:
        return err
    c = await _channel(app["application_pk"], channel_id)
    if c is None:
        return DiscordJSONResponse(discord_error(10003, "Unknown Channel"), status_code=404, headers=headers)
    return DiscordJSONResponse(channel_dto(c, guild_id=c["guild_id"]), headers=headers)


@router.get("/api/v10/channels/{channel_id}/messages")
async def list_messages(
    request: Request,
    channel_id: str,
    limit: int = Query(50, ge=1, le=100),
    before: Optional[str] = Query(None),
    after: Optional[str] = Query(None),
    around: Optional[str] = Query(None),
):
    app, headers, err = await authed(request, "channels/{id}/messages")
    if err is not None:
        return err
    c = await _channel(app["application_pk"], channel_id)
    if c is None:
        return DiscordJSONResponse(discord_error(10003, "Unknown Channel"), status_code=404, headers=headers)
    rows = await state().pool.fetch(
        """
        SELECT m.message_id, m.content, m.type, m.pinned, m.mentions, m.attachments,
               m.embeds, m.reactions, m.referenced_message_id, m.created_at, m.edited_at,
               u.discord_user_id, u.username, u.discriminator, u.avatar_hash, u.is_bot,
               p.full_name
          FROM app_discord.messages m
          LEFT JOIN app_discord.users u ON u.id = m.author_user_pk
          LEFT JOIN org.people p ON p.id = u.person_id
         WHERE m.channel_pk = $1
        """,
        c["channel_pk"],
    )
    items = [dict(r) for r in rows]
    page = discord_filter_by_snowflake(
        items, before=before, after=after, around=around, limit=limit, id_field="message_id",
    )
    return DiscordJSONResponse(
        [_row_to_msg(r, channel_id=channel_id, guild_id=c["guild_id"]) for r in page],
        headers=headers,
    )


@router.get("/api/v10/channels/{channel_id}/messages/{message_id}")
async def get_message(request: Request, channel_id: str, message_id: str):
    app, headers, err = await authed(request, "channels/{id}/messages/{mid}")
    if err is not None:
        return err
    c = await _channel(app["application_pk"], channel_id)
    if c is None:
        return DiscordJSONResponse(discord_error(10003, "Unknown Channel"), status_code=404, headers=headers)
    row = await _fetch_one(c["channel_pk"], message_id)
    if row is None:
        return DiscordJSONResponse(discord_error(10008, "Unknown Message"), status_code=404, headers=headers)
    return DiscordJSONResponse(_row_to_msg(row, channel_id=channel_id, guild_id=c["guild_id"]), headers=headers)


@router.post("/api/v10/channels/{channel_id}/messages")
async def create_message(request: Request, channel_id: str):
    app, headers, err = await authed(request, "channels/{id}/messages")
    if err is not None:
        return err
    c = await _channel(app["application_pk"], channel_id)
    if c is None:
        return DiscordJSONResponse(discord_error(10003, "Unknown Channel"), status_code=404, headers=headers)

    body = await _json_body(request)
    content = str(body.get("content", "") or "")
    embeds = body.get("embeds") or []

    clock = await get_clock(state().pool, state().run_id)
    when = clock.virtual_now
    message_id = discord_snowflake(when)
    await project_message(
        state().pool,
        channel_pk=c["channel_pk"],
        message_id=message_id,
        author_user_pk=None,  # authored by the bot (the application), not a person
        content=content,
        created_at=when,
        embeds=embeds,
    )
    msg = message_dto(
        {
            "message_id": message_id, "content": content, "created_at": when,
            "edited_at": None, "type": 0, "pinned": False, "mentions": [],
            "attachments": [], "embeds": embeds, "reactions": [],
        },
        author=bot_user_dto(app["application_id"]),
        channel_id=channel_id,
        guild_id=c["guild_id"],
    )
    return DiscordJSONResponse(msg, headers=headers)


@router.patch("/api/v10/channels/{channel_id}/messages/{message_id}")
async def edit_message(request: Request, channel_id: str, message_id: str):
    app, headers, err = await authed(request, "channels/{id}/messages/{mid}")
    if err is not None:
        return err
    c = await _channel(app["application_pk"], channel_id)
    if c is None:
        return DiscordJSONResponse(discord_error(10003, "Unknown Channel"), status_code=404, headers=headers)
    body = await _json_body(request)
    content = str(body.get("content", "") or "")
    now = datetime.now(timezone.utc)
    updated = await state().pool.fetchrow(
        "UPDATE app_discord.messages SET content = $3, edited_at = $4 "
        "WHERE channel_pk = $1 AND message_id = $2 RETURNING message_id",
        c["channel_pk"], message_id, content, now,
    )
    if updated is None:
        return DiscordJSONResponse(discord_error(10008, "Unknown Message"), status_code=404, headers=headers)
    row = await _fetch_one(c["channel_pk"], message_id)
    return DiscordJSONResponse(_row_to_msg(row, channel_id=channel_id, guild_id=c["guild_id"]), headers=headers)


@router.delete("/api/v10/channels/{channel_id}/messages/{message_id}")
async def delete_message(request: Request, channel_id: str, message_id: str):
    app, headers, err = await authed(request, "channels/{id}/messages/{mid}")
    if err is not None:
        return err
    c = await _channel(app["application_pk"], channel_id)
    if c is None:
        return DiscordJSONResponse(discord_error(10003, "Unknown Channel"), status_code=404, headers=headers)
    deleted = await state().pool.fetchrow(
        "DELETE FROM app_discord.messages WHERE channel_pk = $1 AND message_id = $2 RETURNING message_id",
        c["channel_pk"], message_id,
    )
    if deleted is None:
        return DiscordJSONResponse(discord_error(10008, "Unknown Message"), status_code=404, headers=headers)
    return DiscordJSONResponse(None, status_code=204, headers=headers)


async def _fetch_one(channel_pk, message_id: str) -> Optional[dict]:
    row = await state().pool.fetchrow(
        """
        SELECT m.message_id, m.content, m.type, m.pinned, m.mentions, m.attachments,
               m.embeds, m.reactions, m.referenced_message_id, m.created_at, m.edited_at,
               u.discord_user_id, u.username, u.discriminator, u.avatar_hash, u.is_bot,
               p.full_name
          FROM app_discord.messages m
          LEFT JOIN app_discord.users u ON u.id = m.author_user_pk
          LEFT JOIN org.people p ON p.id = u.person_id
         WHERE m.channel_pk = $1 AND m.message_id = $2
        """,
        channel_pk, message_id,
    )
    return dict(row) if row else None


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        return json.loads(raw or b"{}")
    except Exception:
        return {}
