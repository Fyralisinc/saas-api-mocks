"""Outbound webhook delivery retries non-2xx (with backoff) and dead-letters
after a bound — it does NOT drop, matching real senders (Slack Events API,
GitHub, Gmail Pub/Sub all retry non-2xx over a long window).

Regression for the gap found running Fyralis against the mock: a receiver that
returned 401 during a load spike had its events stamped delivered and dropped.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from spammers.common.webhook_emitter import DELIVERY_MAX_ATTEMPTS, mark_emitted

pytestmark = pytest.mark.asyncio(loop_scope="session")


async def _new_event(pool, run_id):
    actor = await pool.fetchval("SELECT id FROM org.people WHERE run_id = $1 LIMIT 1", run_id)
    eid = uuid4()
    await pool.execute(
        "INSERT INTO timeline.events (id, run_id, virtual_ts, type, actor_id, payload, cross_refs, is_historical) "
        "VALUES ($1, $2, $3, 'webhook.retry.test', $4, '{}'::jsonb, '{}'::jsonb, FALSE)",
        eid, run_id, datetime(2026, 1, 1, tzinfo=timezone.utc), actor,
    )
    return eid


async def _state(pool, eid):
    return await pool.fetchrow(
        "SELECT emitted_at, emit_attempts, emit_next_attempt_at FROM timeline.events WHERE id = $1", eid
    )


async def _cleanup(pool, eid):
    await pool.execute("DELETE FROM timeline.events WHERE id = $1", eid)


async def test_2xx_is_terminal(pool, run_id):
    eid = await _new_event(pool, run_id)
    await mark_emitted(pool, eid, status=202)
    r = await _state(pool, eid)
    assert r["emitted_at"] is not None
    assert r["emit_attempts"] == 0
    await _cleanup(pool, eid)


@pytest.mark.parametrize("code", [404, 410])
async def test_endpoint_gone_is_terminal_no_retry(pool, run_id, code):
    eid = await _new_event(pool, run_id)
    await mark_emitted(pool, eid, status=code)
    r = await _state(pool, eid)
    assert r["emitted_at"] is not None          # 404/410 → stop, like an unsubscribe
    assert r["emit_attempts"] == 0
    await _cleanup(pool, eid)


@pytest.mark.parametrize("code", [401, 403, 429, 500, 503, -1])  # -1 = transport failure
async def test_non_2xx_is_retried_not_dropped(pool, run_id, code):
    eid = await _new_event(pool, run_id)
    await mark_emitted(pool, eid, status=code)
    r = await _state(pool, eid)
    assert r["emitted_at"] is None              # NOT dropped — stays pending
    assert r["emit_attempts"] == 1
    assert r["emit_next_attempt_at"] is not None  # rescheduled with backoff
    await _cleanup(pool, eid)


async def test_dead_letter_after_max_attempts(pool, run_id):
    eid = await _new_event(pool, run_id)
    for i in range(DELIVERY_MAX_ATTEMPTS):
        await mark_emitted(pool, eid, status=503)
        r = await _state(pool, eid)
        if i < DELIVERY_MAX_ATTEMPTS - 1:
            assert r["emitted_at"] is None      # still retrying
    r = await _state(pool, eid)
    assert r["emit_attempts"] == DELIVERY_MAX_ATTEMPTS
    assert r["emitted_at"] is not None          # dead-lettered → stops retrying forever
    await _cleanup(pool, eid)


async def test_drain_gate_respects_next_attempt_at(pool, run_id):
    """The EmissionLoop's eligibility predicate skips an event until its retry is due."""
    eid = await _new_event(pool, run_id)
    await mark_emitted(pool, eid, status=503)   # schedules next attempt ~30s out
    gate = ("SELECT count(*) FROM timeline.events WHERE id = $1 AND emitted_at IS NULL "
            "AND (emit_next_attempt_at IS NULL OR emit_next_attempt_at <= now())")
    assert await pool.fetchval(gate, eid) == 0  # not yet due → not eligible
    await pool.execute(
        "UPDATE timeline.events SET emit_next_attempt_at = now() - interval '1 second' WHERE id = $1", eid
    )
    assert await pool.fetchval(gate, eid) == 1  # due → eligible for re-delivery
    await _cleanup(pool, eid)
