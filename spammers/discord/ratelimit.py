"""Discord rate limiting — per-route token buckets with ``X-RateLimit-*`` headers.

Real Discord buckets requests per route (often per major-parameter, e.g. per
channel) and also enforces a global ~50/sec limit. Each response carries
``X-RateLimit-Limit/Remaining/Reset/Reset-After/Bucket`` and, when limited,
returns **429** with ``Retry-After`` and the body
``{"message":"You are being rate limited.","retry_after":<float>,"global":<bool>}``.

We model the per-route bucket with the shared token-bucket limiter and synthesize
the headers from the bucket state.
"""
from __future__ import annotations

import hashlib
import math
import time
from typing import Optional, Tuple

from spammers.common.rate_limit import discord_default_bucket
from spammers.discord.responses import DiscordJSONResponse
from spammers.discord.state import state


def _bucket_hash(route: str) -> str:
    """Opaque per-route bucket id, like Discord's ``X-RateLimit-Bucket``."""
    return hashlib.md5(route.encode("utf-8")).hexdigest()[:24]  # noqa: S324 (not security)


async def check(route: str, *, identity: str) -> Tuple[dict[str, str], Optional[DiscordJSONResponse]]:
    """Consume one unit from ``route``'s bucket for ``identity``.

    Returns ``(headers, error_response_or_None)``. ``headers`` are attached to
    every response (success or 429).
    """
    capacity, refill = discord_default_bucket()
    st = state()
    key = f"discord:{route}:{identity}"
    ok, retry, bucket = await st.rate_limiter.take(
        key=key, capacity=capacity, refill_per_sec=refill, cost=1.0,
    )
    reset_after = bucket.reset_at()
    headers = {
        "X-RateLimit-Limit": str(int(capacity)),
        "X-RateLimit-Remaining": str(max(0, math.floor(bucket.tokens))),
        "X-RateLimit-Reset": f"{time.time() + reset_after:.3f}",
        "X-RateLimit-Reset-After": f"{reset_after:.3f}",
        "X-RateLimit-Bucket": _bucket_hash(route),
    }
    if ok:
        return headers, None
    headers["X-RateLimit-Global"] = "false"
    headers["Retry-After"] = f"{retry:.3f}"
    body = {
        "message": "You are being rate limited.",
        "retry_after": round(retry, 3),
        "global": False,
    }
    return headers, DiscordJSONResponse(body, status_code=429, headers=headers)
