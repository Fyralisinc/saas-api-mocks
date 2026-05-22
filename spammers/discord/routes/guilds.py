"""Guild endpoints: ``GET /guilds/{id}``, ``/guilds/{id}/channels``,
``/guilds/{id}/members/{user}``."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import discord_error
from spammers.discord.dto import channel_dto, guild_dto, member_dto, user_dto
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.routes._deps import authed
from spammers.discord.state import state

router = APIRouter()


async def _guild_row(application_pk, guild_id: str):
    return await state().pool.fetchrow(
        "SELECT id, guild_id, name, icon_hash, owner_user_id, created_at "
        "FROM app_discord.guilds WHERE application_pk = $1 AND guild_id = $2",
        application_pk, guild_id,
    )


@router.get("/api/v10/guilds/{guild_id}")
async def get_guild(request: Request, guild_id: str):
    app, headers, err = await authed(request, "guilds/{id}")
    if err is not None:
        return err
    g = await _guild_row(app["application_pk"], guild_id)
    if g is None:
        return DiscordJSONResponse(discord_error(10004, "Unknown Guild"), status_code=404, headers=headers)
    return DiscordJSONResponse(guild_dto(dict(g)), headers=headers)


@router.get("/api/v10/guilds/{guild_id}/channels")
async def list_channels(request: Request, guild_id: str):
    app, headers, err = await authed(request, "guilds/{id}/channels")
    if err is not None:
        return err
    g = await _guild_row(app["application_pk"], guild_id)
    if g is None:
        return DiscordJSONResponse(discord_error(10004, "Unknown Guild"), status_code=404, headers=headers)
    rows = await state().pool.fetch(
        "SELECT channel_id, name, type, parent_id, topic, nsfw "
        "FROM app_discord.channels WHERE guild_pk = $1 ORDER BY created_at",
        g["id"],
    )
    return DiscordJSONResponse(
        [channel_dto(dict(r), guild_id=guild_id) for r in rows], headers=headers,
    )


@router.get("/api/v10/guilds/{guild_id}/members/{user_id}")
async def get_member(request: Request, guild_id: str, user_id: str):
    app, headers, err = await authed(request, "guilds/{id}/members/{user}")
    if err is not None:
        return err
    g = await _guild_row(app["application_pk"], guild_id)
    if g is None:
        return DiscordJSONResponse(discord_error(10004, "Unknown Guild"), status_code=404, headers=headers)
    row = await state().pool.fetchrow(
        """
        SELECT u.discord_user_id, u.username, u.discriminator, u.avatar_hash,
               u.is_bot, p.full_name
          FROM app_discord.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.application_pk = $1 AND u.discord_user_id = $2
        """,
        app["application_pk"], user_id,
    )
    if row is None:
        return DiscordJSONResponse(discord_error(10007, "Unknown Member"), status_code=404, headers=headers)
    return DiscordJSONResponse(
        member_dto(user_dto(dict(row)), joined_at=g["created_at"]), headers=headers,
    )
