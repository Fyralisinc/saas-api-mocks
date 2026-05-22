"""Gateway URL discovery endpoints.

``GET /api/v10/gateway`` and ``/api/v10/gateway/bot`` tell a bot where to open
the WebSocket. ``/gateway/bot`` additionally reports recommended shard count and
the session-start rate limit.
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from spammers.discord.auth import resolve_application
from spammers.discord.gateway_url import gateway_ws_base
from spammers.discord.responses import DiscordJSONResponse

router = APIRouter()


@router.get("/api/v10/gateway")
async def gateway() -> DiscordJSONResponse:
    return DiscordJSONResponse({"url": gateway_ws_base()})


@router.get("/api/v10/gateway/bot")
async def gateway_bot(request: Request) -> DiscordJSONResponse:
    # Bot-scoped variant requires auth on real Discord.
    app = await resolve_application(request)
    if app is None:
        return DiscordJSONResponse({"message": "401: Unauthorized", "code": 0}, status_code=401)
    return DiscordJSONResponse({
        "url": gateway_ws_base(),
        "shards": 1,
        "session_start_limit": {
            "total": 1000,
            "remaining": 1000,
            "reset_after": 0,
            "max_concurrency": 1,
        },
    })
