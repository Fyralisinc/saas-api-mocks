"""Rate-limit tier fidelity.

Slack publishes per-method tiers (requests/min): Tier 1 ~1, Tier 2 ~20,
Tier 3 ~50, Tier 4 ~100. chat.postMessage is special (~1 msg/sec/channel).
We assert the mock's configured sustained rate (refill_per_sec * 60) against
the documented tier for each method.
"""
from __future__ import annotations

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


# This mock emulates a Marketplace / internal Slack app, so conversations.history
# and conversations.replies are Tier 3 (~50/min) with the classic limit cap.
# (Slack's May-2025 change drops these to Tier 1 / 15-object pages for
# *non-Marketplace* apps — flip both here and in routes/conversations.py to
# emulate that instead.)
@pytest.mark.parametrize("method", ["conversations.history", "conversations.replies"])
def test_history_replies_tier3(method):
    assert _rpm(method) == 50


def test_history_object_cap():
    # Marketplace cap: default 100, max 1000. Arguments arrive via query OR
    # body, so the cap lives in the shared param reader, not the signature.
    from spammers.slack.params import int_param

    assert int_param({}, "limit", 100, lo=1, hi=1000) == 100          # default
    assert int_param({"limit": "5000"}, "limit", 100, lo=1, hi=1000) == 1000  # max
    assert int_param({"limit": "0"}, "limit", 100, lo=1, hi=1000) == 1        # min
