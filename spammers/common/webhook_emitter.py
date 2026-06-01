"""Signed outbound webhook delivery from the mocks to Fyralis.

Each provider passes its signature function in; this module owns the
retry/backoff schedule (matching what the real services do) and emission
bookkeeping into ``timeline.events.emitted_at``.

Real-service retry policies:
  Slack:   retries up to 3× on non-2xx, backoff ~30s/60s/300s
  Discord: gateway is push-only (no retry); interactions HTTPS reply uses
           replay to ack; this module is used for slack/github/gmail mostly
  GitHub:  retries up to ~8× over 1h on non-2xx (exponential)
  Gmail Pub/Sub: retries up to 7 days with exponential backoff (we cap at 1h)

For the spammer harness, retry is bounded to keep tests deterministic. Default
schedule: [1s, 5s, 30s] with a 90s wall budget.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Mapping, Optional
from uuid import UUID

import httpx
import structlog


log = structlog.get_logger("spammers.webhook_emitter")


SignFn = Callable[[bytes], Mapping[str, str]]
"""Signature builder. Receives the body bytes, returns headers to add."""


DEFAULT_SCHEDULE = (1.0, 5.0, 30.0)
DEFAULT_BUDGET_S = 90.0

# Cross-tick retry of a delivery that keeps failing (after the in-call budget
# above is exhausted). Real senders retry non-2xx over a long window instead of
# dropping; we mirror that with a bounded, backed-off reschedule and then
# dead-letter. Backoff is REAL wall-clock (a webhook retry is not virtual-time).
DELIVERY_MAX_ATTEMPTS = 5
RETRY_BACKOFF_S = (30.0, 60.0, 300.0, 600.0)


async def deliver(
    *,
    url: str,
    body: bytes,
    sign: SignFn,
    extra_headers: Optional[Mapping[str, str]] = None,
    schedule: tuple[float, ...] = DEFAULT_SCHEDULE,
    budget_s: float = DEFAULT_BUDGET_S,
    timeout_s: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> tuple[int, str]:
    """POST ``body`` to ``url`` with provider signature headers.

    Returns ``(status_code, response_text)``. Raises only on terminal
    transport failure after budget exhaustion.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=timeout_s)
    try:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if extra_headers:
            headers.update(extra_headers)
        sig_headers = sign(body)
        headers.update(sig_headers)

        loop = asyncio.get_event_loop()
        deadline = loop.time() + budget_s
        last_status = -1
        last_text = ""
        for attempt, sleep_s in enumerate([0.0, *schedule], start=1):
            if sleep_s > 0:
                if loop.time() + sleep_s >= deadline:
                    break
                await asyncio.sleep(sleep_s)
            try:
                resp = await client.post(url, content=body, headers=headers)
                last_status = resp.status_code
                last_text = resp.text or ""
                if 200 <= resp.status_code < 300:
                    return resp.status_code, last_text
                if resp.status_code in (410, 404):
                    log.warning(
                        "webhook_terminal_status",
                        status=resp.status_code, url=url,
                    )
                    return resp.status_code, last_text
            except httpx.TransportError as exc:
                last_text = f"transport_error:{type(exc).__name__}"
                log.warning("webhook_transport_error", error=type(exc).__name__, attempt=attempt)
        return last_status, last_text
    finally:
        if owns_client:
            await client.aclose()


def _is_terminal(status: int) -> bool:
    """A delivery outcome that should NOT be retried.

    2xx = accepted; 404/410 = the endpoint is gone (real senders stop / unsubscribe).
    Everything else (other 4xx like 401/403/429, all 5xx, and transport failures
    reported as status<=0) is retryable.
    """
    return 200 <= status < 300 or status in (404, 410)


async def mark_emitted(pool, event_id: UUID, *, status: int, attempt_at: Optional[datetime] = None) -> None:
    """Record a delivery outcome against ``timeline.events``.

    - **Terminal** (2xx delivered, or 404/410 gone): stamp ``emitted_at`` — done.
    - **Retryable** (other non-2xx, or transport failure): leave ``emitted_at``
      NULL and schedule ``emit_next_attempt_at`` with backoff so the EmissionLoop
      re-delivers later — matching real senders, which retry non-2xx over a long
      window rather than dropping. After ``DELIVERY_MAX_ATTEMPTS`` we dead-letter
      (stamp + log) so a permanently-broken endpoint can't retry forever.
    """
    at = attempt_at or datetime.now(timezone.utc)
    if _is_terminal(status):
        await pool.execute(
            "UPDATE timeline.events SET emitted_at = $2, emit_next_attempt_at = NULL WHERE id = $1",
            event_id, at,
        )
        return

    row = await pool.fetchrow(
        "UPDATE timeline.events SET emit_attempts = emit_attempts + 1 WHERE id = $1 RETURNING emit_attempts",
        event_id,
    )
    attempts = row["emit_attempts"] if row else DELIVERY_MAX_ATTEMPTS
    if attempts >= DELIVERY_MAX_ATTEMPTS:
        await pool.execute(
            "UPDATE timeline.events SET emitted_at = $2, emit_next_attempt_at = NULL WHERE id = $1",
            event_id, at,
        )
        log.warning("webhook_dead_letter", event_id=str(event_id), status=status, attempts=attempts)
        return

    backoff = RETRY_BACKOFF_S[min(attempts - 1, len(RETRY_BACKOFF_S) - 1)]
    await pool.execute(
        "UPDATE timeline.events SET emit_next_attempt_at = $2 WHERE id = $1",
        event_id, at + timedelta(seconds=backoff),
    )
    log.info("webhook_retry_scheduled", event_id=str(event_id), status=status,
             attempt=attempts, next_in_s=backoff)
