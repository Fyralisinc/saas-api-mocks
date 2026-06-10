"""Build the dossier the Studio renders after `Initialize`.

Combines:

  * Static corpus profile (mission, products, repos, people with behavioural
    patterns, monthly signal roll-up) — parsed once by `corpus_profile.load`
    and cached for the life of the process.
  * Run-local dynamic state (virtual_now, replay cursor, paused flag, what's
    actually landed in the provider tables so far).

Everything is grounded — no invention. The model layer of Fyralis is tested
against this dossier, so it must match what is actually replayed.
"""
from __future__ import annotations

import os
from uuid import UUID

import asyncpg

from spammers.studio import corpus_profile
from spammers.studio.companies import Company


_DEFAULT_CORPUS = os.environ.get(
    "ALPEN_CORPUS_PATH",
    os.path.join(os.getcwd(), "corpus", "build", "events.jsonl"),
)


async def build(pool: asyncpg.Pool, run_id: UUID, company: Company) -> dict:
    profile = corpus_profile.load_profile(_DEFAULT_CORPUS)

    run = await pool.fetchrow(
        "SELECT virtual_now, corpus_path, created_at FROM org.runs WHERE id=$1",
        run_id,
    )
    now = run["virtual_now"]
    now_ym = now.strftime("%Y-%m") if now else ""

    # ---- dynamic state derived from the run + corpus window ----------------
    months = profile["monthly"]["months"]
    first_ym = months[0]["ym"] if months else ""
    last_ym  = months[-1]["ym"] if months else ""
    months_elapsed = sum(1 for m in months if m["ym"] <= now_ym)
    months_total = len(months)
    pct_progress = int((months_elapsed / months_total) * 100) if months_total else 0

    signals_backfilled = sum(
        m["unique_total"] for m in months if m["ym"] <= now_ym
    )
    signals_corpus = sum(m["unique_total"] for m in months)

    # ---- "what's actually in the provider tables right now" ----------------
    db_signals = await _db_signals(pool, run_id)

    # ---- mark which months have been replayed yet --------------------------
    monthly_view = []
    for m in months:
        in_backfill = m["ym"] <= now_ym
        monthly_view.append({**m, "in_backfill": in_backfill,
                             "is_future": not in_backfill})

    return {
        "name": profile["company"]["display_name"],
        "real_name": profile["company"]["name"],
        "stage": profile["company"]["stage"],
        "tagline": company.tagline,
        "logo_id": company.key,
        "as_of": now.isoformat() if now else None,

        "state": {
            "started_virtual": f"{first_ym}-01" if first_ym else None,
            "now_virtual":     now.isoformat() if now else None,
            "end_virtual":     profile["monthly"]["last_ts"],
            "months_total":    months_total,
            "months_elapsed":  months_elapsed,
            "months_remaining": max(0, months_total - months_elapsed),
            "pct_progress":    pct_progress,
            "signals_corpus":  signals_corpus,
            "signals_backfilled": signals_backfilled,
            "signals_remaining":  max(0, signals_corpus - signals_backfilled),
            "db_signals_now":  db_signals,
            "db_signals_total": sum(db_signals.values()),
        },

        "overview": {
            "blurb":     profile["company"]["overview_blurb"],
            "mission":   profile["company"]["mission"],
            "founded":   profile["company"]["founded"],
            "homepage":  profile["company"]["homepage"],
            "blog":      profile["company"]["blog"],
            "github_org": profile["company"]["github_org"],
            "headcount": profile["company"]["headcount"],
            "cofounders": profile["company"]["cofounders"],
            "products":  profile["products"],
            "repos":     profile["repos"],
            "milestones": profile["milestones"],
            "teams":     profile["teams"],
        },

        "people":  profile["people"],
        "signal_notes": profile["signal_notes"],
        "fundraising": profile["fundraising"],
        "finance_totals": profile["finance_totals"],

        "monthly": {
            "overview_blurb": _monthly_overview_blurb(
                months_elapsed, months_total,
                signals_backfilled, signals_corpus,
                profile["monthly"]["totals_corpus"],
            ),
            "totals_corpus":      profile["monthly"]["totals_corpus"],
            "ingest_corpus":      profile["monthly"]["ingest_corpus"],
            "phase_legend":       profile["monthly"]["phase_legend"],
            "months":             monthly_view,
        },
    }


# ---------------------------------------------------------------------------

