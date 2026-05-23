"""Discord object shapes (REST + Gateway).

Builders take ``app_discord.*`` rows (as dicts) and return JSON matching the real
Discord API, so a consumer can't tell the mock apart from the real service.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional


def _as_list(v: Any) -> list[Any]:
    """Coerce a JSONB column (asyncpg returns these as ``str``) to a list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _avatar_url(user_id: str, avatar_hash: Optional[str]) -> Optional[str]:
    if not avatar_hash:
        return None
    return f"https://cdn.discordapp.com/avatars/{user_id}/{avatar_hash}.png"


def user_dto(row: dict[str, Any]) -> dict[str, Any]:
    """A Discord user object from an ``app_discord.users`` row."""
    uid = row["discord_user_id"]
    username = row["username"]
    return {
        "id": uid,
        "username": username,
        "discriminator": row.get("discriminator", "0") or "0",
        "global_name": row.get("full_name") or username,
        "avatar": row.get("avatar_hash"),
        "bot": bool(row.get("is_bot", False)),
        "system": False,
        "public_flags": 0,
        "flags": 0,
        "primary_guild": None,
    }


def bot_user_dto(application_id: str, *, username: str = "Fyralis", discriminator: str = "0000") -> dict[str, Any]:
    """The bot's own user object (``GET /users/@me``, gateway READY)."""
    return {
        "id": application_id,
        "username": username,
        "discriminator": discriminator,
        "global_name": None,
        "avatar": None,
        "bot": True,
        "system": False,
        "verified": True,
        "mfa_enabled": True,
        "public_flags": 0,
        "flags": 0,
        "primary_guild": None,
    }


def guild_dto(row: dict[str, Any]) -> dict[str, Any]:
    """A (mostly-complete) guild object from an ``app_discord.guilds`` row."""
    gid = row["guild_id"]
    return {
        "id": gid,
        "name": row["name"],
        "icon": row.get("icon_hash"),
        "icon_hash": row.get("icon_hash"),
        "splash": None,
        "discovery_splash": None,
        "owner_id": row.get("owner_user_id"),
        "afk_channel_id": None,
        "afk_timeout": 300,
        "widget_enabled": False,
        "verification_level": 1,
        "default_message_notifications": 0,
        "explicit_content_filter": 0,
        "roles": [{
            "id": gid,  # @everyone role shares the guild id
            "name": "@everyone",
            "permissions": "559623605571137",
            "position": 0,
            "color": 0,
            "colors": {"primary_color": 0, "secondary_color": None, "tertiary_color": None},
            "hoist": False,
            "managed": False,
            "mentionable": False,
            "description": None,
            "icon": None,
            "unicode_emoji": None,
            "flags": 0,
        }],
        "emojis": [],
        "features": [],
        "mfa_level": 0,
        "application_id": None,
        "system_channel_id": None,
        "system_channel_flags": 0,
        "rules_channel_id": None,
        "vanity_url_code": None,
        "description": None,
        "banner": None,
        "premium_tier": 0,
        "premium_subscription_count": 0,
        "preferred_locale": "en-US",
        "nsfw_level": 0,
        "premium_progress_bar_enabled": False,
        # Fields the official GuildResponse schema also marks required.
        "nsfw": False,
        "region": "deprecated",  # voice region is deprecated; Discord returns this literal
        "home_header": None,
        "incidents_data": None,
        "stickers": [],
        "max_members": 25000000,
        "max_presences": None,
        "max_video_channel_users": 25,
        "max_stage_video_channel_users": 50,
        "public_updates_channel_id": None,
        "safety_alerts_channel_id": None,
        "widget_channel_id": None,
    }


def unavailable_guild_dto(guild_id: str) -> dict[str, Any]:
    """The stub form sent in READY before the full GUILD_CREATE arrives."""
    return {"id": guild_id, "unavailable": True}


