"""Outbound Discord interaction delivery (Director emission path).

When the orchestrator drains a live ``discord.interaction`` event, this module
builds the interaction object Discord would POST to a consumer's Interactions
Endpoint URL, signs it (Ed25519 over ``timestamp + body``), and delivers it with
``X-Signature-Ed25519`` / ``X-Signature-Timestamp`` — the headers a consumer
verifies with the application's public key.

Unlike the Gateway (push-only, owned by the mock process), interactions are an
HTTP request/response flow, so they ride the same ``deliver`` path as the Slack
and GitHub webhooks.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import UUID

import asyncpg
import structlog

from spammers.common.ids import discord_snowflake
from spammers.common.signing import discord_sign
from spammers.common.webhook_emitter import deliver, mark_emitted

log = structlog.get_logger("spammers.discord.interactions_out")


async def emit(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    discord_interactions_url: str,
) -> tuple[int, str]:
    """Fetch a ``discord.interaction`` event and POST a signed interaction."""
    ev = await pool.fetchrow(
        "SELECT type, payload, virtual_ts, actor_id FROM timeline.events WHERE id = $1",
        event_id,
    )
    if ev is None:
        raise LookupError(f"discord interaction event not found: {event_id}")
    payload = ev["payload"] if isinstance(ev["payload"], dict) else json.loads(ev["payload"])

    app = await pool.fetchrow(
        """
        SELECT a.id AS application_pk, a.application_id, a.private_key,
               g.guild_id
          FROM app_discord.applications a
          LEFT JOIN app_discord.guilds g ON g.application_pk = a.id
         WHERE a.run_id = $1
         LIMIT 1
        """,
        run_id,
    )
    if app is None:
        raise LookupError(f"no discord application for run {run_id}")

    envelope = await _build_interaction(pool, app, payload, ev["actor_id"])
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time()))
    private_key = app["private_key"]

    def sign(b: bytes) -> Mapping[str, str]:
        return {
            "X-Signature-Ed25519": discord_sign(private_key, ts, b),
            "X-Signature-Timestamp": ts,
        }

    status, text = await deliver(url=discord_interactions_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("discord_interaction_emitted", event_id=str(event_id),
             interaction_type=envelope["type"], status=status)
    return status, text


async def _build_interaction(
    pool: asyncpg.Pool, app: asyncpg.Record, payload: dict, actor_id: UUID
) -> dict[str, Any]:
    interaction_type = int(payload.get("interaction_type", 2))
    application_id = app["application_id"]
    base: dict[str, Any] = {
        "id": discord_snowflake(),
        "application_id": application_id,
        "type": interaction_type,
        "token": f"mock.{discord_snowflake()}",
        "version": 1,
    }
    if interaction_type == 1:  # PING — minimal body
        return base

    # Resolve a channel + the acting member's user for command/component types.
    channel_name = (payload.get("channel") or "general").lstrip("#")
    chan = await pool.fetchrow(
        """
        SELECT c.channel_id FROM app_discord.channels c
          JOIN app_discord.guilds g ON g.id = c.guild_pk
         WHERE g.application_pk = $1 AND c.name = $2
         LIMIT 1
        """,
        app["application_pk"], channel_name,
    )
    member_user = await _member_user(pool, app["application_pk"], actor_id)

    base.update({
        "guild_id": app["guild_id"],
        "channel_id": chan["channel_id"] if chan else None,
        "member": {
            "user": member_user,
            "roles": [],
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "deaf": False,
            "mute": False,
            "pending": False,
        },
        "app_permissions": "562949953421311",
        "locale": "en-US",
        "guild_locale": "en-US",
    })

    command = payload.get("command", "ping")
    if interaction_type == 2:  # APPLICATION_COMMAND
        cmd = await pool.fetchrow(
            "SELECT command_id FROM app_discord.commands WHERE application_pk = $1 AND name = $2",
            app["application_pk"], command,
        )
        base["data"] = {
            "id": cmd["command_id"] if cmd else discord_snowflake(),
            "name": command,
            "type": 1,
        }
    elif interaction_type == 3:  # MESSAGE_COMPONENT
        base["data"] = {
            "custom_id": command,
            "component_type": 2,  # button
        }
    return base


async def _member_user(pool: asyncpg.Pool, application_pk: UUID, person_id: UUID) -> dict[str, Any]:
    row = await pool.fetchrow(
        """
        SELECT u.discord_user_id, u.username, u.discriminator, u.avatar_hash, p.full_name
          FROM app_discord.users u
          JOIN org.people p ON p.id = u.person_id
         WHERE u.application_pk = $1 AND u.person_id = $2
        """,
        application_pk, person_id,
    )
    if row is None:
        return {"id": "0", "username": "unknown", "discriminator": "0", "global_name": None,
                "avatar": None, "bot": False, "public_flags": 0, "primary_guild": None}
    return {
        "id": row["discord_user_id"],
        "username": row["username"],
        "discriminator": row["discriminator"] or "0",
        "global_name": row["full_name"] or row["username"],
        "avatar": row["avatar_hash"],
        "bot": False,
        "public_flags": 0,
        "primary_guild": None,
    }
