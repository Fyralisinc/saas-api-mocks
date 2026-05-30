"""Per-user rate limiting for the Drive mock.

Drive enforces per-user request limits; on exhaustion it returns ``403`` with a
``userRateLimitExceeded`` reason (and ``429`` for ``rateLimitExceeded``). We
return ``429`` + ``Retry-After`` — the consumer's shared Google client maps both
429 and 403-quota to its backoff path.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from starlette.responses import Response

from spammers.common.errors import google_error
from spammers.common.rate_limit import RateLimiter
from spammers.drive.auth import resolve_token

_RL = RateLimiter()
_MEDIA = "application/json; charset=UTF-8"
_CAP, _REFILL = 100.0, 20.0


async def guard(request) -> Optional[Response]:
    if not request.url.path.startswith("/drive/v3"):
        return None
    claims = resolve_token(request)
    if claims is None:
        return None
    key = "drive:" + str(claims.get("sub") or "user")
    ok, retry, _ = await _RL.take(key, capacity=_CAP, refill_per_sec=_REFILL, cost=1.0)
    if ok:
        return None
    ra = max(1, math.ceil(retry))
    body = google_error(429, "Rate Limit Exceeded", reason="rateLimitExceeded", domain="usageLimits")
    return Response(content=json.dumps(body), status_code=429, media_type=_MEDIA,
                    headers={"Retry-After": str(ra)})
