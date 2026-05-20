"""Outbound Slack Events API delivery.

When the orchestrator promotes a ``slack.message`` timeline event from
historical to live, this module signs the Events envelope and POSTs it to
Fyralis's ``/webhooks/slack`` (or whatever ``events_url`` the install
configured).

Envelope shape matches the real Slack Events API:
{
  "token": "...",                         # legacy verification token; ignored by Fyralis
  "team_id": "T...",
  "api_app_id": "A...",
  "event": {
    "type": "message",
    "channel": "C...",
    "user": "U...",
    "text": "...",
    "ts": "1626825612.000200",
    "event_ts": "1626825612.000200",
    "team": "T..."
  },
  "type": "event_callback",
  "event_id": "Ev...",
  "event_time": 1626825612,
  "authorizations": [{...}],
  "is_ext_shared_channel": false
}
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.ids import slack_event_id, slack_ts
from spammers.common.signing import slack_sign
from spammers.common.webhook_emitter import deliver, mark_emitted


log = structlog.get_logger("spammers.slack.events")


async def emit_message(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    event_id: UUID,
    fyralis_events_url: str,
) -> tuple[int, str]:
    """Fetch a ``slack.message`` event and POST it to Fyralis."""
    row = await pool.fetchrow(
        """
        SELECT te.virtual_ts, te.payload, te.actor_id,
               w.team_id, w.app_id, w.signing_secret, w.bot_user_id,
               u.slack_user_id AS user_id, u.id AS user_pk,
               c.channel_id, c.id AS channel_pk
          FROM timeline.events te
          JOIN org.people p ON p.id = te.actor_id
          JOIN app_slack.workspaces w ON w.run_id = te.run_id
          JOIN app_slack.users u ON u.workspace_id = w.id AND u.person_id = p.id
          LEFT JOIN app_slack.channels c
                 ON c.workspace_id = w.id
                AND c.name = btrim(te.payload->>'channel', '#')
         WHERE te.id = $1
        """,
        event_id,
    )
    if row is None:
        raise LookupError(f"slack timeline event not found: {event_id}")
    if row["channel_id"] is None:
        raise LookupError(f"channel not provisioned for slack event: {event_id}")

    payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])
    text = payload.get("text", "")
    ts_str = slack_ts(row["virtual_ts"])

    envelope = {
        "token": "verification-token-mock",
        "team_id": row["team_id"],
        "api_app_id": row["app_id"],
        "event": {
            "type": "message",
            "channel": row["channel_id"],
            "user": row["user_id"],
            "text": text,
            "ts": ts_str,
            "event_ts": ts_str,
            "team": row["team_id"],
        },
        "type": "event_callback",
        "event_id": slack_event_id(),
        "event_time": int(row["virtual_ts"].timestamp()),
        "authorizations": [
            {
                "enterprise_id": None,
                "team_id": row["team_id"],
                "user_id": row["bot_user_id"],
                "is_bot": True,
                "is_enterprise_install": False,
            },
        ],
        "is_ext_shared_channel": False,
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts_header = str(int(time.time()))
    signing_secret = row["signing_secret"]

    def sign(b: bytes) -> Mapping[str, str]:
        sig = slack_sign(signing_secret, ts_header, b)
        return {
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts_header,
        }

    # Project the live message into app_slack.messages so subsequent pull APIs
    # (conversations.history/replies) return it too, matching real Slack.
    thread_ts = payload.get("thread_ts")
    await pool.execute(
        """
        INSERT INTO app_slack.messages
            (id, channel_pk, user_pk, ts, thread_ts, text, timeline_event_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        ON CONFLICT (channel_pk, ts) DO NOTHING
        """,
        uuid4(), row["channel_pk"], row["user_pk"], ts_str, thread_ts, text, event_id,
    )

    status, text = await deliver(url=fyralis_events_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("slack_event_emitted", event_id=str(event_id), status=status)
    return status, text


async def emit_url_verification(
    *,
    signing_secret: str,
    fyralis_events_url: str,
) -> tuple[int, str]:
    """Slack fires this once when an Events URL is configured.

    Real Slack expects the responder to echo back the ``challenge`` string
    within 3s. The mock sends it; Fyralis's webhook handler responds.
    """
    challenge = "spammer-url-verification-challenge"
    envelope = {
        "token": "verification-token-mock",
        "challenge": challenge,
        "type": "url_verification",
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts_header = str(int(time.time()))

    def sign(b: bytes) -> Mapping[str, str]:
        sig = slack_sign(signing_secret, ts_header, b)
        return {
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts_header,
        }

    return await deliver(url=fyralis_events_url, body=body, sign=sign)


async def emit_app_uninstalled(
    pool: asyncpg.Pool,
    *,
    run_id: UUID,
    team_id: str,
    fyralis_events_url: str,
) -> tuple[int, str]:
    row = await pool.fetchrow(
        "SELECT signing_secret, app_id FROM app_slack.workspaces WHERE run_id = $1 AND team_id = $2",
        run_id, team_id,
    )
    if row is None:
        raise LookupError(f"workspace not found: {team_id}")
    envelope = {
        "token": "verification-token-mock",
        "team_id": team_id,
        "api_app_id": row["app_id"],
        "event": {"type": "app_uninstalled"},
        "type": "event_callback",
        "event_id": slack_event_id(),
        "event_time": int(time.time()),
    }
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts_header = str(int(time.time()))

    def sign(b: bytes) -> Mapping[str, str]:
        sig = slack_sign(row["signing_secret"], ts_header, b)
        return {"X-Slack-Signature": sig, "X-Slack-Request-Timestamp": ts_header}

    return await deliver(url=fyralis_events_url, body=body, sign=sign)
