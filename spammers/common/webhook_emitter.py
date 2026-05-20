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
from datetime import datetime, timezone
from typing import Awaitable, Callable, Mapping, Optional
from uuid import UUID

import httpx
import structlog


log = structlog.get_logger("spammers.webhook_emitter")


SignFn = Callable[[bytes], Mapping[str, str]]
"""Signature builder. Receives the body bytes, returns headers to add."""


DEFAULT_SCHEDULE = (1.0, 5.0, 30.0)
DEFAULT_BUDGET_S = 90.0


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


async def mark_emitted(pool, event_id: UUID, *, status: int, attempt_at: Optional[datetime] = None) -> None:
    at = attempt_at or datetime.now(timezone.utc)
    await pool.execute(
        "UPDATE timeline.events SET emitted_at = $2 WHERE id = $1",
        event_id, at,
    )