async def _db_signals(pool: asyncpg.Pool, run_id: UUID) -> dict[str, int]:
    """Per-provider row count visible across the mock APIs for this run.

    This is the "what would Fyralis see if it ingested right now" picture —
    sum of the user-visible row counts each provider's API would return.
    Most providers use a single primary table; a few add comments/changelogs.
    """
    out: dict[str, int] = {}

    # ---- slack: messages
    val = await pool.fetchval(
        """SELECT count(*) FROM app_slack.messages m
             JOIN app_slack.channels c ON c.id = m.channel_pk
             JOIN app_slack.workspaces w ON w.id = c.workspace_id
            WHERE w.run_id = $1""", run_id)
    out["slack"] = int(val or 0)

    # ---- discord: messages
    val = await pool.fetchval(
        """SELECT count(*) FROM app_discord.messages m
             JOIN app_discord.channels c ON c.id = m.channel_pk
             JOIN app_discord.guilds g ON g.id = c.guild_pk
             JOIN app_discord.applications a ON a.id = g.application_pk
            WHERE a.run_id = $1""", run_id)
    out["discord"] = int(val or 0)

    # ---- github: commits + PRs + issues + reviews + comments
    app = await pool.fetchrow(
        "SELECT id FROM app_github.apps WHERE run_id=$1", run_id)
    if app:
        inst = await pool.fetchrow(
            "SELECT id FROM app_github.installations WHERE app_pk=$1", app["id"])
        if inst:
            repo_ids = [r["id"] for r in await pool.fetch(
                "SELECT id FROM app_github.repositories WHERE installation_pk=$1",
                inst["id"])]
            if repo_ids:
                gh = 0
                # commits / pull_requests / issues / issue_comments hang off
                # a repo directly; reviews hang off a pull_request.
                for tbl in ("commits", "pull_requests", "issues", "issue_comments"):
                    gh += int(await pool.fetchval(
                        f"SELECT count(*) FROM app_github.{tbl} WHERE repo_pk = ANY($1)",
                        repo_ids) or 0)
                gh += int(await pool.fetchval(
                    """SELECT count(*) FROM app_github.reviews r
                         JOIN app_github.pull_requests pr ON pr.id = r.pr_pk
                        WHERE pr.repo_pk = ANY($1)""", repo_ids) or 0)
                out["github"] = gh
            else:
                out["github"] = 0
        else:
            out["github"] = 0
    else:
        out["github"] = 0

    # ---- gmail: messages
    val = await pool.fetchval(
        """SELECT count(*) FROM app_gmail.messages m
             JOIN app_gmail.threads t ON t.id = m.thread_pk
             JOIN app_gmail.mailboxes mb ON mb.id = t.mailbox_pk
             JOIN app_gmail.customers cu ON cu.id = mb.customer_pk
            WHERE cu.run_id = $1""", run_id)
    out["gmail"] = int(val or 0)

    # ---- calendar: events
    val = await pool.fetchval(
        """SELECT count(*) FROM app_calendar.events e
             JOIN app_calendar.calendars c ON c.id = e.calendar_pk
             JOIN app_calendar.accounts a ON a.id = c.account_pk
            WHERE a.run_id = $1""", run_id)
    out["calendar"] = int(val or 0)

    # ---- notion: pages + comments (comments hang off pages)
    val = await pool.fetchval(
        """SELECT (SELECT count(*) FROM app_notion.pages p
                     JOIN app_notion.integrations i ON i.id = p.integration_pk
                    WHERE i.run_id = $1)
                + (SELECT count(*) FROM app_notion.comments c
                     JOIN app_notion.pages p2 ON p2.id = c.page_pk
                     JOIN app_notion.integrations i2 ON i2.id = p2.integration_pk
                    WHERE i2.run_id = $1)""", run_id)
    out["notion"] = int(val or 0)

    # ---- drive: files + comments (comments hang off files)
    val = await pool.fetchval(
        """SELECT (SELECT count(*) FROM app_drive.files f
                     JOIN app_drive.installations i ON i.id = f.installation_pk
                    WHERE i.run_id = $1)
                + (SELECT count(*) FROM app_drive.comments c
                     JOIN app_drive.files f2 ON f2.id = c.file_pk
                     JOIN app_drive.installations i2 ON i2.id = f2.installation_pk
                    WHERE i2.run_id = $1)""", run_id)
    out["drive"] = int(val or 0)

    # ---- jira: issues + comments + changelogs (both hang off issues)
    val = await pool.fetchval(
        """SELECT (SELECT count(*) FROM app_jira.issues x
                     JOIN app_jira.projects p ON p.id = x.project_pk
                     JOIN app_jira.installations i ON i.id = p.installation_pk
                    WHERE i.run_id = $1)
                + (SELECT count(*) FROM app_jira.comments c
                     JOIN app_jira.issues x ON x.id = c.issue_pk
                     JOIN app_jira.projects p ON p.id = x.project_pk
                     JOIN app_jira.installations i ON i.id = p.installation_pk
                    WHERE i.run_id = $1)
                + (SELECT count(*) FROM app_jira.changelogs cl
                     JOIN app_jira.issues x ON x.id = cl.issue_pk
                     JOIN app_jira.projects p ON p.id = x.project_pk
                     JOIN app_jira.installations i ON i.id = p.installation_pk
                    WHERE i.run_id = $1)""", run_id)
    out["jira"] = int(val or 0)

    # ---- quickbooks: deposits + purchases (accounts/vendors/employees are
    # one-off seeding, so they don't dominate the live "what Fyralis sees right
    # now" picture — but we still include them so the tile's count is honest)
    val = await pool.fetchval(
        """SELECT (SELECT count(*) FROM app_quickbooks.companies WHERE run_id = $1)
                + (SELECT count(*) FROM app_quickbooks.accounts a
                     JOIN app_quickbooks.companies c ON c.id = a.company_pk
                    WHERE c.run_id = $1)
                + (SELECT count(*) FROM app_quickbooks.vendors v
                     JOIN app_quickbooks.companies c ON c.id = v.company_pk
                    WHERE c.run_id = $1)
                + (SELECT count(*) FROM app_quickbooks.employees e
                     JOIN app_quickbooks.companies c ON c.id = e.company_pk
                    WHERE c.run_id = $1)
                + (SELECT count(*) FROM app_quickbooks.deposits d
                     JOIN app_quickbooks.companies c ON c.id = d.company_pk
                    WHERE c.run_id = $1)
                + (SELECT count(*) FROM app_quickbooks.purchases p
                     JOIN app_quickbooks.companies c ON c.id = p.company_pk
                    WHERE c.run_id = $1)""", run_id)
    out["quickbooks"] = int(val or 0)

    # ---- grafana: annotations (the org-wide observability stream)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_grafana.annotations a
             JOIN app_grafana.instances i ON i.id = a.instance_pk
            WHERE i.run_id = $1""", run_id)
    out["grafana"] = int(val or 0)

    # ---- mercury: bank transactions (the cash-movement stream)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_mercury.transactions t
             JOIN app_mercury.accounts a ON a.id = t.account_pk
             JOIN app_mercury.organizations o ON o.id = a.org_pk
            WHERE o.run_id = $1""", run_id)
    out["mercury"] = int(val or 0)

    # ---- ashby: recruiting entities (candidate/application/job/interview/offer)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_ashby.entities e
             JOIN app_ashby.organizations o ON o.id = e.org_pk
            WHERE o.run_id = $1""", run_id)
    out["ashby"] = int(val or 0)

    # ---- brex: corporate-card + cash transactions (the spend/cash stream)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_brex.transactions t
             JOIN app_brex.accounts a ON a.id = t.account_pk
             JOIN app_brex.organizations o ON o.id = a.org_pk
            WHERE o.run_id = $1""", run_id)
    out["brex"] = int(val or 0)

    # ---- deel: contractor/payroll invoices (the global-payroll payment stream)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_deel.invoices i
             JOIN app_deel.organizations o ON o.id = i.org_pk
            WHERE o.run_id = $1""", run_id)
    out["deel"] = int(val or 0)

    # ---- hibob: the HR People directory (the HR system-of-record signal)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_hibob.employees e
             JOIN app_hibob.companies c ON c.id = e.company_pk
            WHERE c.run_id = $1""", run_id)
    out["hibob"] = int(val or 0)

    # ---- figma: the design event stream (file versions + comments merged)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_figma.versions v
             JOIN app_figma.files f ON f.id = v.file_pk
             JOIN app_figma.teams t ON t.id = f.team_pk
            WHERE t.run_id = $1""", run_id)
    out["figma"] = int(val or 0)

    # ---- miro: the whiteboard item stream (one observation per board item)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_miro.items i
             JOIN app_miro.boards b ON b.id = i.board_pk
             JOIN app_miro.orgs o ON o.id = b.org_pk
            WHERE o.run_id = $1""", run_id)
    out["miro"] = int(val or 0)

    # ---- ramp: the corporate-card spend stream (one observation per transaction)
    val = await pool.fetchval(
        """SELECT count(*) FROM app_ramp.transactions t
             JOIN app_ramp.organizations o ON o.id = t.org_pk
            WHERE o.run_id = $1""", run_id)
    out["ramp"] = int(val or 0)

    return out


def _monthly_overview_blurb(elapsed: int, total: int,
                            sig_back: int, sig_corpus: int,
                            corpus_totals: dict[str, int]) -> str:
    biggest = sorted(corpus_totals.items(), key=lambda kv: -kv[1])[:3]
    biggest_blurb = ", ".join(f"{n:,} {p}" for p, n in biggest)
    return (
        f"This dossier covers {total} virtual months "
        f"({elapsed} elapsed · {total - elapsed} ahead). "
        f"The full corpus is {sig_corpus:,} unique signals "
        f"({biggest_blurb} dominate). "
        f"{sig_back:,} signals have been backfilled into the mocks so far; the "
        f"remaining {max(0, sig_corpus - sig_back):,} will land month-by-month as "
        f"the virtual clock advances."
    )
