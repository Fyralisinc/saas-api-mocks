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


async def check(request: Request, installation_id: int) -> Tuple[dict[str, str], Optional[GitHubJSONResponse]]:
    """Consume one unit. Returns ``(headers, error_response_or_None)``.

    Headers are attached to every response (success or 403). Records the
    installation on ``request.state`` so the ETag middleware can refund a 304.
    """
    request.state.gh_rl_installation = installation_id
    w = _window(installation_id)
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
