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

# Mock-only: a queue of *forced* 429s per integration, armed via
# POST /_control/rate_limit, so a test can deterministically make the next N
# authed /v1 requests rate-limit (and exercise the consumer's Retry-After
# backoff) without having to race the token bucket. Real Notion has no such knob.
_FORCED: dict[str, int] = {}
_FORCED_RA: dict[str, int] = {}


def arm_rate_limit(integration_pk, count: int, retry_after: int = 1) -> None:
    key = "notion:" + str(integration_pk)
    _FORCED[key] = max(0, int(count))
    _FORCED_RA[key] = max(1, int(retry_after))


def _rate_limited(retry_after: int) -> Response:
    body = notion_error(429, "rate_limited", "You have been rate limited. Please try again later.")
    return Response(content=json.dumps(body), status_code=429, media_type=_MEDIA,
                    headers={"Retry-After": str(retry_after)})


async def guard(request) -> Optional[Response]:
    if not request.url.path.startswith("/v1/"):
        return None
    if request.url.path.startswith("/v1/oauth"):
        return None
    if not authed(request):
        return None  # let the route return 401
    key = "notion:" + str(state().integration_pk)
    forced = _FORCED.get(key, 0)
    if forced > 0:
        _FORCED[key] = forced - 1
        return _rate_limited(_FORCED_RA.get(key, 1))
    ok, retry, _ = await _RL.take(key, capacity=_CAP, refill_per_sec=_REFILL, cost=1.0)
    if ok:
        return None
    return _rate_limited(max(1, math.ceil(retry)))
