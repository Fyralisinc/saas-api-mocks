"""Rate-limit tier fidelity.

Slack publishes per-method tiers (requests/min): Tier 1 ~1, Tier 2 ~20,
Tier 3 ~50, Tier 4 ~100. chat.postMessage is special (~1 msg/sec/channel).
We assert the mock's configured sustained rate (refill_per_sec * 60) against
the documented tier for each method.
"""
from __future__ import annotations

import inspect

import pytest

from spammers.common.rate_limit import slack_tier_for


def _rpm(method: str) -> float:
    _cap, refill = slack_tier_for(method)
    return round(refill * 60, 3)


# Well-documented, stable tiers.
@pytest.mark.parametrize(
    "method,expected_rpm",
    [
        ("users.list", 20),          # Tier 2
        ("conversations.list", 20),  # Tier 2
        ("users.info", 100),         # Tier 4
        ("team.info", 50),           # Tier 3
    ],
)
def test_documented_tier_rpm(method, expected_rpm):
    assert _rpm(method) == expected_rpm


def test_chat_post_message_one_per_second():
    # Slack: chat.postMessage allows ~1 message per second per channel.
    _cap, refill = slack_tier_for("chat.postMessage")
    assert refill == 1.0


# May 2025: conversations.history/.replies moved to Tier 1 (1/min, max 15
# objects) for non-Marketplace apps. This is the policy the mock should match
# if it emulates a non-Marketplace app.
@pytest.mark.parametrize("method", ["conversations.history", "conversations.replies"])
def test_history_replies_tier1_non_marketplace(method):
    assert _rpm(method) == 1


def test_history_object_cap_is_15():
    # Non-Marketplace cap: default & max `limit` should be 15.
    from spammers.slack.routes.conversations import history

    param = inspect.signature(history).parameters["limit"]
    default = getattr(param.default, "default", param.default)
    assert default == 15
