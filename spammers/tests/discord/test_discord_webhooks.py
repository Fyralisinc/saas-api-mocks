"""Outbound interaction webhook fidelity — Ed25519 signature + headers.

The Director emits ``discord.interaction`` events as signed POSTs. A consumer
verifies ``X-Signature-Ed25519`` over ``timestamp + body`` with the app's public
key (exactly what real Discord requires). We capture the delivery and assert it
verifies — and that tampering fails.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from spammers.common.signing import discord_verify
from spammers.discord import interactions_out
from spammers.orggen.live import inject_discord_interaction
from spammers.tests.discord.conftest import APPLICATION_ID

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _db_public_key(dc_state) -> str:
    row = await dc_state.pool.fetchrow(
        "SELECT public_key FROM app_discord.applications WHERE run_id = $1",
        dc_state.run_id,
    )
    return row["public_key"]


class _Capture:
    def __init__(self):
        self.url = None
        self.body = None
        self.headers = None

    async def deliver(self, *, url, body, sign, extra_headers=None, **kw):
        self.url = url
        self.body = body
        self.headers = dict(sign(body))
        if extra_headers:
            self.headers.update(extra_headers)
        return 204, ""


async def test_interaction_signed_and_verifiable(dc_state, monkeypatch):
    cap = _Capture()
    monkeypatch.setattr(interactions_out, "deliver", cap.deliver)

    event_id = await inject_discord_interaction(
        dc_state.pool, dc_state.run_id, handle="alice", command="ping", interaction_type=2,
    )
    status, _ = await interactions_out.emit(
        dc_state.pool, run_id=dc_state.run_id, event_id=event_id,
        discord_interactions_url="http://consumer/webhooks/discord",
    )
    assert status == 204
    assert cap.url == "http://consumer/webhooks/discord"

    sig = cap.headers["X-Signature-Ed25519"]
    ts = cap.headers["X-Signature-Timestamp"]
    pub = await _db_public_key(dc_state)
    # Real Discord verification: signature over (timestamp + body).
    assert discord_verify(pub, sig, ts, cap.body) is True
    # Tampering with the body must fail verification.
    assert discord_verify(pub, sig, ts, cap.body + b"x") is False


async def test_interaction_payload_shape(dc_state, monkeypatch):
    import json
    cap = _Capture()
    monkeypatch.setattr(interactions_out, "deliver", cap.deliver)

    event_id = await inject_discord_interaction(
        dc_state.pool, dc_state.run_id, handle="bob", command="deploy", interaction_type=2,
    )
    await interactions_out.emit(
        dc_state.pool, run_id=dc_state.run_id, event_id=event_id,
        discord_interactions_url="http://consumer/webhooks/discord",
    )
    body = json.loads(cap.body)
    assert body["type"] == 2  # APPLICATION_COMMAND
    assert body["application_id"] == APPLICATION_ID
    assert body["data"]["name"] == "deploy"
    assert body["member"]["user"]["username"] == "bob"
    assert body["token"]


async def test_ping_interaction_minimal(dc_state, monkeypatch):
    import json
    cap = _Capture()
    monkeypatch.setattr(interactions_out, "deliver", cap.deliver)

    event_id = await inject_discord_interaction(
        dc_state.pool, dc_state.run_id, handle="alice", interaction_type=1,
    )
    await interactions_out.emit(
        dc_state.pool, run_id=dc_state.run_id, event_id=event_id,
        discord_interactions_url="http://consumer/webhooks/discord",
    )
    body = json.loads(cap.body)
    assert body["type"] == 1  # PING
    assert "data" not in body
