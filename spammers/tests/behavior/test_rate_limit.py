"""429 behavior. Real Slack: HTTP 429, body {ok:false,error:"ratelimited"},
plus an integer Retry-After header (seconds)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def test_burst_triggers_429(client, auth_header):
    # conversations.list is the cheapest read endpoint to burst (capacity ~10).
    statuses = []
    last_429 = None
    for _ in range(40):
        r = await client.get("/api/conversations.list", headers=auth_header)
        statuses.append(r.status_code)
        if r.status_code == 429:
            last_429 = r
            break
    assert 429 in statuses, "expected a 429 after bursting past the bucket"
    assert last_429.json() == {"ok": False, "error": "ratelimited"}
    retry_after = last_429.headers.get("Retry-After")
    assert retry_after is not None
    assert int(retry_after) >= 1
