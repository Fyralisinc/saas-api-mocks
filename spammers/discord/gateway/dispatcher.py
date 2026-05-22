"""GatewayDispatcher — turns live ``discord.message`` events into MESSAGE_CREATE.

The mock owns this loop (not the Director) because the Gateway WebSocket
connections live in this process. It polls the shared virtual clock, claims
``discord.message`` timeline rows (``FOR UPDATE SKIP LOCKED``, marked emitted on
claim — gateway push is fire-and-forget), projects each into
``app_discord.messages`` for later REST reads, and fans the event out to
connected sessions.

No historical replay: a watermark captured at startup means messages at or
before the dispatcher's start time are projected + marked but **never**
dispatched, matching real Discord (a bot connecting late does not receive a
flood of past messages).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

import asyncpg
import structlog

from spammers.common.clock import get_clock
from spammers.common.ids import discord_snowflake
from spammers.discord.dto import message_dto, user_dto
from spammers.discord.gateway.opcodes import Intents
from spammers.discord.messages import project_message

log = structlog.get_logger("spammers.discord.dispatcher")

_CLAIM_SQL = """
    UPDATE timeline.events SET emitted_at = now()
     WHERE id IN (
        SELECT id FROM timeline.events
         WHERE run_id = $1
           AND type = 'discord.message'
           AND is_historical = FALSE
           AND emitted_at IS NULL
           AND virtual_ts <= $2
         ORDER BY virtual_ts ASC
         LIMIT $3
         FOR UPDATE SKIP LOCKED
     )
    RETURNING id, payload, virtual_ts, actor_id
"""


class GatewayDispatcher:
    def __init__(
        self,
        pool: asyncpg.Pool,
        run_id: UUID,
        hub,
        *,
        poll_interval_s: float = 0.5,
        batch_size: int = 20,
    ) -> None:
        self._pool = pool
        self._run_id = run_id
        self._hub = hub
        self._poll_interval_s = poll_interval_s
        self._batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._watermark: Optional[datetime] = None

    async def _drain_once(self) -> int:
        clock = await get_clock(self._pool, self._run_id)
        if self._watermark is None:
            self._watermark = clock.virtual_now  # no replay of pre-start messages
        rows = await self._pool.fetch(
            _CLAIM_SQL, self._run_id, clock.virtual_now, self._batch_size,
        )
        for row in rows:
            try:
                await self._handle(row)
            except Exception as exc:  # never let one bad event stall the loop
                log.warning("discord_dispatch_failed", event_id=str(row["id"]), error=str(exc))
        return len(rows)

    async def _handle(self, row: asyncpg.Record) -> None:
        payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
        channel_name = (payload.get("channel") or "").lstrip("#")
        content = payload.get("text", "") or ""
        virtual_ts: datetime = row["virtual_ts"]

        chan = await self._pool.fetchrow(
            """
            SELECT c.id AS channel_pk, c.channel_id, c.type,
                   g.guild_id, g.application_pk
              FROM app_discord.channels c
              JOIN app_discord.guilds g ON g.id = c.guild_pk
              JOIN app_discord.applications a ON a.id = g.application_pk
             WHERE a.run_id = $1 AND c.name = $2
             LIMIT 1
            """,
            self._run_id, channel_name,
        )
        if chan is None:
            log.warning("discord_dispatch_no_channel", channel=channel_name)
            return

        author = await self._pool.fetchrow(
            """
            SELECT u.id AS author_user_pk, u.discord_user_id, u.username,
                   u.discriminator, u.avatar_hash, u.is_bot, p.full_name
              FROM app_discord.users u
              JOIN org.people p ON p.id = u.person_id
             WHERE u.application_pk = $1 AND u.person_id = $2
            """,
            chan["application_pk"], row["actor_id"],
        )

        message_id = discord_snowflake(virtual_ts)
        await project_message(
            self._pool,
            channel_pk=chan["channel_pk"],
            message_id=message_id,
            author_user_pk=author["author_user_pk"] if author else None,
            content=content,
            created_at=virtual_ts,
            timeline_event_id=row["id"],
        )

        # No historical replay: only dispatch events strictly after the watermark.
        if self._watermark is not None and virtual_ts <= self._watermark:
            return

        author_obj = user_dto(dict(author)) if author else _system_author()
        full = message_dto(
            {
                "message_id": message_id,
                "content": content,
                "created_at": virtual_ts,
                "edited_at": None,
                "type": 0,
                "pinned": False,
                "mentions": [],
                "attachments": [],
                "embeds": [],
                "reactions": [],
            },
            author=author_obj,
            channel_id=chan["channel_id"],
            guild_id=chan["guild_id"],
        )

        def payload_for(session) -> dict[str, Any]:
            if session.intents & Intents.MESSAGE_CONTENT:
                return full
            gated = dict(full)
            gated["content"] = ""
            gated["embeds"] = []
            gated["attachments"] = []
            return gated

        self._hub.fan_out(
            chan["application_pk"], "MESSAGE_CREATE", payload_for,
            required_intent=Intents.GUILD_MESSAGES,
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self._drain_once()
                self._hub.reap()
                if n == 0:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_s)
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                log.warning("discord_dispatcher_loop_error", error=str(exc))
                await asyncio.sleep(self._poll_interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None


def _system_author() -> dict[str, Any]:
    return {
        "id": "0", "username": "system", "discriminator": "0000",
        "global_name": "system", "avatar": None, "bot": True, "system": True,
        "public_flags": 0, "flags": 0,
    }
