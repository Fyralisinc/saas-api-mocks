-- =============================================================================
-- app_ashby.*  — Ashby (recruiting / applicant-tracking) projection state
--
-- One Ashby ORGANIZATION (the hiring company) per run, identified by its API key
-- + webhook secret (like grafana's instance / mercury's organization). Under it
-- hangs a single stream of ENTITIES — the recruiting signal set Fyralis shards
-- one-per-type: candidate, application, job, interview, offer.
--
-- Ashby is an RPC-style API: every read is an HTTP POST to ``/<category>.list``
-- (cursor-paginated, + an incremental ``syncToken``) or ``/<category>.info`` (one
-- entity by id). All five entity types share the SAME list/cursor/syncToken
-- machinery, so they live in one table discriminated by ``kind``; the full wire
-- object is stored verbatim in ``data`` (JSONB) and returned as-is, with the
-- denormalised ``created_at``/``updated_at``/``status`` columns driving the
-- ordering, the syncToken floor, and (status) the state_change discriminator.
--
-- The live push surface is the HMAC-signed webhook (``Ashby-Signature:
-- sha256=<hex>``), an independent path that carries the FULL entity body under
-- ``{action, data:{<entity>:{…}}}``.
--
-- Timestamps are TIMESTAMPTZ; the wire ``createdAt``/``updatedAt`` render as
-- ISO-8601 UTC with millisecond precision + ``Z`` (e.g. ``2024-01-15T10:30:00.000Z``).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_ashby;

CREATE TABLE IF NOT EXISTS app_ashby.organizations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.ashbyhq.com (or the mock)
    org_id TEXT NOT NULL,                    -- the Ashby organization identifier (scope key)
    legal_business_name TEXT NOT NULL,
    api_key TEXT NOT NULL,                   -- HTTP Basic username; empty password
    webhook_secret TEXT NOT NULL,            -- HMAC-SHA256 webhook signing key (Ashby-Signature)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One recruiting entity per row. ``entity_id`` is Ashby's entity UUID — the value
-- used as ``id`` on the wire AND as the lookup key for ``.info``. ``kind`` is the
-- RPC category (candidate|application|job|interview|offer). ``data`` is the exact
-- wire object (returned verbatim by ``.list``/``.info``). ``status`` is the
-- denormalised lifecycle status (NULL for kinds without one, e.g. interview) used
-- both as a ``.list`` filter input and as the terminal-state discriminator. The
-- list walk is a stable (updated_at ASC, entity_id ASC) order; a ``syncToken``
-- floor filters ``updated_at > floor``.
CREATE TABLE IF NOT EXISTS app_ashby.entities (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_ashby.organizations(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,                      -- candidate|application|job|interview|offer
    entity_id UUID NOT NULL,                 -- wire `id` + `.info` lookup key
    status TEXT,                             -- denormalised lifecycle status (nullable)
    data JSONB NOT NULL,                     -- the full wire object, returned verbatim
    created_at TIMESTAMPTZ NOT NULL,         -- wire `createdAt`
    updated_at TIMESTAMPTZ NOT NULL,         -- wire `updatedAt` (cursor/syncToken key)
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (org_pk, kind, entity_id)
);
-- The list endpoint walks one kind in (updated_at, entity_id) order; index it.
CREATE INDEX IF NOT EXISTS ashby_entities_kind_updated_idx
    ON app_ashby.entities(org_pk, kind, updated_at, entity_id);
