"""Per-user quota limiting for the Gmail mock.

Real Gmail enforces ~250 quota units/sec/user; most reads cost ~5 units. On
exhaustion it returns ``429`` with a ``Retry-After`` header and a
``rateLimitExceeded`` reason. We rate-limit authenticated traffic only (so an
unauthenticated request still gets the route's ``401``).
"""
from __future__ import annotations

import json
import math
from typing import Optional

from starlette.responses import Response

from spammers.common.errors import gmail_error
from spammers.common.rate_limit import RateLimiter, gmail_quota
from spammers.gmail.auth import resolve_token

_RL = RateLimiter()
_MEDIA = "application/json; charset=UTF-8"


async def guard(request) -> Optional[Response]:
    path = request.url.path
    if not (path.startswith("/gmail/v1") or path.startswith("/admin/directory")):
        return None
    claims = resolve_token(request)
    if claims is None:
        return None  # let the route return 401
    key = "gmail:" + str(claims.get("sub") or "user")
    cost = 1.0 if path.startswith("/admin/directory") else 5.0
    cap, refill = gmail_quota()
    ok, retry, _ = await _RL.take(key, capacity=cap, refill_per_sec=refill, cost=cost)
    if ok:
        return None
    ra = max(1, math.ceil(retry))
    body = gmail_error(429, "User-rate limit exceeded. Retry after some time.",
                       reason="rateLimitExceeded")
    return Response(content=json.dumps(body), status_code=429, media_type=_MEDIA,
                    headers={"Retry-After": str(ra)})
