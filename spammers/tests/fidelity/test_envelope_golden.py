"""The outbound Events API envelope must match Slack's documented structure."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import respx

from spammers.director.orchestrator import EmissionLoop
from spammers.orggen.live import inject_slack_message
from spammers.slack.events import emit_url_verification
from spammers.tests.conftest import SIGNING_SECRET, VIRTUAL_NOW

pytestmark = pytest.mark.asyncio(loop_scope="session")

WEBHOOK_URL = "https://consumer.test/webhooks/slack"
GOLDEN = json.loads((Path(__file__).parent / "golden" / "message_event.json").read_text())


async def _capture_message_envelope(pool, run_id) -> dict:
    await inject_slack_message(
        pool, run_id, handle="alice", channel="random",
        text="golden", at_virtual=VIRTUAL_NOW - timedelta(seconds=40),
    )
    loop = EmissionLoop(pool, run_id, slack_events_url=WEBHOOK_URL)
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, json={"ok": True}))
        await loop._drain_once()
    return json.loads(route.calls[0].request.content)


async def test_message_envelope_structure(client, pool, run_id):
    env = await _capture_message_envelope(pool, run_id)
    assert set(env.keys()) == set(GOLDEN.keys())
    assert set(env["event"].keys()) == set(GOLDEN["event"].keys())
    assert env["type"] == "event_callback"
    assert env["event"]["type"] == "message"
    assert env["is_ext_shared_channel"] is False
    auth0 = env["authorizations"][0]
    assert auth0["is_bot"] is True
    assert set(auth0.keys()) == set(GOLDEN["authorizations"][0].keys())


async def test_url_verification_envelope(client, pool, run_id):
    with respx.mock:
        route = respx.post(WEBHOOK_URL).mock(return_value=httpx.Response(200, text="challenge"))
        await emit_url_verification(signing_secret=SIGNING_SECRET, fyralis_events_url=WEBHOOK_URL)
    env = json.loads(route.calls[0].request.content)
    assert env["type"] == "url_verification"
    assert "challenge" in env
