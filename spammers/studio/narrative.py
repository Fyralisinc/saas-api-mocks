"""Derive a faithful 'company dossier' from the seeded run data.

There is no hand-authored narrative in OrgGen, but the structured data is
a real goal-directed company: people in roles/teams, named projects with
lifecycle dates (= goals), and open issues/PRs (= obstacles / work in
flight). We project that into a description a modeller can check messages
against. Everything here is grounded in the DB — no invention.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from spammers.studio.companies import Company


async def build(pool: asyncpg.Pool, run_id: UUID, company: Company) -> dict:
    run = await pool.fetchrow(
        "SELECT virtual_now FROM org.runs WHERE id=$1", run_id)
    now = run["virtual_now"]

    teams = [dict(r) for r in await pool.fetch(
        """SELECT t.name, count(p.id) AS headcount
             FROM org.teams t LEFT JOIN org.people p ON p.team_id=t.id
            WHERE t.run_id=$1 GROUP BY t.name ORDER BY headcount DESC""", run_id)]
    headcount = await pool.fetchval("SELECT count(*) FROM org.people WHERE run_id=$1", run_id)

    proj_rows = await pool.fetch(
        """SELECT pr.title, pr.slug, pr.started_at, pr.ended_at, pr.repos,
                  pe.handle AS owner, pe.full_name AS owner_name
             FROM org.projects pr LEFT JOIN org.people pe ON pe.id=pr.owner_id
            WHERE pr.run_id=$1 ORDER BY pr.started_at""", run_id)
    shipped, in_progress, planned = [], [], []
    for r in proj_rows:
        repos = r["repos"] if isinstance(r["repos"], list) else []
        item = {"title": r["title"], "slug": r["slug"], "owner": r["owner"],
                "owner_name": r["owner_name"], "repo": repos[0] if repos else None}
        if r["ended_at"] and r["ended_at"] < now:
            item["status"] = "shipped"
            shipped.append(item)
        elif r["started_at"] and r["started_at"] > now:
            item["status"] = "planned"
            planned.append(item)
        else:
            item["status"] = "in_progress"
            in_progress.append(item)

    # GitHub progress + obstacles
    repo_ids = []
    app = await pool.fetchrow("SELECT id FROM app_github.apps WHERE run_id=$1", run_id)
    if app:
        inst = await pool.fetchrow("SELECT id FROM app_github.installations WHERE app_pk=$1", app["id"])
        if inst:
            repo_ids = [r["id"] for r in await pool.fetch(
                "SELECT id FROM app_github.repositories WHERE installation_pk=$1", inst["id"])]
    merged_prs = open_prs = closed_issues = open_issues = 0
    obstacles: list[str] = []
    if repo_ids:
        merged_prs = await pool.fetchval("SELECT count(*) FROM app_github.pull_requests WHERE repo_pk=ANY($1) AND merged=true", repo_ids)
        open_prs = await pool.fetchval("SELECT count(*) FROM app_github.pull_requests WHERE repo_pk=ANY($1) AND state='open'", repo_ids)
        closed_issues = await pool.fetchval("SELECT count(*) FROM app_github.issues WHERE repo_pk=ANY($1) AND state='closed'", repo_ids)
        open_issues = await pool.fetchval("SELECT count(*) FROM app_github.issues WHERE repo_pk=ANY($1) AND state='open'", repo_ids)
        obstacles = [r["title"] for r in await pool.fetch(
            "SELECT title FROM app_github.issues WHERE repo_pk=ANY($1) AND state='open' ORDER BY created_at DESC LIMIT 8", repo_ids)]

    building = [p["title"] for p in (in_progress + planned)] or [p["title"] for p in shipped]
    team_summary = ", ".join(f"{t['name']} ({t['headcount']})" for t in teams)
    summary = (
        f"{company.name} is a {company.stage.lower()} — about {headcount} people "
        f"across {team_summary or 'a small team'}. They're a SaaS company building "
        f"{_join(building)}. As of the snapshot ({now:%b %Y}), "
        f"{len(shipped)} initiative(s) have shipped, {len(in_progress)} are in flight, "
        f"and {open_issues} known issue(s) are open."
    )

    return {
        "name": company.name,
        "stage": company.stage,
        "tagline": company.tagline,
        "as_of": now.isoformat(),
        "headcount": headcount,
        "teams": teams,
        "summary": summary,
        "goal": f"Build and ship: {_join(building)}.",
        "projects": {"shipped": shipped, "in_progress": in_progress, "planned": planned},
        "achieved": {
            "shipped_projects": [p["title"] for p in shipped],
            "merged_prs": merged_prs,
            "closed_issues": closed_issues,
        },
        "remaining": {
            "in_progress_projects": [p["title"] for p in in_progress],
            "planned_projects": [p["title"] for p in planned],
            "open_prs": open_prs,
        },
        "obstacles": {
            "open_issues": open_issues,
            "open_prs": open_prs,
            "items": obstacles,
        },
    }


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return "their product"
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return ", ".join(items[:-1]) + f", and {items[-1]}"