def partial_guild_dto(row: dict[str, Any], *, bot_id: str) -> dict[str, Any]:
    """A partial guild as listed by ``GET /users/@me/guilds``."""
    return {
        "id": row["guild_id"],
        "name": row["name"],
        "icon": row.get("icon_hash"),
        "banner": None,
        "owner": row.get("owner_user_id") == bot_id,
        "permissions": "562949953421311",  # full perms for the bot
        "features": [],
        "approximate_member_count": 0,
        "approximate_presence_count": 0,
    }


def application_dto(app: dict[str, Any], *, name: str = "Fyralis") -> dict[str, Any]:
    """The bot's own application object (``GET /oauth2/applications/@me``)."""
    aid = app["application_id"]
    return {
        "id": aid,
        "name": name,
        "icon": None,
        "description": "",
        "type": None,
        "bot_public": True,
        "bot_require_code_grant": False,
        "owner": bot_user_dto(aid, username=name),
        "verify_key": app.get("public_key") or "",
        "team": None,
        "flags": 0,
        "flags_new": 0,
        "rpc_origins": [],
        "redirect_uris": [],
        "interactions_endpoint_url": None,
        "role_connections_verification_url": None,
        "explicit_content_filter": 0,
        "approximate_guild_count": 1,
        "approximate_user_install_count": 0,
        "approximate_user_authorization_count": 0,
    }


def channel_dto(row: dict[str, Any], *, guild_id: Optional[str] = None) -> dict[str, Any]:
    """A guild channel object from an ``app_discord.channels`` row."""
    out: dict[str, Any] = {
        "id": row["channel_id"],
        "type": int(row.get("type", 0)),
        "name": row["name"],
        "position": int(row.get("position", 0) or 0),
        "topic": row.get("topic"),
        "nsfw": bool(row.get("nsfw", False)),
        "parent_id": row.get("parent_id"),
        "permission_overwrites": [],
        "rate_limit_per_user": 0,
        "flags": 0,
    }
    if guild_id is not None:
        out["guild_id"] = guild_id
    if int(row.get("type", 0)) == 0:
        out["last_message_id"] = None
    return out


def member_dto(user: dict[str, Any], *, joined_at: Optional[datetime] = None) -> dict[str, Any]:
    """A guild member object wrapping a user object."""
    return {
        "user": user,
        "nick": None,
        "avatar": None,
        "roles": [],
        "joined_at": _iso(joined_at) or _iso(datetime.now(timezone.utc)),
        "premium_since": None,
        "deaf": False,
        "mute": False,
        "pending": False,
        "flags": 0,
    }


def message_dto(
    row: dict[str, Any],
    *,
    author: dict[str, Any],
    channel_id: str,
    guild_id: Optional[str] = None,
) -> dict[str, Any]:
    """A message object from an ``app_discord.messages`` row + its author."""
    out: dict[str, Any] = {
        "id": row["message_id"],
        "channel_id": channel_id,
        "author": author,
        "content": row.get("content", "") or "",
        "timestamp": _iso(row.get("created_at")),
        "edited_timestamp": _iso(row.get("edited_at")),
        "tts": False,
        "mention_everyone": False,
        "mentions": _as_list(row.get("mentions")),
        "mention_roles": [],
        "attachments": _as_list(row.get("attachments")),
        "embeds": _as_list(row.get("embeds")),
        "reactions": _as_list(row.get("reactions")),
        "pinned": bool(row.get("pinned", False)),
        "type": int(row.get("type", 0)),
        "flags": 0,
        "components": [],  # required by MessageResponse; empty for plain messages
    }
    if guild_id is not None:
        out["guild_id"] = guild_id
    if row.get("referenced_message_id"):
        out["message_reference"] = {
            "message_id": row["referenced_message_id"],
            "channel_id": channel_id,
            "guild_id": guild_id,
        }
    return out


def command_dto(row: dict[str, Any], application_id: str) -> dict[str, Any]:
    """An application command object from an ``app_discord.commands`` row."""
    return {
        "id": row["command_id"],
        "application_id": application_id,
        "name": row["name"],
        "description": row.get("description", "") or "",
        "type": int(row.get("type", 1)),
        "options": _as_list(row.get("options")),
        "default_member_permissions": None,
        "dm_permission": True,
        "nsfw": False,
        "version": row["command_id"],
    }
