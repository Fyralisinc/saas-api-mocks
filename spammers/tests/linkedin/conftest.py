"""Fixtures for the LinkedIn mock fidelity suite (organization Community-Management
read surface). LinkedIn is POLL-ONLY — no webhook.

Seeds a deterministic organization with:
  * five POSTS (4 shares + 1 ugcPost) — enough to walk a 3-page OFFSET cursor at
    count=2 and exercise both URN types + the epoch-millis timestamps;
  * one lifetime SHARE_STATS aggregate (totalShareStatistics);
  * one lifetime FOLLOWER_STATS row (facet breakdowns).

Wires the LinkedIn ``state`` singleton + an ASGI client.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

ACCESS_TOKEN = "AQX-linkedin_at_fidelityMockToken000000000000000"
CLIENT_ID = "linkedin_id_fidelity0001"
CLIENT_SECRET = "linkedin_secret_fidelity0001"
ORG_ID = 80411507
ORG_URN = f"urn:li:organization:{ORG_ID}"
LOCALIZED_NAME = "Alpen Labs"
VANITY_NAME = "alpen-labs"
VERSION = "202605"

VNOW = datetime(2026, 2, 1, tzinfo=timezone.utc)
INC = VNOW - timedelta(days=1100)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


# (post_id, urn_type, commentary, days_ago, edited)
POSTS = [
    (7120000000000000001, "ugcPost", "We're hiring across engineering! #hiring", 400, False),
    (7120000000000000002, "share", "Shipped a 3x performance improvement. #performance", 300, True),
    (7120000000000000003, "share", "Welcoming new teammates this month. #team", 200, False),
    (7120000000000000004, "share", "Crossed a major reliability target. #reliability", 100, False),
    (7120000000000000005, "share", "Reflecting on the last sprint. Onwards. #buildinpublic", 20, False),
]


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def linkedin_run(pool) -> UUID:
    run_id = uuid4()
    await pool.execute(
        """INSERT INTO org.runs (id, size, runtime, seed, fyralis_tenant_id,
               fyralis_base_url, virtual_now, mode, speed_multiplier)
           VALUES ($1,'small','few_months',31,$2,'http://localhost:8000',$3,'frozen',1.0)""",
        run_id, uuid4(), VNOW)
    org_pk = uuid4()
    await pool.execute(
        """INSERT INTO app_linkedin.organizations
            (id, run_id, base_url, org_id, org_urn, localized_name, vanity_name,
             website, access_token, client_id, client_secret, created_at)
           VALUES ($1,$2,'https://api.linkedin.com',$3,$4,$5,$6,
                   'https://alpenlabs.com',$7,$8,$9,$10)""",
        org_pk, run_id, ORG_ID, ORG_URN, LOCALIZED_NAME, VANITY_NAME,
        ACCESS_TOKEN, CLIENT_ID, CLIENT_SECRET, INC)

    for i, (pid, urn_type, commentary, days, edited) in enumerate(POSTS):
        created = VNOW - timedelta(days=days)
        last_mod = created + (timedelta(hours=6) if edited else timedelta(0))
        await pool.execute(
            """INSERT INTO app_linkedin.posts
                (id, org_pk, post_id, urn_type, commentary, lifecycle_state, visibility,
                 feed_distribution, is_edited, is_reshare_disabled, created_at_ms,
                 last_modified_ms, published_at_ms, sort_key, created_at)
               VALUES ($1,$2,$3,$4,$5,'PUBLISHED','PUBLIC','MAIN_FEED',$6,FALSE,
                       $7,$8,$9,$10,$11)""",
            uuid4(), org_pk, pid, urn_type, commentary, edited,
            _ms(created), _ms(last_mod), _ms(created), i, created)

    await pool.execute(
        """INSERT INTO app_linkedin.share_stats
            (org_pk, click_count, comment_count, engagement, impression_count,
             like_count, share_count, unique_impressions_count)
           VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
        org_pk, 109276, 70, 0.007549471334119487, 14490816, 52, 0, 9327)

    def facet(segs, key, paid=0):
        return [{"followerCounts": {"organicFollowerCount": c, "paidFollowerCount": paid},
                 key: s} for s, c in segs]

    await pool.execute(
        """INSERT INTO app_linkedin.follower_stats
            (org_pk, by_association_type, by_seniority, by_function,
             by_staff_count_range, by_geo_country, by_geo, by_industry)
           VALUES ($1,$2::jsonb,$3::jsonb,$4::jsonb,$5::jsonb,$6::jsonb,$7::jsonb,$8::jsonb)""",
        org_pk,
        json.dumps(facet([("EMPLOYEE", 196), ("MEMBER", 1402)], "associationType")),
        json.dumps(facet([("urn:li:seniority:2", 4), ("urn:li:seniority:4", 88)], "seniority")),
        json.dumps(facet([("urn:li:function:22", 1662)], "function")),
        json.dumps(facet([("SIZE_1", 29), ("SIZE_2_TO_10", 6)], "staffCountRange")),
        json.dumps(facet([("urn:li:geo:103644278", 66)], "geo")),
        json.dumps(facet([("urn:li:geo:90009626", 84)], "geo")),
        json.dumps(facet([("urn:li:industry:96", 33)], "industry")),
    )
    return run_id


@pytest_asyncio.fixture(loop_scope="session")
async def linkedin_client(pool, linkedin_run):
    from spammers.linkedin import state as li_state
    from spammers.linkedin.app import create_app, _FORCED_429

    li_state._STATE = li_state.LinkedinMockState(pool=pool, run_id=linkedin_run)
    _FORCED_429["count"] = 0
    transport = ASGITransport(app=create_app())
    async with AsyncClient(transport=transport, base_url="http://mock") as c:
        yield c
    li_state._STATE = None


@pytest.fixture
def linkedin_headers() -> dict[str, str]:
    """The three headers every versioned /rest/ call needs."""
    return {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Linkedin-Version": VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
