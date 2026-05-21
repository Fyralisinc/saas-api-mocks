"""GitHub rate limiting.

Primary REST limit: 5000 requests/hour per installation. On exhaustion GitHub
returns **403** (not 429) with the documented body, and every response carries
``X-RateLimit-*`` headers.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from spammers.common.errors import github_error
from spammers.github.responses import GitHubJSONResponse
from spammers.github.state import state

LIMIT = 5000
REFILL_PER_SEC = 5000.0 / 3600.0


async def check(installation_id: int) -> Tuple[dict[str, str], Optional[GitHubJSONResponse]]:
    """Consume one unit. Returns ``(headers, error_response_or_None)``.

    Headers are attached to every response (success or 403).
    """
    st = state()
    ok, _retry, bucket = await st.rate_limiter.take(
        key=f"github:{installation_id}",
        capacity=float(LIMIT),
        refill_per_sec=REFILL_PER_SEC,
        cost=1.0,
    )
    remaining = max(0, int(bucket.tokens))
    headers = {
        "X-RateLimit-Limit": str(LIMIT),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(time.time() + bucket.reset_at())),
        "X-RateLimit-Used": str(LIMIT - remaining),
        "X-RateLimit-Resource": "core",
    }
    if ok:
        return headers, None
    body = github_error(
        f"API rate limit exceeded for installation ID {installation_id}.",
        documentation_url="https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting",
    )
    return headers, GitHubJSONResponse(body, status_code=403, headers=headers)
