"""Projection of messages into ``app_discord.messages`` for read APIs.

Both the REST write path (``POST /channels/{id}/messages``) and the Gateway
dispatcher persist messages here so later ``GET /channels/{id}/messages`` reads
return them — mirroring how the Slack mock projects into ``app_slack.messages``.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

import asyncpg


async def project_message(
    conn: asyncpg.Connection | asyncpg.Pool,
    *,
    channel_pk: UUID,
    message_id: str,
    author_user_pk: Optional[UUID],
    content: str,
    created_at: datetime,
    msg_type: int = 0,
    mentions: Optional[list[Any]] = None,
    attachments: Optional[list[Any]] = None,
    embeds: Optional[list[Any]] = None,
    referenced_message_id: Optional[str] = None,
    timeline_event_id: Optional[UUID] = None,
) -> UUID:
    """Insert a message row (idempotent on ``(channel_pk, message_id)``)."""
    pk = uuid4()
    await conn.execute(
        """
        INSERT INTO app_discord.messages
            (id, channel_pk, message_id, author_user_pk, content, type, pinned,
             mentions, attachments, embeds, reactions, referenced_message_id,
             created_at, timeline_event_id)
        VALUES ($1, $2, $3, $4, $5, $6, FALSE,
                $7::jsonb, $8::jsonb, $9::jsonb, '[]'::jsonb, $10, $11, $12)
        ON CONFLICT (channel_pk, message_id) DO NOTHING
        """,
        pk, channel_pk, message_id, author_user_pk, content, msg_type,
        json.dumps(mentions or []), json.dumps(attachments or []),
        json.dumps(embeds or []), referenced_message_id, created_at,
        timeline_event_id,
    )
    return pk
