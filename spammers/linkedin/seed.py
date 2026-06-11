"""Realistic LinkedIn corpus seeding.

LinkedIn is a NET-NEW Tier-C source: the frozen run has no LinkedIn corpus, so we
model a realistic organization PAGE ourselves (the brief sanctions this, like
ramp/brex/carta projecting the run's org onto a stream). The company is the LinkedIn
**organization**; we project the run onto the three organization signal families the
Fyralis flow doc names (it calls them share / social_action / follower_stat), served
via the REAL Community Management surface:

  * **POSTS** — ~2 dozen published org posts/shares (company updates), the primary
    multi-page OFFSET stream → a genuine ``start``/``count`` walk.
  * **SHARE_STATS** — the lifetime ``totalShareStatistics`` aggregate (clicks/likes/
    comments/shares/impressions/uniques + engagement).
  * **FOLLOWER_STATS** — the lifetime facet breakdowns (by association type / seniority
    / function / staff-count range / geo / industry), derived from the headcount.

Timestamps are epoch-MILLIS integers. Everything is deterministic off the run seed.
Idempotent: a second call after the organization row exists is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from random import Random
from typing import Optional
from uuid import UUID, uuid4

import asyncpg

# Seed-stable organization identity (hand these to the ingest-client / memory).
LEGAL_NAME = "Alpen Labs Inc."
LOCALIZED_NAME = "Alpen Labs"
VANITY_NAME = "alpen-labs"
WEBSITE = "https://alpenlabs.com"
ORG_ID = 80411507                         # the numeric URN tail (urn:li:organization:…)
CLIENT_ID = "linkedin_id_86abf013c9d24e57"
CLIENT_SECRET = "linkedin_secret_Wk4Tq9rZ2mPxV7nB6yD1hF8jL3sC5gQ0aR"
ACCESS_TOKEN = "AQX-linkedin_at_3mWtR2sNcK9rB4yD6hF1jM5xQ0wT8uIoP7aLp83ZqV"

# Post commentary templates — realistic org-page company updates. Each is paired with
# an index to keep all posts textually distinct.
_POST_TOPICS = [
    "We're thrilled to announce our latest research milestone — {n} months of work "
    "from the whole team coming together. Proud of what we're building. #research",
    "Alpen Labs is hiring! We're growing the engineering team and looking for people "
    "who love hard problems. Reach out if that's you. #hiring #engineering",
    "Behind every release is a team that cares deeply about the craft. Grateful to "
    "work with people this thoughtful. Update #{n} is live.",
    "Excited to share that we crossed a major reliability target this quarter. "
    "Thank you to our customers for the trust. #reliability",
    "A quick look at how we think about systems design at Alpen Labs — link in the "
    "comments. Always learning. #systemsdesign #{n}",
    "Welcoming new teammates this month across research and platform. The bench keeps "
    "getting stronger. #team #welcome",
    "We shipped a big performance improvement — {n}x faster on the workloads our users "
    "care about most. Details soon. #performance",
    "Reflecting on the last sprint: shipped, learned, iterated. Onwards. #buildinpublic",
    "Our team presented at an industry meetup this week on observability at scale. "
    "Slides coming. #observability",
    "Customer spotlight: how one team cut their integration time dramatically with "
    "Alpen Labs. #customers #casestudy",
    "Open roles update — platform, infra, and applied research. We review every "
    "application. #careers #{n}",
    "Small teams, big leverage. Sharing a few principles that guide how we operate. "
    "#culture #engineering",
    "Milestone #{n}: another quarter of steady, compounding progress. Heads down, "
    "shipping. #startup",
    "We care about correctness as much as speed. A note on our testing philosophy. "
    "#quality #testing",
    "Thank you to the community for the feedback on our latest update — it directly "
    "shaped what we built next. #community",
    "Proud to support open standards and interoperability in everything we ship. "
    "#interoperability #{n}",
]

# Follower-stat facet skeletons (segment keys are real LinkedIn enum/URN shapes).
_SENIORITIES = [1, 2, 3, 4, 5, 6, 7, 8]
_FUNCTIONS = [8, 13, 22, 25, 26]          # Engineering / IT / Research / Sales / …
_GEOS = [103644278, 102713980, 101174742, 100446943, 102890719]  # US / IN / CA / DE / UK
_INDUSTRIES = [4, 6, 96, 109, 3128]
_STAFF_RANGES = ["SIZE_1", "SIZE_2_TO_10", "SIZE_11_TO_50", "SIZE_51_TO_200",
                 "SIZE_201_TO_500", "SIZE_501_TO_1000", "SIZE_1001_TO_5000"]


def _epoch_ms(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _facet(segments: list[tuple[str, int]], key: str, paid: int = 0) -> list[dict]:
    """Build a follower-facet array of ``{<key>: seg, followerCounts:{organic,paid}}``."""
    return [
        {"followerCounts": {"organicFollowerCount": organic, "paidFollowerCount": paid},
         key: seg}
        for seg, organic in segments
    ]


async def seed_linkedin(
    pool: asyncpg.Pool,
    run_id: UUID,
    *,
    at: Optional[datetime] = None,
) -> dict[str, int]:
    """Provision the organization + posts + share/follower stats.

    Idempotent. Returns ``{"posts":P, "shareStats":1, "followerStats":1}``."""
    existing = await pool.fetchval(
        "SELECT id FROM app_linkedin.organizations WHERE run_id = $1", run_id)
    if existing is not None:
        return {"posts": 0, "shareStats": 0, "followerStats": 0}

    seed_row = await pool.fetchrow(
        "SELECT seed, virtual_now FROM org.runs WHERE id = $1", run_id)
    if seed_row is None:
        raise LookupError(f"no run {run_id}")
    rng = Random(int(seed_row["seed"]) ^ 0x6C_69_6E_6B)  # 'link'
    now = at or seed_row["virtual_now"] or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    incorporated = now - timedelta(days=1100)

    org_pk = uuid4()
    org_urn = f"urn:li:organization:{ORG_ID}"
    await pool.execute(
        """INSERT INTO app_linkedin.organizations
            (id, run_id, base_url, org_id, org_urn, localized_name, vanity_name,
             website, access_token, client_id, client_secret, created_at)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)""",
        org_pk, run_id, "https://api.linkedin.com", ORG_ID, org_urn, LOCALIZED_NAME,
        VANITY_NAME, WEBSITE, ACCESS_TOKEN, CLIENT_ID, CLIENT_SECRET, incorporated)

    # ---- POSTS: company updates from the org Page's first post onward --------
    # Fixed adoption date: the org Page starts posting around the seed-round
    # announcement / public launch. Anchoring here (not a rolling `now - 430d`
    # window) accumulates posts forward as virtual-now advances and keeps cadence
    # (~1 post / 16 days) stable regardless of how far out `at` is.
    _ADOPTED_LI = datetime(2024, 4, 1, tzinfo=timezone.utc)
    first_post_at = _ADOPTED_LI if _ADOPTED_LI < now else now - timedelta(days=14)
    span_days = max(14, (now - first_post_at).days)
    n_posts = max(6, round(span_days / 16.5))
    # A large, LinkedIn-shaped 19-digit share-id base; bump per post deterministically.
    post_id_base = 7_120_000_000_000_000_000
    total_likes = total_comments = total_shares = total_clicks = total_impr = 0
    for i in range(n_posts):
        topic = _POST_TOPICS[i % len(_POST_TOPICS)]
        commentary = topic.format(n=(i + 2))
        # spread roughly evenly across the span, oldest first
        created = first_post_at + timedelta(
            days=int(span_days * i / n_posts), hours=rng.randint(8, 18),
            minutes=rng.randint(0, 59))
        if created > now:
            created = now - timedelta(hours=1)
        edited = rng.random() < 0.15
        last_mod = created + (timedelta(hours=rng.randint(1, 72)) if edited
                              else timedelta(0))
        if last_mod > now:
            last_mod = now
        urn_type = "ugcPost" if i % 5 == 0 else "share"
        post_id = post_id_base + i * 4096 + rng.randint(0, 4095)
        c_ms, p_ms, m_ms = _epoch_ms(created), _epoch_ms(created), _epoch_ms(last_mod)
        await pool.execute(
            """INSERT INTO app_linkedin.posts
                (id, org_pk, post_id, urn_type, commentary, lifecycle_state, visibility,
                 feed_distribution, is_edited, is_reshare_disabled, created_at_ms,
                 last_modified_ms, published_at_ms, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,'PUBLISHED','PUBLIC','MAIN_FEED',$6,FALSE,
                       $7,$8,$9,$10,$11)""",
            uuid4(), org_pk, post_id, urn_type, commentary, edited,
            c_ms, m_ms, p_ms, i, created)
        # accumulate plausible engagement for the lifetime aggregate
        impr = rng.randint(800, 9000)
        total_impr += impr
        total_clicks += rng.randint(20, 400)
        total_likes += rng.randint(3, 90)
        total_comments += rng.randint(0, 25)
        total_shares += rng.randint(0, 12)

    # ---- SHARE STATS: the lifetime totalShareStatistics aggregate ------------
    unique_impr = int(total_impr * 0.62)
    engagement = ((total_clicks + total_likes + total_comments + total_shares)
                  / total_impr) if total_impr else 0.0
    await pool.execute(
        """INSERT INTO app_linkedin.share_stats
            (org_pk, click_count, comment_count, engagement, impression_count,
             like_count, share_count, unique_impressions_count)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        org_pk, total_clicks, total_comments, engagement, total_impr,
        total_likes, total_shares, unique_impr)

    # ---- FOLLOWER STATS: lifetime facet breakdowns (derived from headcount) ---
    employees = await pool.fetchval(
        "SELECT count(*) FROM org.people WHERE run_id = $1", run_id) or 0
    employees = int(employees)
    base_followers = employees * 34 + rng.randint(200, 600)

    def split(total: int, weights: list[int]) -> list[int]:
        s = sum(weights) or 1
        out = [max(0, int(total * w / s)) for w in weights]
        return out

    assoc = [("EMPLOYEE", employees),
             ("MEMBER", max(0, base_followers - employees))]
    sen_w = [3, 9, 16, 22, 18, 12, 7, 4]
    seniority = [(f"urn:li:seniority:{sid}", c)
                 for sid, c in zip(_SENIORITIES, split(base_followers, sen_w))]
    fn_w = [40, 12, 20, 16, 12]
    function = [(f"urn:li:function:{fid}", c)
                for fid, c in zip(_FUNCTIONS, split(base_followers, fn_w))]
    staff_w = [10, 22, 26, 18, 12, 7, 5]
    staff = [(rng_seg, c) for rng_seg, c in
             zip(_STAFF_RANGES, split(base_followers, staff_w))]
    geo_w = [55, 14, 11, 10, 10]
    geo = [(f"urn:li:geo:{gid}", c)
           for gid, c in zip(_GEOS, split(base_followers, geo_w))]
    ind_w = [38, 22, 18, 12, 10]
    industry = [(f"urn:li:industry:{iid}", c)
                for iid, c in zip(_INDUSTRIES, split(base_followers, ind_w))]

    import json
    await pool.execute(
        """INSERT INTO app_linkedin.follower_stats
            (org_pk, by_association_type, by_seniority, by_function,
             by_staff_count_range, by_geo_country, by_geo, by_industry)
           VALUES ($1,$2::jsonb,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb)""",
        org_pk,
        json.dumps(_facet(assoc, "associationType")),
        json.dumps(_facet(seniority, "seniority")),
        json.dumps(_facet(function, "function")),
        json.dumps(_facet(staff, "staffCountRange")),
        json.dumps(_facet(geo, "geo")),
        json.dumps(_facet(geo, "geo")),
        json.dumps(_facet(industry, "industry")),
    )

    return {"posts": n_posts, "shareStats": 1, "followerStats": 1}
