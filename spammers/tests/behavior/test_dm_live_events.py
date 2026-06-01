"""Live DM events carry channel_type, and edits/deletes emit the right subtype.

channel_type is the ONLY field that distinguishes a DM observation from a channel
observation downstream, so a live DM event without it is unfaithful.
"""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
import respx

from uuid import uuid4

from spammers.orggen.live import inject_slack_message
from spammers.slack import events as slack_events
from spammers.tests.conftest import CH_DM_AB, USER_ALICE, VIRTUAL_NOW

pytestmark = pytest.mark.asyncio(loop_scope="session")

WEBHOOK_URL = "https://consumer.test/webhooks/slack"


async def _channel_pk(pool, run_id, channel_id):
    return await pool.fetchval(
        """
        SELECT c.id FROM app_slack.channels c
          JOIN app_slack.workspaces w ON w.id = c.workspace_id
         WHERE w.run_id = $1 AND c.channel_id = $2
        """,
        run_id, channel_id,
    )


async def _scratch_message(pool, run_id, channel_id, ts, text):
    """Insert a throwaway message we can mutate without disturbing the seed."""
    cpk = await _channel_pk(pool, run_id, channel_id)
    upk = await pool.fetchval(
        "SELECT u.id FROM app_slack.users u JOIN app_slack.workspaces w ON w.id = u.workspace_id "
        "WHERE w.run_id = $1 AND u.slack_user_id = $2",
        run_id, USER_ALICE,
    )
    await pool.execute(
        "INSERT INTO app_slack.messages (id, channel_pk, user_pk, ts, text) "
        "VALUES ($1, $2, $3, $4, $5)",
        uuid4(), cpk, upk, ts, text,
    )
    return cpk


async def test_live_dm_event_has_channel_type_im(client, pool, run_id):
    event_id = await inject_slack_message(
        pool, run_id, handle="alice", channel="dm-alice-bob",
        text="live dm", at_virtual=VIRTUAL_NOW - timedelta(seconds=30),
    )
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await slack_events.emit_message(
            pool, run_id=run_id, event_id=event_id, fyralis_events_url=WEBHOOK_URL,
        )
    env = json.loads(route.calls[0].request.content)
    assert env["event"]["channel_type"] == "im"
    assert env["event"]["channel"].startswith("D")
    assert "client_msg_id" in env["event"]


async def test_message_changed_subtype(client, pool, run_id):
    ts = "1768000999.000001"
    cpk = await _scratch_message(pool, run_id, CH_DM_AB, ts, "before edit")
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await slack_events.emit_message_changed(
            pool, channel_pk=cpk, ts=ts, new_text="edited!",
            fyralis_events_url=WEBHOOK_URL,
        )
    ev = json.loads(route.calls[0].request.content)["event"]
    assert ev["subtype"] == "message_changed"
    assert ev["hidden"] is True
    assert ev["channel_type"] == "im"
    assert ev["message"]["text"] == "edited!"
    assert ev["message"]["edited"]["ts"]
    assert ev["previous_message"]["text"] == "before edit"


async def test_message_deleted_subtype(client, pool, run_id):
    ts = "1768000999.000002"
    cpk = await _scratch_message(pool, run_id, CH_DM_AB, ts, "delete me")
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await slack_events.emit_message_deleted(
            pool, channel_pk=cpk, ts=ts, fyralis_events_url=WEBHOOK_URL,
        )
    ev = json.loads(route.calls[0].request.content)["event"]
    assert ev["subtype"] == "message_deleted"
    assert ev["hidden"] is True
    assert ev["deleted_ts"] == ts
    assert "text" not in ev
