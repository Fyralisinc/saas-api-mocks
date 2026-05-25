"""Rate limiting for the Notion mock.

Notion's documented limit is ~3 requests/sec per integration (with short
bursts). On exhaustion it returns ``429`` with an integer ``Retry-After`` header
and ``{"object":"error","status":429,"code":"rate_limited",…}``.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from starlette.responses import Response

from spammers.common.errors import notion_error
from spammers.common.rate_limit import RateLimiter
from spammers.notion.auth import authed
from spammers.notion.state import state

_RL = RateLimiter()
_MEDIA = "application/json; charset=utf-8"
# ~3 req/sec average, with a 15-request burst (matches Notion's behavior).
_CAP, _REFILL = 15.0, 3.0


async def guard(request) -> Optional[Response]:
    if not request.url.path.startswith("/v1/"):
        return None
    if request.url.path.startswith("/v1/oauth"):
        return None
    if not authed(request):
        return None  # let the route return 401
    key = "notion:" + str(state().integration_pk)
    ok, retry, _ = await _RL.take(key, capacity=_CAP, refill_per_sec=_REFILL, cost=1.0)
    if ok:
        return None
    ra = max(1, math.ceil(retry))
    body = notion_error(429, "rate_limited", "You have been rate limited. Please try again later.")
    return Response(content=json.dumps(body), status_code=429, media_type=_MEDIA,
                    headers={"Retry-After": str(ra)})
