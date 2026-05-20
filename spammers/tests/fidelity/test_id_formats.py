"""ID/timestamp format fidelity against Slack's documented shapes."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from spammers.common.ids import (
    slack_app_id,
    slack_bot_token,
    slack_channel_id,
    slack_event_id,
    slack_team_id,
    slack_ts,
    slack_user_id,
)


def test_team_id():
    assert re.fullmatch(r"T[A-Z0-9]{8,}", slack_team_id())


def test_user_id():
    assert re.fullmatch(r"U[A-Z0-9]{8,}", slack_user_id())


def test_channel_id():
    assert re.fullmatch(r"C[A-Z0-9]{8,}", slack_channel_id())


def test_app_id():
    assert re.fullmatch(r"A[A-Z0-9]{8,}", slack_app_id())


def test_bot_token():
    assert re.fullmatch(r"xoxb-\d{12}-\d{12}-[a-z0-9]{24}", slack_bot_token())


def test_event_id():
    assert re.fullmatch(r"Ev[A-Za-z0-9]{10}", slack_event_id())


def test_ts_format():
    ts = slack_ts(datetime(2026, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc))
    assert re.fullmatch(r"\d+\.\d{6}", ts)
    secs, micros = ts.split(".")
    assert micros == "123456"
