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
from spammers.discord import interactions_out as discord_interactions
from spammers.github import webhooks as github_webhooks
from spammers.gmail import webhooks as gmail_webhooks
from spammers.jira import webhooks as jira_webhooks
from spammers.quickbooks import webhooks as quickbooks_webhooks
from spammers.grafana import webhooks as grafana_webhooks
from spammers.mercury import webhooks as mercury_webhooks
from spammers.ashby import webhooks as ashby_webhooks
from spammers.brex import webhooks as brex_webhooks
from spammers.deel import webhooks as deel_webhooks
from spammers.notion import webhooks as notion_webhooks
from spammers.slack import events as slack_events


log = structlog.get_logger("spammers.orchestrator")


class EmissionLoop:
    def __init__(
        self,
        pool: asyncpg.Pool,
        run_id: UUID,
        *,
        slack_events_url: Optional[str] = None,
        github_events_url: Optional[str] = None,
        discord_interactions_url: Optional[str] = None,
        notion_webhook_url: Optional[str] = None,
        gmail_pubsub_url: Optional[str] = None,
        jira_webhook_url: Optional[str] = None,
        quickbooks_webhook_url: Optional[str] = None,
        grafana_webhook_url: Optional[str] = None,
        mercury_webhook_url: Optional[str] = None,
        ashby_webhook_url: Optional[str] = None,
        brex_webhook_url: Optional[str] = None,
        deel_webhook_url: Optional[str] = None,
        poll_interval_s: float = 0.5,
        batch_size: int = 20,
    ) -> None:
        self._pool = pool
        self._run_id = run_id
        self._slack_events_url = slack_events_url
        self._github_events_url = github_events_url
        self._discord_interactions_url = discord_interactions_url
        self._notion_webhook_url = notion_webhook_url
        self._gmail_pubsub_url = gmail_pubsub_url
        self._jira_webhook_url = jira_webhook_url
        self._quickbooks_webhook_url = quickbooks_webhook_url
        self._grafana_webhook_url = grafana_webhook_url
        self._mercury_webhook_url = mercury_webhook_url
        self._ashby_webhook_url = ashby_webhook_url
        self._brex_webhook_url = brex_webhook_url
        self._deel_webhook_url = deel_webhook_url
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
               AND type <> 'discord.message'
               -- a failed delivery is held off until its (real-time) retry is due
               AND (emit_next_attempt_at IS NULL OR emit_next_attempt_at <= now())
             ORDER BY virtual_ts ASC
             LIMIT $3
            """,
            self._run_id, clock.virtual_now, self._batch_size,
        )
        for row in rows:
            try:
                etype = row["type"]
                if etype == "slack.message" and self._slack_events_url:
                    await slack_events.emit_message(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        fyralis_events_url=self._slack_events_url,
                    )
                elif etype.startswith("github.") and self._github_events_url:
                    await github_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        github_events_url=self._github_events_url,
                    )
                elif etype == "discord.interaction" and self._discord_interactions_url:
                    await discord_interactions.emit(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        discord_interactions_url=self._discord_interactions_url,
                    )
                elif etype == "notion.page" and self._notion_webhook_url:
                    await notion_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        notion_webhook_url=self._notion_webhook_url,
                    )
                elif etype == "gmail.message" and self._gmail_pubsub_url:
                    await gmail_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        gmail_pubsub_url=self._gmail_pubsub_url,
                    )
                elif etype == "jira.issue" and self._jira_webhook_url:
                    await jira_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        jira_webhook_url=self._jira_webhook_url,
                    )
                elif etype == "quickbooks.change" and self._quickbooks_webhook_url:
                    await quickbooks_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        quickbooks_webhook_url=self._quickbooks_webhook_url,
                    )
                elif etype == "grafana.alert" and self._grafana_webhook_url:
                    await grafana_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        grafana_webhook_url=self._grafana_webhook_url,
                    )
                elif etype == "mercury.transaction" and self._mercury_webhook_url:
                    await mercury_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        mercury_webhook_url=self._mercury_webhook_url,
                    )
                elif etype == "ashby.object" and self._ashby_webhook_url:
                    await ashby_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        ashby_webhook_url=self._ashby_webhook_url,
                    )
                elif etype == "brex.transfer" and self._brex_webhook_url:
                    await brex_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        brex_webhook_url=self._brex_webhook_url,
                    )
                elif etype == "deel.event" and self._deel_webhook_url:
                    await deel_webhooks.emit_event(
                        self._pool,
                        run_id=self._run_id,
                        event_id=row["id"],
                        deel_webhook_url=self._deel_webhook_url,
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
