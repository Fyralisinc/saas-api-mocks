"""Webhook emission loop.

When mode == 'live', drains timeline.events where:
  - run_id matches
  - is_historical = FALSE
  - emitted_at IS NULL
  - virtual_ts <= virtual_now

For each event, dispatches to the right provider emitter (currently:
Slack). Records ``emitted_at`` on success or transient failure (the
emitter logs status).
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import asyncpg
import structlog

from spammers.common.clock import get_clock
from spammers.slack import events as slack_events


log = structlog.get_logger("spammers.orchestrator")


class EmissionLoop:
    def __init__(
        self,
        pool: asyncpg.Pool,
        run_id: UUID,
        *,
        slack_events_url: Optional[str] = None,
        poll_interval_s: float = 0.5,
        batch_size: int = 20,
    ) -> None:
        self._pool = pool
        self._run_id = run_id
        self._slack_events_url = slack_events_url
        self._poll_interval_s = poll_interval_s
        self._batch_size = batch_size
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def _drain_once(self) -> int:
        clock = await get_clock(self._pool, self._run_id)
        rows = await self._pool.fetch(
            """
            SELECT id, type
              FROM timeline.events
             WHERE run_id = $1
               AND is_historical = FALSE
               AND emitted_at IS NULL
               AND virtual_ts <= $2
             ORDER BY virtual_ts ASC
             LIMIT $3
            """,
            self._run_id, clock.virtual_now, self._batch_size,
        )
        for row in rows:
            try:
                if row["type"] == "slack.message" and self._slack_events_url:
                    await slack_events.emit_message(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        fyralis_events_url=self._slack_events_url,
                    )
                else:
                    # No emitter registered — mark as emitted to skip
                    await self._pool.execute(
                        "UPDATE timeline.events SET emitted_at = $2 WHERE id = $1",
                        row["id"], datetime.now(timezone.utc),
                    )
            except Exception as exc:
                log.warning("emit_failed", event_id=str(row["id"]), error=str(exc))
                # Don't mark emitted_at on failure → will retry next loop
        return len(rows)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                n = await self._drain_once()
                if n == 0:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_s)
                    except asyncio.TimeoutError:
                        pass
            except Exception as exc:
                log.warning("orchestrator_loop_error", error=str(exc))
                await asyncio.sleep(self._poll_interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
