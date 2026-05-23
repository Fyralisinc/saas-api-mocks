"""User endpoints: ``GET /users/@me`` and ``GET /users/{id}``."""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.common.errors import discord_error
from spammers.discord.dto import bot_user_dto, partial_guild_dto, user_dto
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.routes._deps import authed
from spammers.discord.state import state

router = APIRouter()


@router.get("/api/v10/users/@me")
async def get_me(request: Request):
    app, headers, err = await authed(request, "users/@me")
    if err is not None:
        return err
    return DiscordJSONResponse(bot_user_dto(app["application_id"]), headers=headers)


@router.get("/api/v10/users/@me/guilds")
async def get_my_guilds(request: Request):
    app, headers, err = await authed(request, "users/@me/guilds")
    if err is not None:
        return err
    rows = await state().pool.fetch(
        "SELECT guild_id, name, icon_hash, owner_user_id "
        "FROM app_discord.guilds WHERE application_pk = $1 ORDER BY created_at",
        app["application_pk"],
    )
    guilds = [partial_guild_dto(dict(r), bot_id=app["application_id"]) for r in rows]
    return DiscordJSONResponse(guilds, headers=headers)


@router.get("/api/v10/users/{user_id}")
async def get_user(request: Request, user_id: str):
    app, headers, err = await authed(request, "users/{id}")
    if err is not None:
        return err
    if user_id == "@me":
        return DiscordJSONResponse(bot_user_dto(app["application_id"]), headers=headers)
    st = state()
    row = await st.pool.fetchrow(
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
        return DiscordJSONResponse(discord_error(10013, "Unknown User"), status_code=404, headers=headers)
    return DiscordJSONResponse(user_dto(dict(row)), headers=headers)
