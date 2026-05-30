"""Per-principal rate limiting for the Jira mock.

Jira Cloud returns HTTP ``429`` with a ``Retry-After`` header on overload. We
limit authenticated traffic only; ``/rest/api/3/myself`` and health are cheap
but still counted. Burst 50, sustained 10/s — backfill paging won't trip it.
"""
from __future__ import annotations

import base64
import json
import math
from typing import Optional

from starlette.responses import Response

from spammers.common.errors import jira_error
from spammers.common.rate_limit import RateLimiter

_RL = RateLimiter()
_CAP, _REFILL = 50.0, 10.0


def _principal(request) -> Optional[str]:
    h = request.headers.get("authorization") or request.headers.get("Authorization")
    if not h or not h.lower().startswith("basic "):
        return None
    try:
        raw = base64.b64decode(h[6:].strip()).decode("utf-8", "replace")
        return raw.split(":", 1)[0]
    except Exception:
        return "jira"


async def guard(request) -> Optional[Response]:
    if not request.url.path.startswith("/rest/api/3"):
        return None
    principal = _principal(request)
    if principal is None:
        return None
    ok, retry, _ = await _RL.take(f"jira:{principal}", capacity=_CAP, refill_per_sec=_REFILL, cost=1.0)
    if ok:
        return None
    ra = max(1, math.ceil(retry))
    body = jira_error("The request is rate limited.")
    return Response(content=json.dumps(body), status_code=429, media_type="application/json",
                    headers={"Retry-After": str(ra)})
