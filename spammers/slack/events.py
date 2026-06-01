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


def _channel_type(is_im: bool, is_mpim: bool, is_private: bool) -> str:
    """Slack's event.channel_type: the ONLY field distinguishing a DM from a
    channel observation downstream."""
    if is_im:
        return "im"
    if is_mpim:
        return "mpim"
    if is_private:
        return "group"
    return "channel"


def _event_context() -> str:
    return "EC" + slack_event_id()[2:]


def _sign_headers(signing_secret, ts_header: str):
    def sign(b: bytes) -> Mapping[str, str]:
        return {
            "X-Slack-Signature": slack_sign(signing_secret, ts_header, b),
            "X-Slack-Request-Timestamp": ts_header,
        }
    return sign


def _envelope(row, event: dict, *, virtual_ts) -> dict:
    return {
        "token": "verification-token-mock",
        "team_id": row["team_id"],
        "context_team_id": row["team_id"],
        "context_enterprise_id": None,
        "api_app_id": row["app_id"],
        "event": event,
        "type": "event_callback",
        "event_id": slack_event_id(),
        "event_time": int(virtual_ts.timestamp()),
        "event_context": _event_context(),
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
               c.channel_id, c.id AS channel_pk,
               c.is_im, c.is_mpim, c.is_private
          FROM timeline.events te
          JOIN org.people p ON p.id = te.actor_id
          JOIN app_slack.workspaces w ON w.run_id = te.run_id
          JOIN app_slack.users u ON u.workspace_id = w.id AND u.person_id = p.id
          LEFT JOIN app_slack.channels c
                 ON c.workspace_id = w.id
                AND (c.channel_id = te.payload->>'channel'
                     OR c.name = btrim(te.payload->>'channel', '#'))
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
    client_msg_id = str(uuid4())
    channel_type = _channel_type(row["is_im"], row["is_mpim"], row["is_private"])

    event = {
        "type": "message",
        "channel": row["channel_id"],
        "user": row["user_id"],
        "text": text,
        "ts": ts_str,
        "event_ts": ts_str,
        "channel_type": channel_type,
        "client_msg_id": client_msg_id,
        "team": row["team_id"],
    }
    thread_ts = payload.get("thread_ts")
    if thread_ts:
        event["thread_ts"] = thread_ts
    envelope = _envelope(row, event, virtual_ts=row["virtual_ts"])
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts_header = str(int(time.time()))
    sign = _sign_headers(row["signing_secret"], ts_header)

    # Project the live message into app_slack.messages so subsequent pull APIs
    # (conversations.history/replies) return it too, matching real Slack.
    await pool.execute(
        """
        INSERT INTO app_slack.messages
            (id, channel_pk, user_pk, ts, thread_ts, text, client_msg_id, timeline_event_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (channel_pk, ts) DO NOTHING
        """,
        uuid4(), row["channel_pk"], row["user_pk"], ts_str, thread_ts, text,
        client_msg_id, event_id,
    )

    status, resp = await deliver(url=fyralis_events_url, body=body, sign=sign)
    await mark_emitted(pool, event_id, status=status, attempt_at=datetime.now(timezone.utc))
    log.info("slack_event_emitted", event_id=str(event_id), status=status, channel_type=channel_type)
    return status, resp


async def emit_message_changed(
    pool: asyncpg.Pool,
    *,
    channel_pk: UUID,
    ts: str,
    new_text: str,
    fyralis_events_url: str,
) -> tuple[int, str]:
    """Deliver a ``message_changed`` edit event (subtype, hidden, nested message).

    Mirrors real Slack: the envelope's top-level event ts is a fresh edit ts; the
    real (new) content lives in the nested ``message`` object with an ``edited``
    stamp, and the prior content is echoed in ``previous_message``.
    """
    row = await pool.fetchrow(
        """
        SELECT m.ts, m.text AS old_text, m.thread_ts, u.slack_user_id AS user_id,
               c.channel_id, c.is_im, c.is_mpim, c.is_private,
               w.team_id, w.app_id, w.signing_secret, w.bot_user_id
          FROM app_slack.messages m
          JOIN app_slack.channels c ON c.id = m.channel_pk
          JOIN app_slack.workspaces w ON w.id = c.workspace_id
          LEFT JOIN app_slack.users u ON u.id = m.user_pk
         WHERE m.channel_pk = $1 AND m.ts = $2
        """,
        channel_pk, ts,
    )
    if row is None:
        raise LookupError(f"message not found for edit: {channel_pk}:{ts}")
    edit_ts = slack_ts(datetime.now(timezone.utc))
    channel_type = _channel_type(row["is_im"], row["is_mpim"], row["is_private"])
    event = {
        "type": "message",
        "subtype": "message_changed",
        "hidden": True,
        "channel": row["channel_id"],
        "channel_type": channel_type,
        "ts": edit_ts,
        "event_ts": edit_ts,
        "message": {
            "type": "message",
            "user": row["user_id"],
            "text": new_text,
            "ts": row["ts"],
            "edited": {"user": row["user_id"], "ts": edit_ts},
        },
        "previous_message": {
            "type": "message",
            "user": row["user_id"],
            "text": row["old_text"],
            "ts": row["ts"],
        },
    }
    envelope = _envelope(row, event, virtual_ts=datetime.now(timezone.utc))
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts_header = str(int(time.time()))
    await pool.execute(
        "UPDATE app_slack.messages SET text = $3, edited = $4::jsonb WHERE channel_pk = $1 AND ts = $2",
        channel_pk, ts, new_text, json.dumps({"user": row["user_id"], "ts": edit_ts}),
    )
    return await deliver(url=fyralis_events_url, body=body, sign=_sign_headers(row["signing_secret"], ts_header))


async def emit_message_deleted(
    pool: asyncpg.Pool,
    *,
    channel_pk: UUID,
    ts: str,
    fyralis_events_url: str,
) -> tuple[int, str]:
    """Deliver a ``message_deleted`` event (subtype, hidden, deleted_ts, no text)."""
    row = await pool.fetchrow(
        """
        SELECT m.ts, m.text AS old_text, u.slack_user_id AS user_id,
               c.channel_id, c.is_im, c.is_mpim, c.is_private,
               w.team_id, w.app_id, w.signing_secret, w.bot_user_id
          FROM app_slack.messages m
          JOIN app_slack.channels c ON c.id = m.channel_pk
          JOIN app_slack.workspaces w ON w.id = c.workspace_id
          LEFT JOIN app_slack.users u ON u.id = m.user_pk
         WHERE m.channel_pk = $1 AND m.ts = $2
        """,
        channel_pk, ts,
    )
    if row is None:
        raise LookupError(f"message not found for delete: {channel_pk}:{ts}")
    event_ts = slack_ts(datetime.now(timezone.utc))
    channel_type = _channel_type(row["is_im"], row["is_mpim"], row["is_private"])
    event = {
        "type": "message",
        "subtype": "message_deleted",
        "hidden": True,
        "channel": row["channel_id"],
        "channel_type": channel_type,
        "deleted_ts": row["ts"],
        "ts": event_ts,
        "event_ts": event_ts,
        "previous_message": {
            "type": "message",
            "user": row["user_id"],
            "text": row["old_text"],
            "ts": row["ts"],
        },
    }
    envelope = _envelope(row, event, virtual_ts=datetime.now(timezone.utc))
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8")
    ts_header = str(int(time.time()))
    await pool.execute(
        "UPDATE app_slack.messages SET is_hidden = TRUE WHERE channel_pk = $1 AND ts = $2",
        channel_pk, ts,
    )
    return await deliver(url=fyralis_events_url, body=body, sign=_sign_headers(row["signing_secret"], ts_header))


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
