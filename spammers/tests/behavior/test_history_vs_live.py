"""The README's central contract: historical vs live events.

  - Historical events are pull-only — never webhooked.
  - Live events are drained, signed, and POSTed to the consumer AND (per the
    README) "also get projected into app_slack.messages so subsequent pulls
    see them too".
"""
from __future__ import annotations

from datetime import timedelta

import httpx
import pytest
import respx

from spammers.common.signing import slack_sign
from spammers.director.orchestrator import EmissionLoop
from spammers.orggen.live import inject_slack_message
from spammers.tests.conftest import BOT_TOKEN, CH_RANDOM, SIGNING_SECRET, VIRTUAL_NOW

pytestmark = pytest.mark.asyncio(loop_scope="session")

WEBHOOK_URL = "https://consumer.test/webhooks/slack"


async def test_no_webhook_for_historical_events(client, pool, run_id):
    # A historical event (is_historical=TRUE) must never be drained for emission.
    await pool.execute(
        """
        INSERT INTO timeline.events (id, run_id, virtual_ts, type, actor_id, payload, is_historical)
        VALUES (gen_random_uuid(), $1, $2, 'slack.message',
                (SELECT id FROM org.people WHERE run_id=$1 AND handle='alice'),
                '{"channel":"general","text":"old news"}'::jsonb, TRUE)
        """,
        run_id, VIRTUAL_NOW - timedelta(days=1),
    )
    loop = EmissionLoop(pool, run_id, slack_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await loop._drain_once()
    assert route.call_count == 0


async def test_live_event_is_delivered_and_signed(client, pool, run_id):
    await inject_slack_message(
        pool, run_id, handle="alice", channel="random",
        text="live broadcast one", at_virtual=VIRTUAL_NOW - timedelta(seconds=30),
    )
    loop = EmissionLoop(pool, run_id, slack_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await loop._drain_once()

    assert route.call_count == 1
    req = route.calls[0].request
    body = req.content
    sig = req.headers["X-Slack-Signature"]
    ts = req.headers["X-Slack-Request-Timestamp"]
    # Signature must verify against the workspace signing secret over the exact bytes sent.
    assert sig == slack_sign(SIGNING_SECRET, ts, body)
    assert sig.startswith("v0=")


async def test_live_event_is_requeryable_via_history(client, pool, run_id):
    # README promise: after emission the live message is also queryable via pulls.
    text = "live broadcast two"
    await inject_slack_message(
        pool, run_id, handle="bob", channel="random",
        text=text, at_virtual=VIRTUAL_NOW - timedelta(seconds=20),
    )
    loop = EmissionLoop(pool, run_id, slack_events_url=WEBHOOK_URL)
    with respx.mock:
        respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await loop._drain_once()

    r = await client.get(
        "/api/conversations.history",
        params={"channel": CH_RANDOM, "limit": 15},
        headers={"Authorization": f"Bearer {BOT_TOKEN}"},
    )
    texts = [m["text"] for m in r.json()["messages"]]
    assert text in texts, "live message was emitted but is not queryable via conversations.history"
