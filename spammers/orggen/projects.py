"""Project graph generator.

Each project owns a slug, a primary engineering team, a Slack channel,
a Discord channel, a GitHub repo, and an email thread anchor. Events
reference projects so the cross-app weaving is grounded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence
from uuid import UUID, uuid4

from spammers.orggen.personas import Person
from spammers.orggen.profiles import ProfileSpec
from spammers.orggen.seed import RunRandom


@dataclass
class Project:
    id: UUID
    slug: str
    title: str
    owner_handle: str
    team_name: str
    started_at: datetime
    ended_at: datetime | None = None
    repos: list[str] = field(default_factory=list)
    slack_channels: list[str] = field(default_factory=list)
    discord_channels: list[str] = field(default_factory=list)
    email_thread_anchors: list[str] = field(default_factory=list)


_PROJECT_TITLES = [
    ("billing-rate-limiter", "Billing API rate limiter"),
    ("ingest-hardening",     "Ingestion path hardening"),
    ("onboarding-revamp",    "Customer onboarding revamp"),
    ("auth-rewrite",         "Auth middleware rewrite"),
    ("dashboard-redesign",   "Dashboard redesign"),
    ("incident-tooling",     "Incident response tooling"),
    ("data-warehouse",       "Data warehouse migration"),
    ("notifications-v2",     "Notifications platform v2"),
    ("mobile-launch",        "Mobile app launch"),
    ("search-relevance",     "Search relevance tuning"),
    ("checkout-flow",        "Checkout flow rebuild"),
    ("compliance-soc2",      "SOC2 readiness"),
    ("api-gateway",          "API gateway consolidation"),
    ("recsys",               "Recommendations service"),
    ("growth-experiments",   "Growth experiments harness"),
    ("internal-cli",         "Internal CLI overhaul"),
    ("cost-control",         "Cloud cost controls"),
    ("perf-cleanup",         "Performance cleanup"),
    ("doc-revamp",           "Documentation revamp"),
    ("support-deflection",   "Support deflection bot"),
    ("partner-portal",       "Partner portal"),
    ("invoice-pdf",          "Invoice PDF generator"),
    ("export-tools",         "Data export tools"),
    ("audit-log",            "Audit log surface"),
    ("session-refactor",     "Session-management refactor"),
    ("billing-prorations",   "Billing prorations correctness"),
    ("webhook-retries",      "Webhook retry policy"),
    ("multi-region",         "Multi-region rollout"),
    ("schema-migrations",    "Schema-migration runner"),
    ("observability",        "Observability foundations"),
    ("queue-resilience",     "Queue resilience"),
    ("ratelimit-redis",      "Redis-backed rate limiting"),
    ("k8s-rollouts",         "Kubernetes rollout strategy"),
    ("feature-flags",        "Feature flag platform"),
    ("seo-improvements",     "SEO improvements"),
    ("landing-page-redesign", "Landing page redesign"),
    ("permissions-v2",       "Permissions model v2"),
    ("oauth-cleanup",        "OAuth provider cleanup"),
    ("billing-export",       "Billing export pipeline"),
    ("paid-trials",          "Paid trial conversion"),
]


def generate_projects(
    spec: ProfileSpec,
    rng: RunRandom,
    people: Sequence[Person],
    *,
    virtual_now: datetime,
) -> list[Project]:
    rng_p = rng.sub("projects")
    earliest = virtual_now - spec.duration

    # Engineering people only own projects
    eng_owners = [p for p in people if "Engineering" in p.team_name or p.team_name in ("Product",)]
    if not eng_owners:
        eng_owners = list(people)

    # Number of projects scales with repo count (1 project per ~1-2 repos)
    n_projects = max(spec.repos, min(len(_PROJECT_TITLES), spec.repos + spec.teams))
    titles_pool = _PROJECT_TITLES.copy()
    rng_p.shuffle(titles_pool)
    if n_projects > len(titles_pool):
        # duplicate with suffixes
        extra = n_projects - len(titles_pool)
        titles_pool += [(f"{s}-{i}", f"{t} (phase {i + 2})") for i, (s, t) in enumerate(titles_pool[:extra])]
    titles_pool = titles_pool[:n_projects]

    projects: list[Project] = []
    for slug, title in titles_pool:
        owner = rng_p.choice(eng_owners)
        # Project lifespan: 4-24 weeks
        span_days = rng_p.randint(28, 168)
        # start uniformly across the runtime, but ensure end ≤ virtual_now
        max_start_offset = int(spec.duration.total_seconds() / 86400) - span_days
        if max_start_offset <= 0:
            start = earliest
        else:
            start = earliest + timedelta(days=rng_p.randint(0, max_start_offset))
        end = start + timedelta(days=span_days)
        end_ended = end if end < virtual_now else None

        slack_channels = [
            f"#{slug}",
            f"#{slug}-discussion",
        ]
        discord_channels = [f"{slug}"]
        repos = [f"acme/{slug}"]
        email_anchors = [f"{slug}-weekly-digest"]

        projects.append(Project(
            id=uuid4(),
            slug=slug,
            title=title,
            owner_handle=owner.handle,
            team_name=owner.team_name,
            started_at=start.replace(microsecond=0),
            ended_at=end_ended.replace(microsecond=0) if end_ended else None,
            repos=repos,
            slack_channels=slack_channels,
            discord_channels=discord_channels,
            email_thread_anchors=email_anchors,
        ))

    return projects
