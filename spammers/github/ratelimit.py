"""GitHub rate limiting — fixed hourly window per installation.

Real GitHub: 5000 requests/hour per installation, reset reported (UTC epoch
seconds) in ``X-RateLimit-Reset`` on a fixed window boundary. On exhaustion
GitHub returns **403** with ``x-ratelimit-remaining: 0`` and the documented body.
A 304 (conditional request) does not count against the limit — see ``refund()``,
called by the app's ETag middleware.
"""
from __future__ import annotations

import time
from typing import Optional, Tuple

from fastapi import Request

from spammers.common.errors import github_error
from spammers.github.responses import GitHubJSONResponse
from spammers.github.state import state

LIMIT = 5000
WINDOW_S = 3600


def _window(installation_id: int) -> dict:
    now = time.time()
    windows = state().windows
    w = windows.get(installation_id)
    if w is None or now >= w["reset"]:
        w = {"reset": now + WINDOW_S, "used": 0}
        windows[installation_id] = w
    return w


def _headers(w: dict) -> dict[str, str]:
    remaining = max(0, LIMIT - w["used"])
    return {
        "X-RateLimit-Limit": str(LIMIT),
        "X-RateLimit-Remaining": str(remaining),
        "X-RateLimit-Reset": str(int(w["reset"])),
        "X-RateLimit-Used": str(min(w["used"], LIMIT)),
        "X-RateLimit-Resource": "core",
    }


_SECONDARY_DOCS = (
    "https://docs.github.com/rest/overview/rate-limits-for-the-rest-api"
    "#about-secondary-rate-limits"
)


def arm_secondary_limit(installation_id: int, *, count: int = 1, retry_after: int = 60) -> None:
    """Arm the next ``count`` requests for this installation to hit the secondary
    (abuse) rate limit — a 429 with ``Retry-After`` — exactly like GitHub does
    under bursty traffic. Lets a consumer's Retry-After backoff be exercised."""
    state().secondary[installation_id] = {"remaining": int(count), "retry_after": int(retry_after)}


def _take_secondary(installation_id: int) -> Optional[int]:
    s = state().secondary.get(installation_id)
    if not s or s["remaining"] <= 0:
        return None
    s["remaining"] -= 1
    if s["remaining"] <= 0:
        state().secondary.pop(installation_id, None)
    return s["retry_after"]


async def check(request: Request, installation_id: int) -> Tuple[dict[str, str], Optional[GitHubJSONResponse]]:
    """Consume one unit. Returns ``(headers, error_response_or_None)``.

    Headers are attached to every response (success or 403). Records the
    installation on ``request.state`` so the ETag middleware can refund a 304.
    """
    request.state.gh_rl_installation = installation_id
    w = _window(installation_id)

    # Secondary (abuse) limit: a 429 + Retry-After that does NOT exhaust the
    # primary quota — GitHub returns this under bursty traffic (common during
    # PR-review / check-run fan-out). Checked before consuming a primary unit.
    retry_after = _take_secondary(installation_id)
    if retry_after is not None:
        headers = {**_headers(w), "Retry-After": str(retry_after)}
        body = github_error(
            "You have exceeded a secondary rate limit. Please wait a few minutes "
            "before you try again.",
            documentation_url=_SECONDARY_DOCS,
        )
        return headers, GitHubJSONResponse(body, status_code=429, headers=headers)

    w["used"] += 1
    headers = _headers(w)
    if w["used"] > LIMIT:
        body = github_error(
            f"API rate limit exceeded for installation ID {installation_id}.",
            documentation_url="https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting",
        )
        return headers, GitHubJSONResponse(body, status_code=403, headers=headers)
    return headers, None


def refund(installation_id: int) -> None:
    """Give back one unit — used when a conditional request resolves to 304."""
    w = state().windows.get(installation_id)
    if w is not None and w["used"] > 0:
        w["used"] -= 1


def peek_headers(installation_id: int) -> dict[str, str]:
    """Current rate-limit headers without consuming a unit (for 304 responses)."""
    return _headers(_window(installation_id))
