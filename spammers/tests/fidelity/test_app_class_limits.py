"""Post-2025-05-29 non-Marketplace caps on conversations.history/replies:
1 request/minute and a 15-object page limit. Marketplace apps keep Tier 3.

Getting the app class wrong is itself a fidelity gap — the mock would green-light
pagination/timing real Slack rejects — so these are encoded as hard checks.
"""
from __future__ import annotations

import pytest

from spammers.common.rate_limit import slack_tier_for
from spammers.slack.routes.conversations import _history_limit
from spammers.tests.conftest import CH_GENERAL


def test_non_marketplace_history_is_one_per_minute():
    cap, refill = slack_tier_for("conversations.history", "non_marketplace")
    assert cap == 1.0
    assert refill == pytest.approx(1.0 / 60.0)


def test_marketplace_history_is_tier3():
    cap, refill = slack_tier_for("conversations.history", "marketplace")
    assert cap == 50.0
    assert refill == pytest.approx(50.0 / 60.0)


def test_non_marketplace_clamps_page_limit_to_15():
    ident = {"app_distribution": "non_marketplace"}
    limit, _ = _history_limit(ident, {"limit": "1000"})
    assert limit == 15
    # default when omitted is also 15 for non-Marketplace
    limit, _ = _history_limit(ident, {})
    assert limit == 15


def test_marketplace_allows_large_page_limit():
    ident = {"app_distribution": "marketplace"}
    limit, _ = _history_limit(ident, {"limit": "1000"})
    assert limit == 1000


@pytest.mark.asyncio(loop_scope="session")
async def test_non_marketplace_second_history_call_is_rate_limited(client, auth_header):
    # The fixture workspace is non_marketplace (1/min, capacity 1): the first
    # call succeeds, the immediate second is 429 ratelimited.
    first = await client.get(
        "/api/conversations.history", params={"channel": CH_GENERAL}, headers=auth_header
    )
    assert first.json()["ok"] is True
    second = await client.get(
        "/api/conversations.history", params={"channel": CH_GENERAL}, headers=auth_header
    )
    assert second.status_code == 429
    assert second.json() == {"ok": False, "error": "ratelimited"}
    assert int(second.headers["Retry-After"]) >= 1
