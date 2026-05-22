"""Shared per-route plumbing: auth + rate-limit, with header propagation."""
from __future__ import annotations

from typing import Optional, Tuple

from fastapi import Request

from spammers.common.errors import discord_error
from spammers.discord import ratelimit
from spammers.discord.auth import resolve_application
from spammers.discord.responses import DiscordJSONResponse

# Discord's "401: Unauthorized" body shape (code 0 = general error).
UNAUTHORIZED = discord_error(0, "401: Unauthorized")


async def authed(
    request: Request, route: str
) -> Tuple[Optional[dict], dict[str, str], Optional[DiscordJSONResponse]]:
    """Resolve the bot application + apply the route's rate-limit bucket.

    Returns ``(app, headers, error_response)``. When ``app`` is None or
    ``error_response`` is set, the caller should return ``error_response``.
    """
    app = await resolve_application(request)
    if app is None:
        return None, {}, DiscordJSONResponse(UNAUTHORIZED, status_code=401)
    headers, limited = await ratelimit.check(route, identity=app["application_id"])
    if limited is not None:
        return app, headers, limited
    return app, headers, None
