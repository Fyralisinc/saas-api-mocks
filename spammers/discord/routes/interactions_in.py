"""Interaction response endpoints (the consumer answering an interaction).

After the mock delivers a signed interaction (see ``interactions_out.py``), the
consumer replies via:
  - ``POST /interactions/{id}/{token}/callback`` — the initial response
    (type 1 = PONG for a PING, type 4 = message, type 5 = deferred, …).
  - ``POST /webhooks/{application_id}/{token}`` — followup messages.
  - ``PATCH /webhooks/{application_id}/{token}/messages/@original`` — edit the
    original response.

These are authenticated by the interaction token in the path (not a bot token),
so the mock simply records receipt and returns Discord's status codes.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request

from spammers.common.ids import discord_snowflake
from spammers.discord.responses import DiscordJSONResponse

router = APIRouter()


@router.post("/api/v10/interactions/{interaction_id}/{token}/callback")
async def callback(request: Request, interaction_id: str, token: str, with_response: bool = Query(False)):
    body = await _json_body(request)
    # type 1 = PONG (ack a PING), 4 = CHANNEL_MESSAGE_WITH_SOURCE, 5 = DEFERRED, …
    if with_response:
        return DiscordJSONResponse({
            "interaction": {"id": interaction_id, "type": int(body.get("type", 4))},
            "resource": {"type": 0},
        }, status_code=200)
    return DiscordJSONResponse(None, status_code=204)


@router.post("/api/v10/webhooks/{application_id}/{token}")
async def followup(request: Request, application_id: str, token: str):
    body = await _json_body(request)
    return DiscordJSONResponse({
        "id": discord_snowflake(),
        "type": 20,  # APPLICATION_COMMAND reply
        "content": str(body.get("content", "") or ""),
        "application_id": application_id,
        "embeds": body.get("embeds") or [],
        "flags": int(body.get("flags", 0) or 0),
    }, status_code=200)


@router.patch("/api/v10/webhooks/{application_id}/{token}/messages/{message_id}")
async def edit_followup(request: Request, application_id: str, token: str, message_id: str):
    body = await _json_body(request)
    return DiscordJSONResponse({
        "id": message_id if message_id != "@original" else discord_snowflake(),
        "type": 20,
        "content": str(body.get("content", "") or ""),
        "application_id": application_id,
        "embeds": body.get("embeds") or [],
    }, status_code=200)


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        raw = await request.body()
        data = json.loads(raw or b"{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
