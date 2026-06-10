"""Director CLI.

Subcommands:
  prepare   — ensure DB exists, apply migrations, replay the Gharelu-Alpen corpus
  install   — walk Slack OAuth flow into Fyralis
  emit      — start the live-emission loop
  jump      — advance virtual time
  status    — show run + clock state
  reset     — drop and recreate the mock-orgs DB

Use ``--help`` on each subcommand.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog

from spammers.common.clock import advance, get_clock, jump_to, set_mode
from spammers.common.db import apply_migrations, create_pool, ensure_database_exists
from spammers.corpus.replay import backfill
from spammers.director.installer import install_slack
from spammers.director.orchestrator import EmissionLoop
from spammers.director.runs import create_run, get_run, latest_run
from spammers.orggen.live import (
    inject_calendar_event,
    inject_discord_interaction,
    inject_discord_message,
    inject_drive_file,
    inject_gmail_message,
    inject_github_event,
    inject_github_lifecycle,
    inject_jira_issue,
    inject_quickbooks_change,
    inject_grafana_alert,
    inject_aws_event,
    inject_mercury_transaction,
    inject_ashby_application_change,
    inject_brex_transfer,
    inject_deel_event,
    inject_hibob_event,
    inject_figma_event,
    inject_ramp_transaction,
    inject_gusto_event,
    inject_fireflies_transcript,
    inject_telegram_message,
    inject_signal_message,
    inject_notion_page,
    inject_notion_page_update,
    inject_slack_message,
)


log = structlog.get_logger("spammers.director.cli")


def _eprint(*a):
    print(*a, file=sys.stderr)


async def _cmd_prepare(args: argparse.Namespace) -> int:
    await ensure_database_exists()
    pool = await create_pool()
    applied = await apply_migrations(pool)
    _eprint(f"migrations applied: {applied}")

    if args.tenant_id is None:
        _eprint("error: --tenant-id is required")
        return 2
    tenant_id = UUID(args.tenant_id)

    corpus_path = os.path.abspath(args.corpus)
    if not os.path.exists(corpus_path):
        _eprint(f"error: corpus file not found: {corpus_path}")
        _eprint("  generate it with: (cd corpus && make corpus)")
        await pool.close()
        return 2
    as_of_str = args.as_of or datetime.now(timezone.utc).date().isoformat()
    as_of = datetime.fromisoformat(as_of_str).replace(tzinfo=timezone.utc)

    rid = await create_run(
        pool,
        fyralis_tenant_id=tenant_id, fyralis_base_url=args.fyralis_base,
        virtual_now=as_of, corpus_path=corpus_path,
    )
    _eprint(f"run created: {rid} (as-of {as_of_str})")
    counts = await backfill(pool, rid, corpus_path, until=as_of)
    # Grafana is a net-new Tier-C source with no corpus events; seed its realistic
    # annotation stream (derived from the org already replayed) into the run.
    from spammers.grafana.seed import seed_grafana
    g = await seed_grafana(pool, rid, at=as_of)
    _eprint(f"grafana seeded: {g}")
    # Mercury is a net-new Tier-C source: project the run's finance corpus into a
    # realistic bank account/transaction stream (after backfill populates QuickBooks).
    from spammers.mercury.seed import seed_mercury
    m = await seed_mercury(pool, rid, at=as_of)
    _eprint(f"mercury seeded: {m}")
    # Ashby is a net-new Tier-C source: project the run's org (teams -> departments,
    # people -> hiring team) into a realistic candidate/application/job/interview/offer
    # recruiting stream.
    from spammers.ashby.seed import seed_ashby
    a = await seed_ashby(pool, rid, at=as_of)
    _eprint(f"ashby seeded: {a}")
    # Brex is a net-new Tier-C source: project the run's vendor purchases onto a
    # corporate-card stream + funding deposits onto a cash account (REAL Brex API,
    # not the Fyralis Mercury-clone — divergence logged in brex-fidelity-audit).
    from spammers.brex.seed import seed_brex
    b = await seed_brex(pool, rid, at=as_of)
    _eprint(f"brex seeded: {b}")
    # Deel is a net-new Tier-C source: project the run's people into global-payroll
    # contracts + a monthly paid-invoice stream (REAL api.letsdeel.com/rest/v2 API,
    # not the Fyralis Mercury-clone — divergence logged in deel-fidelity-audit).
    from spammers.deel.seed import seed_deel
    d = await seed_deel(pool, rid, at=as_of)
    _eprint(f"deel seeded: {d}")
    # HiBob is a net-new Tier-C source: project the run's people into the HiBob
    # People directory + a salary/payroll stream + a recent time-off-change feed
    # (REAL api.hibob.com API, not the Fyralis Gusto/Brex-clone — divergence
    # logged in hibob-fidelity-audit).
    from spammers.hibob.seed import seed_hibob
    h = await seed_hibob(pool, rid, at=as_of)
    _eprint(f"hibob seeded: {h}")
    # Figma is a net-new Tier-C source: project the run's people into a Figma team
    # with files + version history + comments (REAL api.figma.com versions+comments
    # MERGE, not the Fyralis Brex-clone /events — divergence logged in
    # figma-fidelity-audit).
    from spammers.figma.seed import seed_figma
    fg = await seed_figma(pool, rid, at=as_of)
    _eprint(f"figma seeded: {fg}")
    # Miro is a net-new Tier-C source: project the run's people into a Miro org
    # with collaborative whiteboards + their items (REAL api.miro.com /v2 offset
    # boards + cursor items, not the Fyralis Brex-clone single-paginator — divergence
    # logged in miro-fidelity-audit; Miro is POLL-ONLY, webhooks discontinued 2025-12-05).
    from spammers.miro.seed import seed_miro
    mi = await seed_miro(pool, rid, at=as_of)
    _eprint(f"miro seeded: {mi}")
    # Ramp is a net-new Tier-C source: project the run's vendor purchases onto a
    # corporate-card spend stream + people onto users/cards + a reimbursement feed
    # (REAL api.ramp.com /developer/v1 keyset-cursor REST, not the Fyralis QBO-query
    # clone — divergence logged in ramp-fidelity-audit).
    from spammers.ramp.seed import seed_ramp
    rp = await seed_ramp(pool, rid, at=as_of)
    _eprint(f"ramp seeded: {rp}")
    # Gusto is a net-new Tier-C source: project the run's people onto a payroll +
    # HR directory (REAL api.gusto.com /v1 REST bare-array + X-* header pagination,
    # not the Fyralis QBO /v3 SQL-query clone — divergence logged in
    # gusto-fidelity-audit).
    from spammers.gusto.seed import seed_gusto
    gu = await seed_gusto(pool, rid, at=as_of)
    _eprint(f"gusto seeded: {gu}")
    # Carta is a net-new Tier-C source: project the run's people into a startup
    # cap table — stakeholders + share classes + option grants + SAFEs (REAL
    # api.carta.com /v1alpha1 issuer suite with Google-AIP token pagination +
    # protobuf-wrapper money/decimals, not the Fyralis QBO/Gusto SQL-query clone —
    # divergence logged in carta-fidelity-audit; Carta is POLL-ONLY, no webhook).
    from spammers.carta.seed import seed_carta
    ca = await seed_carta(pool, rid, at=as_of)
    _eprint(f"carta seeded: {ca}")
    # LinkedIn is a net-new Tier-C source: project the run's company into a LinkedIn
    # organization Page — published posts/shares + lifetime share & follower stats
    # (REAL api.linkedin.com /rest Community-Management Rest.li finders with OFFSET
    # paging + epoch-millis timestamps, not the Fyralis QBO/Carta SQL-query clone —
    # divergence logged in linkedin-fidelity-audit; LinkedIn is POLL-ONLY, no webhook).
    from spammers.linkedin.seed import seed_linkedin
    li = await seed_linkedin(pool, rid, at=as_of)
    _eprint(f"linkedin seeded: {li}")
    # Fireflies is a net-new Tier-C source: project the run's people into a stream of
    # meeting transcripts — Fireflies' AI-notetaker signal (REAL api.fireflies.ai
    # GraphQL transcripts/transcript/user queries, not the Fyralis fake Brex REST
    # clone — divergence logged in fireflies-fidelity-audit; POLL + webhook PUSH).
    from spammers.fireflies.seed import seed_fireflies
    ff = await seed_fireflies(pool, rid, at=as_of)
    _eprint(f"fireflies seeded: {ff}")
    # AWS is a net-new Tier-C source: synthesize a realistic Organization-CloudTrail
    # stream — control-plane management events (people -> IAM principals, repos ->
    # services) + CloudWatch alarm-state changes — read via the SigV4-signed
    # CloudTrail LookupEvents API (the REAL AWS wire, not a plain REST mock; POLL +
    # incremental-poll live, NO webhook — divergence note in aws-fidelity-audit).
    from spammers.aws.seed import seed_aws
    aw = await seed_aws(pool, rid, at=as_of)
    _eprint(f"aws seeded: {aw}")
    # Telegram is a net-new Tier-C source: project the run's people into ONE Telegram
    # account's dialogs (channels/supergroups/basic-groups/1:1 DMs) + message history —
    # consumed via the MTProto user API through Telethon (messages.getHistory backward
    # offset_id paging + a persistent updates connection; NO webhook/HMAC). The mock
    # reproduces the METHOD contract over a transport substitution (HTTP reads + a WS
    # gateway, the Discord-gateway analog) — divergence note in telegram-fidelity-audit.
    from spammers.telegram.seed import seed_telegram
    tg = await seed_telegram(pool, rid, at=as_of)
    _eprint(f"telegram seeded: {tg}")
    # Signal is a net-new Tier-C source (the LAST of the 25): project the run's people
    # into ONE Signal linked account's threads (groups + 1:1 direct) + message history
    # — consumed via a signal-cli linked device (forward-only receive; the Fyralis
    # get_history backward-walk CONTRACT is served over an HTTP+WS shim carrying REAL
    # signal-cli envelopes; NO webhook/HMAC). Signal is "cloned from Telegram"
    # (ADR-0003 Topology B) — divergence note in signal-fidelity-audit.
    from spammers.signal.seed import seed_signal
    sg = await seed_signal(pool, rid, at=as_of)
    _eprint(f"signal seeded: {sg}")
    _eprint(f"backfill summary: total={sum(counts.values())} kinds={len(counts)}")
    for k, v in sorted(counts.items(), key=lambda x: -x[1])[:8]:
        _eprint(f"  {v:>6d}  {k}")
    print(str(rid))
    await pool.close()
    return 0


async def _cmd_install(args: argparse.Namespace) -> int:
    pool = await create_pool()
    rid = UUID(args.run_id) if args.run_id else await latest_run(pool)
    if rid is None:
        _eprint("error: no run id provided and no recent run found")
        return 2
    run = await get_run(pool, rid)

    api_token = os.environ.get("FYRALIS_API_TOKEN") or args.fyralis_api_token
    if not api_token:
        _eprint("error: FYRALIS_API_TOKEN env or --fyralis-api-token required")
        return 2

    provider = args.provider
    if provider == "slack":
        result = await install_slack(
            fyralis_base=run["fyralis_base_url"],
            fyralis_api_token=api_token,
            slack_mock_base=args.slack_mock_base,
            tenant_id=run["fyralis_tenant_id"],
        )
    else:
        _eprint(f"provider not yet supported in this turn: {provider}")
        await pool.close()
        return 2

    _eprint(f"install result: {json.dumps(result)}")
    await pool.close()
    return 0


async def _cmd_emit(args: argparse.Namespace) -> int:
    pool = await create_pool()
    rid = UUID(args.run_id) if args.run_id else await latest_run(pool)
    if rid is None:
        _eprint("error: no run id provided and no recent run found")
        return 2
    run = await get_run(pool, rid)
    fyralis_base = run["fyralis_base_url"]
    slack_events_url = args.slack_events_url or f"{fyralis_base}/webhooks/slack"
    github_events_url = args.github_events_url or f"{fyralis_base}/webhooks/github"
    discord_interactions_url = args.discord_interactions_url or f"{fyralis_base}/webhooks/discord"
    notion_webhook_url = args.notion_webhook_url or f"{fyralis_base}/webhooks/notion"
    gmail_pubsub_url = args.gmail_pubsub_url or f"{fyralis_base}/webhooks/gmail/pubsub"
    jira_webhook_url = args.jira_webhook_url or f"{fyralis_base}/webhooks/jira"
    quickbooks_webhook_url = args.quickbooks_webhook_url or f"{fyralis_base}/webhooks/quickbooks"
    grafana_webhook_url = args.grafana_webhook_url or f"{fyralis_base}/webhooks/grafana"
    mercury_webhook_url = args.mercury_webhook_url or f"{fyralis_base}/webhooks/mercury"
    ashby_webhook_url = args.ashby_webhook_url or f"{fyralis_base}/webhooks/ashby"
    brex_webhook_url = args.brex_webhook_url or f"{fyralis_base}/webhooks/brex"
    deel_webhook_url = args.deel_webhook_url or f"{fyralis_base}/webhooks/deel"
    hibob_webhook_url = args.hibob_webhook_url or f"{fyralis_base}/webhooks/hibob"
    figma_webhook_url = args.figma_webhook_url or f"{fyralis_base}/webhooks/figma"
    ramp_webhook_url = args.ramp_webhook_url or f"{fyralis_base}/webhooks/ramp"
    gusto_webhook_url = args.gusto_webhook_url or f"{fyralis_base}/webhooks/gusto"
    fireflies_webhook_url = args.fireflies_webhook_url or f"{fyralis_base}/webhooks/fireflies"

    # set live mode at requested speed
    await set_mode(pool, rid, mode="live", speed_multiplier=args.speed)
    _eprint(f"mode=live, speed_multiplier={args.speed}")

    # start the live clock ticker
    from spammers.common.clock import LiveClockTicker
    ticker = LiveClockTicker(pool, rid, tick_s=1.0)
    ticker.start()

    loop = EmissionLoop(pool, rid, slack_events_url=slack_events_url,
                        github_events_url=github_events_url,
                        discord_interactions_url=discord_interactions_url,
                        notion_webhook_url=notion_webhook_url,
                        gmail_pubsub_url=gmail_pubsub_url,
                        jira_webhook_url=jira_webhook_url,
                        quickbooks_webhook_url=quickbooks_webhook_url,
                        grafana_webhook_url=grafana_webhook_url,
                        mercury_webhook_url=mercury_webhook_url,
                        ashby_webhook_url=ashby_webhook_url,
                        brex_webhook_url=brex_webhook_url,
                        deel_webhook_url=deel_webhook_url,
                        hibob_webhook_url=hibob_webhook_url,
                        figma_webhook_url=figma_webhook_url,
                        ramp_webhook_url=ramp_webhook_url,
                        gusto_webhook_url=gusto_webhook_url,
                        fireflies_webhook_url=fireflies_webhook_url)
    loop.start()
    _eprint(f"emitting → slack:{slack_events_url} github:{github_events_url} "
            f"discord:{discord_interactions_url} notion:{notion_webhook_url} "
            f"gmail:{gmail_pubsub_url} jira:{jira_webhook_url} (Ctrl-C to stop)")

    # If this is a corpus run, also start the forward-replay loop. It lands
    # corpus events into provider tables as virtual_now advances past their
    # `t`, and writes timeline.events rows that the EmissionLoop above
    # drains as signed webhooks.
    corpus_loop = None
    corpus_path = run.get("corpus_path")
    if corpus_path:
        from spammers.corpus.replay_loop import CorpusReplayLoop
        corpus_loop = CorpusReplayLoop(pool, rid, corpus_path)
        corpus_loop.start()
        _eprint(f"corpus replay loop active (source: {corpus_path})")

    try:
        stop = asyncio.Event()
        try:
            await stop.wait()
        except asyncio.CancelledError:
            pass
    finally:
        if corpus_loop is not None:
            await corpus_loop.stop()
        await loop.stop()
        await ticker.stop()
        await set_mode(pool, rid, mode="frozen")
        await pool.close()
    return 0


async def _cmd_inject(args: argparse.Namespace) -> int:
    pool = await create_pool()
    rid = UUID(args.run_id) if args.run_id else await latest_run(pool)
    if rid is None:
        _eprint("error: no run found")
        return 2
    if args.provider == "github":
        if args.kind in ("installation", "installation_repositories", "ping"):
            repos = args.repo.split(",") if (args.repo and args.kind == "installation_repositories") else None
            event_id = await inject_github_lifecycle(
                pool, rid, kind=args.kind, action=args.action, repos=repos, handle=args.handle,
            )
        else:
            event_id = await inject_github_event(
                pool, rid, kind=args.kind, action=args.action, number=args.number,
                repo=args.repo, handle=args.handle, title=args.text,
            )
    elif args.provider == "discord":
        if args.kind == "interaction":
            event_id = await inject_discord_interaction(
                pool, rid, handle=args.handle, channel=args.channel,
            )
        else:
            event_id = await inject_discord_message(
                pool, rid, handle=args.handle, channel=args.channel, text=args.text,
            )
    elif args.provider == "notion":
        # --action selects the live event: omitted/"create"/"page.created" makes a
        # NEW page; "update"/"content_updated"/"properties_updated" edits an
        # existing page (dedups, no new observation).
        act = (args.action or "create").replace("page.", "")
        if act in ("update", "content_updated", "properties_updated", "edit"):
            etype = "page.properties_updated" if act == "properties_updated" else "page.content_updated"
            event_id = await inject_notion_page_update(pool, rid, event_type=etype)
        else:
            event_id = await inject_notion_page(
                pool, rid, handle=args.handle, database=args.target, title=args.text,
            )
    elif args.provider == "gmail":
        event_id = await inject_gmail_message(
            pool, rid, handle=args.handle, recipient=args.target, text=args.text,
        )
    elif args.provider == "calendar":
        event_id = await inject_calendar_event(
            pool, rid, handle=args.handle, attendee=args.target, text=args.text,
        )
    elif args.provider == "drive":
        event_id = await inject_drive_file(
            pool, rid, handle=args.handle, title=args.text,
            trash=(args.kind == "trash"), hard=(args.kind == "trash"),
        )
    elif args.provider == "jira":
        event_id = await inject_jira_issue(
            pool, rid, handle=args.handle, project=args.target, summary=args.text,
        )
    elif args.provider == "quickbooks":
        event_id = await inject_quickbooks_change(
            pool, rid, entity_name=args.target or "Bill", memo=args.text,
        )
    elif args.provider == "grafana":
        event_id = await inject_grafana_alert(
            pool, rid, alertname=args.target or "HighErrorRate", summary=args.text,
        )
    elif args.provider == "mercury":
        event_id = await inject_mercury_transaction(
            pool, rid, counterparty=args.target or "Stripe Inc.",
        )
    elif args.provider == "ashby":
        event_id = await inject_ashby_application_change(
            pool, rid, action=args.target or "applicationSubmit",
        )
    elif args.provider == "brex":
        event_id = await inject_brex_transfer(
            pool, rid, counterparty=args.target or "Stripe Inc.",
        )
    elif args.provider == "deel":
        event_id = await inject_deel_event(
            pool, rid, event_type=args.target or "invoice.paid",
        )
    elif args.provider == "hibob":
        event_id = await inject_hibob_event(
            pool, rid, entity=args.target or "employee",
        )
    elif args.provider == "figma":
        event_id = await inject_figma_event(
            pool, rid, entity=args.target or "version",
        )
    elif args.provider == "ramp":
        event_id = await inject_ramp_transaction(
            pool, rid, merchant=args.target or "Amazon Web Services",
        )
    elif args.provider == "gusto":
        event_id = await inject_gusto_event(
            pool, rid, target=args.target or "payroll",
        )
    elif args.provider == "fireflies":
        event_id = await inject_fireflies_transcript(
            pool, rid, title=args.target or "Engineering Standup",
        )
    elif args.provider == "aws":
        event_id = await inject_aws_event(
            pool, rid, event_name=args.target or "RunInstances",
        )
    elif args.provider == "telegram":
        event_id = await inject_telegram_message(
            pool, rid, handle=args.handle, dialog_title=args.target, text=args.text,
            edit=(args.kind == "edit"),
        )
    elif args.provider == "signal":
        event_id = await inject_signal_message(
            pool, rid, handle=args.handle, thread_title=args.target, text=args.text,
            self_sent=(args.kind == "trash"),  # reuse --kind trash as the self-sent skip probe
        )
    else:
        event_id = await inject_slack_message(
            pool, rid,
            handle=args.handle, channel=args.channel, text=args.text,
        )
    _eprint(f"injected event {event_id}")
    print(str(event_id))
    await pool.close()
    return 0


async def _cmd_jump(args: argparse.Namespace) -> int:
    pool = await create_pool()
    rid = UUID(args.run_id) if args.run_id else await latest_run(pool)
    if rid is None:
        _eprint("error: no run found")
        return 2
    if args.to:
        when = datetime.fromisoformat(args.to)
        new = await jump_to(pool, rid, when)
        _eprint(f"jumped to {new.isoformat()}")
    elif args.by:
        delta = _parse_duration(args.by)
        new = await advance(pool, rid, delta)
        _eprint(f"advanced by {args.by} → {new.isoformat()}")
    else:
        _eprint("error: pass --to or --by")
        return 2
    await pool.close()
    return 0


async def _cmd_status(args: argparse.Namespace) -> int:
    pool = await create_pool()
    rid = UUID(args.run_id) if args.run_id else await latest_run(pool)
    if rid is None:
        _eprint("no runs found")
        await pool.close()
        return 0
    run = await get_run(pool, rid)
    clock = await get_clock(pool, rid)
    counts = await pool.fetchrow(
        """
        SELECT
          (SELECT count(*) FROM org.people    WHERE run_id = $1) AS people,
          (SELECT count(*) FROM org.teams     WHERE run_id = $1) AS teams,
          (SELECT count(*) FROM org.projects  WHERE run_id = $1) AS projects,
          (SELECT count(*) FROM timeline.events WHERE run_id = $1) AS events,
          (SELECT count(*) FROM timeline.events WHERE run_id = $1 AND is_historical = TRUE) AS historical,
          (SELECT count(*) FROM timeline.events WHERE run_id = $1 AND emitted_at IS NOT NULL) AS emitted
        """,
        rid,
    )
    body = {
        "run_id": str(rid),
        "size": run["size"],
        "runtime": run["runtime"],
        "fyralis_tenant_id": str(run["fyralis_tenant_id"]),
        "fyralis_base_url": run["fyralis_base_url"],
        "virtual_now": clock.virtual_now.isoformat(),
        "mode": clock.mode,
        "speed_multiplier": clock.speed_multiplier,
        "counts": dict(counts),
    }
    print(json.dumps(body, indent=2, default=str))
    await pool.close()
    return 0


async def _cmd_reset(args: argparse.Namespace) -> int:
    import asyncpg
    pool = await create_pool()
    if args.confirm != "yes":
        _eprint("pass --confirm yes to actually drop schemas")
        await pool.close()
        return 2
    schemas = ["timeline", "app_slack", "app_discord", "app_github", "app_gmail",
               "app_calendar", "app_notion", "app_drive", "app_jira", "app_quickbooks",
               "app_grafana", "app_mercury", "app_ashby", "app_brex", "app_deel",
               "app_hibob", "app_figma", "app_miro", "app_ramp", "app_gusto",
               "app_carta", "app_linkedin", "app_fireflies", "oauth", "org"]
    for s in schemas:
        await pool.execute(f"DROP SCHEMA IF EXISTS {s} CASCADE")
    _eprint(f"dropped schemas: {schemas}")
    await pool.close()
    return 0


def _parse_duration(s: str) -> timedelta:
    """Parse ``5m``, ``2h``, ``1d``, ``30s``, ``1w``."""
    units = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}
    if not s or s[-1] not in units:
        raise ValueError(f"bad duration: {s!r}")
    n = int(s[:-1])
    return timedelta(**{units[s[-1]]: n})


def main() -> None:
    parser = argparse.ArgumentParser(prog="spammers")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_prep = sub.add_parser("prepare", help="apply migrations, create a run, backfill the corpus")
    p_prep.add_argument("--tenant-id", required=True)
    p_prep.add_argument("--fyralis-base", default="http://localhost:8000")
    p_prep.add_argument("--corpus", default="./corpus/build/events.jsonl",
                        help="path to a corpus events.jsonl (default: ./corpus/build/events.jsonl)")
    p_prep.add_argument("--as-of", default=None,
                        help="YYYY-MM-DD cursor for corpus replay (default: today)")
    p_prep.set_defaults(func=_cmd_prepare)

    p_inst = sub.add_parser("install", help="auto-install a provider into Fyralis")
    p_inst.add_argument("--provider", choices=["slack"], required=True)
    p_inst.add_argument("--run-id", default=None)
    p_inst.add_argument("--slack-mock-base", default="http://localhost:7001")
    p_inst.add_argument("--fyralis-api-token", default=None,
                        help="defaults to $FYRALIS_API_TOKEN")
    p_inst.set_defaults(func=_cmd_install)

    p_emit = sub.add_parser("emit", help="start the live emission loop")
    p_emit.add_argument("--run-id", default=None)
    p_emit.add_argument("--speed", type=float, default=1.0)
    p_emit.add_argument("--slack-events-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/slack")
    p_emit.add_argument("--github-events-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/github")
    p_emit.add_argument("--discord-interactions-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/discord")
    p_emit.add_argument("--notion-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/notion")
    p_emit.add_argument("--gmail-pubsub-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/gmail/pubsub")
    p_emit.add_argument("--jira-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/jira")
    p_emit.add_argument("--quickbooks-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/quickbooks")
    p_emit.add_argument("--grafana-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/grafana")
    p_emit.add_argument("--mercury-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/mercury")
    p_emit.add_argument("--ashby-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/ashby")
    p_emit.add_argument("--brex-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/brex")
    p_emit.add_argument("--deel-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/deel")
    p_emit.add_argument("--hibob-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/hibob")
    p_emit.add_argument("--figma-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/figma")
    p_emit.add_argument("--ramp-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/ramp")
    p_emit.add_argument("--gusto-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/gusto")
    p_emit.add_argument("--fireflies-webhook-url", default=None,
                        help="defaults to {fyralis_base}/webhooks/fireflies")
    p_emit.set_defaults(func=_cmd_emit)

    p_inj = sub.add_parser("inject", help="inject a one-off live event")
    p_inj.add_argument("--run-id", default=None)
    p_inj.add_argument("--provider",
                       choices=["slack", "discord", "github", "notion", "gmail",
                                "calendar", "drive", "jira", "quickbooks", "grafana",
                                "mercury", "ashby", "brex", "deel", "hibob", "figma",
                                "ramp", "gusto", "fireflies", "aws", "telegram",
                                "signal"],
                       default="slack")
    p_inj.add_argument("--handle", default=None, help="org.people.handle (default: random)")
    p_inj.add_argument("--channel", default="#general", help="slack/discord channel")
    p_inj.add_argument("--kind",
                       choices=["pull_request", "issues", "push", "pull_request_review",
                                "issue_comment", "check_run", "installation",
                                "installation_repositories", "ping",
                                "message", "interaction", "file", "trash"],
                       default="pull_request",
                       help="github event/lifecycle kind · discord message/interaction · drive file/trash")
    p_inj.add_argument("--action", default=None,
                       help="github webhook action (opened/closed/merged/reopened/synchronize/"
                            "edited; installation suspend/unsuspend/deleted; repos added/removed)")
    p_inj.add_argument("--number", type=int, default=None,
                       help="github PR/issue number to target (reviews, comments, transitions)")
    p_inj.add_argument("--repo", default=None, help="github repo name (default: first)")
    p_inj.add_argument("--target", default=None,
                       help="notion database / gmail recipient handle / calendar attendee handle "
                            "/ figma version|comment / ramp merchant name / gusto payroll|employee")
    p_inj.add_argument("--text", default=None,
                       help="slack/discord text · github/notion title · gmail body · calendar summary")
    p_inj.set_defaults(func=_cmd_inject)

    p_jump = sub.add_parser("jump", help="advance virtual time")
    p_jump.add_argument("--run-id", default=None)
    p_jump.add_argument("--by", default=None, help="e.g. 5m, 2h, 1d")
    p_jump.add_argument("--to", default=None, help="ISO-8601 timestamp")
    p_jump.set_defaults(func=_cmd_jump)

    p_status = sub.add_parser("status", help="show run state")
    p_status.add_argument("--run-id", default=None)
    p_status.set_defaults(func=_cmd_status)

    p_reset = sub.add_parser("reset", help="drop schemas from mock-orgs DB")
    p_reset.add_argument("--confirm", default="no")
    p_reset.set_defaults(func=_cmd_reset)

    args = parser.parse_args()
    rc = asyncio.run(args.func(args))
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
