"""Forward-replay loop — lands corpus events as ``virtual_now`` advances.

While ``backfill`` lands all corpus events up to ``--as-of`` in one pass and
marks them historical (never webhooked), this loop handles the *forward*
slice: events whose ``t`` falls past the cutoff. On every tick it:

  1. Reads the current ``virtual_now`` (driven by ``LiveClockTicker``) and
     the per-run ``replay_cursor`` (last position we landed up to).
  2. Streams corpus events in the half-open window ``(cursor, virtual_now]``.
  3. Dispatches each through the same handlers used by ``backfill`` — they
     land the row in the provider table (idempotent via ``idmap``).
  4. For events that map to a webhookable provider type, also inserts a
     sibling row in ``timeline.events`` with ``is_historical=FALSE``. The
     existing ``EmissionLoop`` drains those and POSTs the signed webhook.
  5. Advances ``replay_cursor`` to the last yielded timestamp.

Speed = wall-clock × ``runs.speed_multiplier``. At 24× the remaining ~11
months of Gharelu-Alpen corpus play out in ~14 days; at 1000× in ~8 hours.

The loop streams ``events.jsonl`` from disk each tick — ``iter_events``
skips ahead via the ``after=`` filter before yielding, so we don't re-read
the backfilled prefix every poll. For a quarter-million-line file the
prefix scan is still O(n); if that becomes a bottleneck, cache a file
offset between ticks.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

import asyncpg
import structlog

from spammers.common.clock import get_clock
from spammers.corpus import cursor as _cursor
from spammers.corpus.idmap import IdMap
from spammers.corpus.loader import iter_events
from spammers.corpus.replay import ReplayContext, _REGISTRY, _not_implemented
from spammers.corpus.schema import Event


log = structlog.get_logger("spammers.corpus.replay_loop")


def _timeline_type(provider: str, kind: str) -> Optional[str]:
    """Map a corpus (provider, kind) to the ``timeline.events.type`` the
    existing EmissionLoop dispatches on. Returns None for events that have
    no webhook emitter (handlers still land them in provider tables, but
    nothing is pushed out)."""
    if provider == "slack" and kind == "message":
        return "slack.message"
    if provider == "github" and kind in (
        "pr.open", "pr.merge", "pr.close",
        "issue.open", "issue.close",
        "review.submit", "release.publish",
    ):
        return f"github.{kind}"
    if provider == "notion" and kind in ("page.create", "page.update"):
        return "notion.page"
    if provider == "gmail" and kind == "message":
        return "gmail.message"
    if provider == "jira" and kind in (
        "issue.create", "issue.transition", "issue.assign",
    ):
        return "jira.issue"
    return None


async def _translate_payload(
    pool: asyncpg.Pool, run_id: UUID,
    provider: str, kind: str, raw: dict,
) -> Optional[dict]:
    """Adapt a corpus event payload to the shape each emitter expects.

    Returns None when the lookup-side prerequisites aren't satisfied (e.g.
    a jira issue was just inserted but isn't visible to this transaction
    yet) — the caller will skip the timeline insert and the cursor still
    advances. Provider-table state is already correct via the handler.
    """
    if provider == "slack" and kind == "message":
        # Corpus stores the channel as its corpus_id (``channel:random``);
        # the slack emitter strips a leading '#' and looks up by name.
        ch = str(raw.get("channel", "")).removeprefix("channel:")
        return {**raw, "channel": f"#{ch}"} if ch else None

    if provider == "jira":
        # Emitter expects ``payload.issue_id`` (integer-shaped Jira id);
        # corpus uses the issue ``key`` (e.g. ``STR-10507``). Look it up
        # from the row the handler just inserted/updated.
        key = raw.get("key")
        if not key:
            return None
        issue_id = await pool.fetchval(
            "SELECT i.issue_id FROM app_jira.issues i "
            "JOIN app_jira.installations inst ON inst.id = i.installation_pk "
            "WHERE inst.run_id = $1 AND i.issue_key = $2",
            run_id, key,
        )
        return {**raw, "issue_id": issue_id} if issue_id else None

    if provider == "notion" and kind in ("page.create", "page.update"):
        # Notion emitter looks up by ``page_id`` (the notion-id form
        # ``8-4-4-4-12``). Corpus carries the original corpus_id; pull the
        # actual notion page_id from app_notion.pages via the corpus_id_map.
        page_pk = await pool.fetchval(
            "SELECT db_pk FROM org.corpus_id_map "
            "WHERE run_id = $1 AND corpus_id = $2",
            run_id, raw.get("id"),
        )
        if page_pk is None:
            return None
        page_id = await pool.fetchval(
            "SELECT page_id FROM app_notion.pages WHERE id = $1", page_pk,
        )
        return {**raw, "page_id": page_id} if page_id else None

    # gmail/github: corpus payload already carries what the emitter reads
    # (gmail looks up by event_id alone; github wants repo/number which
    # the corpus has). Pass through unchanged.
    return raw


class CorpusReplayLoop:
    def __init__(
        self,
        pool: asyncpg.Pool,
        run_id: UUID,
        corpus_path: str | Path,
        *,
        poll_interval_s: float = 1.0,
    ) -> None:
        self._pool = pool
        self._run_id = run_id
        self._corpus_path = Path(corpus_path)
        self._poll_interval_s = poll_interval_s
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._ctx: Optional[ReplayContext] = None

    async def _ensure_ctx(self) -> ReplayContext:
        if self._ctx is None:
            idmap = IdMap(self._pool, self._run_id)
            await idmap.warm()
            self._ctx = ReplayContext(pool=self._pool, run_id=self._run_id, idmap=idmap)
        return self._ctx

    async def _resolve_actor(self, event: Event) -> Optional[UUID]:
        actor = event.get("actor")
        if not isinstance(actor, str) or not actor.startswith("person:"):
            return None
        ctx = await self._ensure_ctx()
        return await ctx.idmap.get(actor)

    async def _tick(self) -> tuple[int, int]:
        clock = await get_clock(self._pool, self._run_id)
        cursor = await _cursor.get(self._pool, self._run_id)
        if cursor is None or clock.virtual_now <= cursor:
            return 0, 0
        ctx = await self._ensure_ctx()
        landed = 0
        webhooked = 0
        last_ts: datetime | None = None
        for ts, event in iter_events(
            self._corpus_path, after=cursor, until=clock.virtual_now,
        ):
            handler = _REGISTRY.get(
                (event["provider"], event["kind"]), _not_implemented,
            )
            try:
                await handler(ctx, event)
                landed += 1
            except Exception as exc:
                log.warning(
                    "corpus_replay_handler_error",
                    kind=f"{event['provider']}.{event['kind']}",
                    error=str(exc)[:200],
                )
                last_ts = ts
                continue

            ttype = _timeline_type(event["provider"], event["kind"])
            if ttype is not None:
                actor_id = await self._resolve_actor(event)
                payload = await _translate_payload(
                    self._pool, self._run_id,
                    event["provider"], event["kind"], event["payload"],
                )
                if actor_id is not None and payload is not None:
                    try:
                        await self._pool.execute(
                            """INSERT INTO timeline.events
                               (id, run_id, virtual_ts, type, actor_id, payload, is_historical)
                               VALUES ($1, $2, $3, $4, $5, $6::jsonb, FALSE)""",
                            uuid4(), self._run_id, ts, ttype, actor_id,
                            json.dumps(payload),
                        )
                        webhooked += 1
                    except Exception as exc:
                        log.warning(
                            "corpus_replay_timeline_insert_failed",
                            kind=ttype, error=str(exc)[:200],
                        )
            last_ts = ts

        if last_ts is not None:
            await _cursor.advance(self._pool, self._run_id, last_ts)
        if landed:
            log.info(
                "corpus_replay_advanced",
                landed=landed, webhooked=webhooked,
                cursor=last_ts.isoformat() if last_ts else None,
            )
        return landed, webhooked

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                await self._tick()
            except Exception as exc:
                log.warning("corpus_replay_loop_error", error=str(exc))
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval_s)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
