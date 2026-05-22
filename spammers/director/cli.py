"""Director CLI.

Subcommands:
  prepare   — ensure DB exists, apply migrations, create a run, run OrgGen
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
from spammers.director.installer import install_slack
from spammers.director.orchestrator import EmissionLoop
from spammers.director.runs import create_run, get_run, latest_run
from spammers.orggen.compile import compile_run
from spammers.orggen.live import LiveEventGenerator, inject_github_event, inject_slack_message


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

    rid = await create_run(
        pool,
        size=args.size,
        runtime=args.runtime,
        seed=args.seed,
        fyralis_tenant_id=tenant_id,
        fyralis_base_url=args.fyralis_base,
    )
    _eprint(f"run created: {rid}")

    summary = await compile_run(pool, rid)
    _eprint(f"orggen summary: {json.dumps(summary)}")
    print(str(rid))                  # only the run id on stdout
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

    # set live mode at requested speed
    await set_mode(pool, rid, mode="live", speed_multiplier=args.speed)
    _eprint(f"mode=live, speed_multiplier={args.speed}")

    # start the live clock ticker
    from spammers.common.clock import LiveClockTicker
    ticker = LiveClockTicker(pool, rid, tick_s=1.0)
    ticker.start()

    loop = EmissionLoop(pool, rid, slack_events_url=slack_events_url,
                        github_events_url=github_events_url)
    loop.start()
    _eprint(f"emitting → slack:{slack_events_url} github:{github_events_url} (Ctrl-C to stop)")

    live_gen: LiveEventGenerator | None = None
    if args.live_rate > 0:
        live_gen = LiveEventGenerator(pool, rid, msgs_per_minute=args.live_rate)
        live_gen.start()
        _eprint(f"live event generator running at {args.live_rate} msgs/min")

    try:
        stop = asyncio.Event()
        try:
            await stop.wait()
        except asyncio.CancelledError:
            pass
    finally:
        if live_gen is not None:
            await live_gen.stop()
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
        event_id = await inject_github_event(
            pool, rid, kind=args.kind, repo=args.repo, handle=args.handle, title=args.text,
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
    schemas = ["timeline", "app_slack", "app_discord", "app_github", "app_gmail", "oauth", "org"]
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

    p_prep = sub.add_parser("prepare", help="apply migrations, create a run, OrgGen")
    p_prep.add_argument("--size", choices=["small", "medium", "large"], required=True)
    p_prep.add_argument("--runtime", choices=["few_months", "one_year", "few_years"], required=True)
    p_prep.add_argument("--seed", type=int, default=42)
    p_prep.add_argument("--tenant-id", required=True)
    p_prep.add_argument("--fyralis-base", default="http://localhost:8000")
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
    p_emit.add_argument("--live-rate", type=float, default=0.0,
                        help="msgs/minute to generate live (default 0 = none)")
    p_emit.set_defaults(func=_cmd_emit)

    p_inj = sub.add_parser("inject", help="inject a one-off live event")
    p_inj.add_argument("--run-id", default=None)
    p_inj.add_argument("--provider", choices=["slack", "github"], default="slack")
    p_inj.add_argument("--handle", default=None, help="org.people.handle (default: random)")
    p_inj.add_argument("--channel", default="#general", help="slack channel")
    p_inj.add_argument("--kind", choices=["pull_request", "issues"], default="pull_request",
                       help="github event kind")
    p_inj.add_argument("--repo", default=None, help="github repo name (default: first)")
    p_inj.add_argument("--text", default=None, help="slack text / github title")
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
