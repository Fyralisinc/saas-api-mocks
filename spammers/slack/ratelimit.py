"""Slack rate-limit middleware.

Per-method bucket; on exhaustion returns Slack's documented body:
``{"ok": false, "error": "ratelimited"}`` with ``Retry-After`` header.
"""
from __future__ import annotations

import math
from typing import Optional

from fastapi import Request

from spammers.common.errors import slack_error
from spammers.common.rate_limit import slack_tier_for
from spammers.slack.responses import SlackJSONResponse as JSONResponse
from spammers.slack.state import state


async def check(
    method: str, *, identity: str, app_distribution: str = "marketplace"
) -> Optional[JSONResponse]:
    """Return None if allowed, else a 429 JSONResponse with Retry-After."""
    cap, refill = slack_tier_for(method, app_distribution)
    st = state()
    ok, retry, _bucket = await st.rate_limiter.take(
        key=f"slack:{method}:{identity}",
        capacity=cap,
        refill_per_sec=refill,
        cost=1.0,
    )
    if ok:
        return None
    retry_after = max(1, math.ceil(retry))
    return JSONResponse(
        slack_error("ratelimited"),
        status_code=429,
        headers={"Retry-After": str(retry_after)},
    )
