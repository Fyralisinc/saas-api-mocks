"""Per-user rate limiting for the Calendar mock.

Google Calendar enforces per-user request limits; on exhaustion it returns
``429`` (``rateLimitExceeded``) with a ``Retry-After`` header. We limit
authenticated traffic only; ``/token`` and health are exempt.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from starlette.responses import Response

from spammers.calendar.auth import resolve_token
from spammers.common.errors import google_error
from spammers.common.rate_limit import RateLimiter

_RL = RateLimiter()
_MEDIA = "application/json; charset=UTF-8"
# Generous bucket: 100 burst, 20/sec sustained — backfill paging won't trip it,
# tight loops will.
_CAP, _REFILL = 100.0, 20.0


async def guard(request) -> Optional[Response]:
    if not request.url.path.startswith("/calendar/v3"):
        return None
    claims = resolve_token(request)
    if claims is None:
        return None
    key = "calendar:" + str(claims.get("sub") or "user")
    ok, retry, _ = await _RL.take(key, capacity=_CAP, refill_per_sec=_REFILL, cost=1.0)
    if ok:
        return None
    ra = max(1, math.ceil(retry))
    body = google_error(429, "Rate Limit Exceeded", reason="rateLimitExceeded", domain="usageLimits")
    return Response(content=json.dumps(body), status_code=429, media_type=_MEDIA,
                    headers={"Retry-After": str(ra)})
