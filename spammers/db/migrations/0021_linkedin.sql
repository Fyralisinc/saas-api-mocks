-- =============================================================================
-- app_linkedin.*  — LinkedIn (organization marketing / Community Management) state
--
-- One LinkedIn ORGANIZATION (the company's LinkedIn Page) per run, identified by an
-- OAuth Bearer access token + the organization URN (urn:li:organization:{id}). Under
-- it hang the three organization signal families the Fyralis flow doc names (it calls
-- them share / social_action / follower_stat), served via the REAL Community
-- Management REST surface (pinned from learn.microsoft.com/linkedin):
--
--   POSTS            — the org's published posts/shares (the primary, multi-page stream)
--   SHARE_STATS      — lifetime organizationalEntityShareStatistics (totalShareStatistics)
--   FOLLOWER_STATS   — lifetime organizationalEntityFollowerStatistics (facet breakdowns)
--
-- LinkedIn's REAL API (api.linkedin.com, ``/rest/``, Rest.li 2.0) is NOTHING like the
-- QuickBooks-Online / Carta SQL-``query`` clone the Fyralis flow doc carries (it flags
-- the whole read surface — endpoints, pagination, field names, scopes, OAuth refresh —
-- TODO(human)/UNVERIFIED throughout, "cloned wholesale from the Carta OAuth2
-- archetype"). The mock honours the REAL wire contract; the Fyralis-vs-real
-- divergences are LOGGED in the linkedin-fidelity-audit memory, not papered over.
--
-- The load-bearing wire facts this schema serves:
--   * READS are Rest.li FINDER collections under ``/rest/`` scoped by an organization
--     URN query param — ``GET /rest/posts?q=author&author={orgURN}`` (OFFSET paging,
--     ``start``/``count``, default count 10 / max 100; envelope
--     ``{elements:[…], paging:{start,count,links:[…]}}``; the EOF signal is a page with
--     FEWER elements than ``count``) + the two stats finders ``q=organizationalEntity``
--     (single lifetime ``elements`` row, no pagination).
--   * TIMESTAMPS are epoch-MILLIS INTEGERS (``createdAt``/``lastModifiedAt``/
--     ``publishedAt`` = e.g. 1634817395768) — NOT ISO strings, NOT the Carta-archetype
--     RFC3339 the Fyralis clone assumes.
--   * Every versioned ``/rest/`` call REQUIRES a ``Linkedin-Version: YYYYMM`` header
--     (missing → 400 VERSION_MISSING; out-of-window → 426 NONEXISTENT_VERSION) and
--     ``X-Restli-Protocol-Version: 2.0.0``; auth is ``Authorization: Bearer``.
--   * IDs are URNs: a post ``id`` is ``urn:li:share:{n}`` OR ``urn:li:ugcPost:{n}``;
--     the org is ``urn:li:organization:{n}``. The org ``id`` from /rest/organizations
--     is a bare INT.
--   * Errors are the CLASSIC 3-key envelope ``{message, serviceErrorCode, status}``
--     (NOT google.rpc.Status, NOT a Fault). 429 carries the throttle message and
--     **NO Retry-After / NO X-RateLimit-*** headers (research-corrected: the Fyralis
--     client's "honours 429 Retry-After" assumption is unsupported by the docs).
--   * POLL-ONLY: LinkedIn org data has NO webhook / push of any kind (partner-gated,
--     no webhook entitlement; absent from Fyralis's VERIFIERS registry). So there is
--     no webhook secret/column here, no signature scheme, no live emit.
--
-- ``sort_key`` is a monotonic per-collection integer; offset paging slices the
-- ordered stream. Share/follower stats are lifetime aggregates (one row per org);
-- follower facet breakdowns are stored as JSONB arrays (emitted verbatim).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_linkedin;

CREATE TABLE IF NOT EXISTS app_linkedin.organizations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                   -- https://api.linkedin.com (or the mock)
    org_id BIGINT NOT NULL,                   -- the numeric organization id (URN tail)
    org_urn TEXT NOT NULL,                    -- urn:li:organization:{org_id}
    localized_name TEXT NOT NULL,             -- the Page display name
    vanity_name TEXT NOT NULL,                -- the Page vanity slug
    website TEXT,                             -- localizedWebsite
    access_token TEXT NOT NULL,               -- a seed-stable OAuth Bearer access token
    client_id TEXT NOT NULL,                  -- OAuth client id (operator-mediated)
    client_secret TEXT NOT NULL,              -- OAuth client secret
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id),
    UNIQUE (run_id, org_id)
);

-- POSTS (org shares/posts). The primary multi-page OFFSET stream. ``post_id`` is the
-- numeric URN tail; the wire ``id`` is ``urn:li:{urn_type}:{post_id}``. Timestamps are
-- epoch-MILLIS integers stored as BIGINT and emitted verbatim.
CREATE TABLE IF NOT EXISTS app_linkedin.posts (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_linkedin.organizations(id) ON DELETE CASCADE,
    post_id BIGINT NOT NULL,                  -- numeric URN tail
    urn_type TEXT NOT NULL DEFAULT 'share',   -- 'share' | 'ugcPost'
    commentary TEXT NOT NULL DEFAULT '',      -- the post body text (may be '')
    lifecycle_state TEXT NOT NULL DEFAULT 'PUBLISHED',  -- PUBLISHED|DRAFT|…
    visibility TEXT NOT NULL DEFAULT 'PUBLIC',
    feed_distribution TEXT NOT NULL DEFAULT 'MAIN_FEED',
    is_edited BOOLEAN NOT NULL DEFAULT FALSE, -- lifecycleStateInfo.isEditedByAuthor
    is_reshare_disabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at_ms BIGINT NOT NULL,            -- wire createdAt (epoch millis)
    last_modified_ms BIGINT NOT NULL,         -- wire lastModifiedAt (epoch millis)
    published_at_ms BIGINT NOT NULL,          -- wire publishedAt (epoch millis)
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (org_pk, post_id)
);
CREATE INDEX IF NOT EXISTS linkedin_posts_idx
    ON app_linkedin.posts(org_pk, last_modified_ms DESC, post_id DESC);

-- SHARE STATS — the lifetime organizationalEntityShareStatistics.totalShareStatistics
-- (one aggregate row per org). The seven documented counters + engagement (a double).
CREATE TABLE IF NOT EXISTS app_linkedin.share_stats (
    org_pk UUID PRIMARY KEY REFERENCES app_linkedin.organizations(id) ON DELETE CASCADE,
    click_count BIGINT NOT NULL DEFAULT 0,
    comment_count BIGINT NOT NULL DEFAULT 0,
    engagement DOUBLE PRECISION NOT NULL DEFAULT 0,
    impression_count BIGINT NOT NULL DEFAULT 0,
    like_count BIGINT NOT NULL DEFAULT 0,
    share_count BIGINT NOT NULL DEFAULT 0,
    unique_impressions_count BIGINT NOT NULL DEFAULT 0
);

-- FOLLOWER STATS — the lifetime organizationalEntityFollowerStatistics facet
-- breakdowns (one row per org). Each facet is a JSONB array of
-- ``{<segmentKey>, followerCounts:{organicFollowerCount, paidFollowerCount}}``.
-- The lifetime endpoint returns NO total (removed — use networkSizes for that), so
-- there is no total column here.
CREATE TABLE IF NOT EXISTS app_linkedin.follower_stats (
    org_pk UUID PRIMARY KEY REFERENCES app_linkedin.organizations(id) ON DELETE CASCADE,
    by_association_type JSONB NOT NULL DEFAULT '[]'::jsonb,
    by_seniority JSONB NOT NULL DEFAULT '[]'::jsonb,
    by_function JSONB NOT NULL DEFAULT '[]'::jsonb,
    by_staff_count_range JSONB NOT NULL DEFAULT '[]'::jsonb,
    by_geo_country JSONB NOT NULL DEFAULT '[]'::jsonb,
    by_geo JSONB NOT NULL DEFAULT '[]'::jsonb,
    by_industry JSONB NOT NULL DEFAULT '[]'::jsonb
);
