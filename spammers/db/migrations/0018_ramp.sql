-- =============================================================================
-- app_ramp.*  — Ramp (corporate cards + bill-pay + reimbursements) state
--
-- One Ramp business (the company) per run, identified by its OAuth client
-- (client_id/client_secret → a minted ``ramp_business_tok_…`` Bearer) + webhook
-- signing secret. Under it hang USERS (employees/cardholders), CARDS, a stream
-- of TRANSACTIONS (the corporate-card spend — the primary signal), and a stream
-- of REIMBURSEMENTS (employee out-of-pocket expenses).
--
-- Ramp's REAL API (api.ramp.com, ``/developer/v1``) is NOTHING like the
-- QuickBooks-Online SQL-query clone the Fyralis flow doc carries (which it flags
-- as TODO(human)/UNVERIFIED throughout). The mock honours the REAL wire contract
-- (pinned from docs.ramp.com's OpenAPI); the Fyralis-vs-real divergences are
-- LOGGED in the ramp-fidelity-audit memory, not papered over.
--
-- The load-bearing wire facts this schema serves:
--   * READS are REST list endpoints with KEYSET (cursor) pagination:
--       GET /developer/v1/transactions   → {data:[Transaction], page:{next}}
--       GET /developer/v1/reimbursements → {data:[Reimbursement], page:{next}}
--       GET /developer/v1/cards          → {data:[Card], page:{next}}
--       GET /developer/v1/users          → {data:[User], page:{next}}
--     ``page.next`` is a FULL URL embedding ``start=<last entity id>`` (a bare
--     UUID keyset cursor), NULL at EOF. page_size default 20 / max 100.
--   * MONEY is DUAL: the top-level ``amount`` is a NUMBER in MAJOR units (dollars),
--     while nested ``CurrencyAmount`` fields (original_transaction_amount,
--     line_items[].amount, payee_amount) are ``{amount:<int CENTS>, currency_code,
--     minor_unit_conversion_rate}``. We store cents and project both.
--   * Transactions key ``currency_code``; reimbursements key ``currency`` (sic).
--   * Timestamps are ISO-8601 with a ``+00:00`` OFFSET (NOT ``Z``), no microseconds;
--     reimbursement ``transaction_date`` is DATE-only.
--
-- ``sort_key`` is a monotonic per-entity integer giving the deterministic keyset
-- order; the wire ``start`` cursor is the last row's wire id, which the app
-- resolves back to its sort_key to continue the walk.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_ramp;

CREATE TABLE IF NOT EXISTS app_ramp.organizations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                   -- https://api.ramp.com (or the mock)
    legal_business_name TEXT NOT NULL,
    business_id TEXT NOT NULL,                -- the Ramp business uuid (webhook `business_id`)
    client_id TEXT NOT NULL,                  -- OAuth client_credentials client id
    client_secret TEXT NOT NULL,              -- OAuth client_credentials secret
    access_token TEXT NOT NULL,               -- a seed-stable minted ramp_business_tok_…
    webhook_secret TEXT NOT NULL,             -- X-Ramp-Signature HMAC-SHA256 key
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- Ramp users (employees / cardholders). entity-attribution backbone.
CREATE TABLE IF NOT EXISTS app_ramp.users (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_ramp.organizations(id) ON DELETE CASCADE,
    user_id TEXT NOT NULL,                    -- wire `id` (uuid)
    first_name TEXT NOT NULL DEFAULT '',
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'BUSINESS_USER',
    status TEXT NOT NULL DEFAULT 'USER_ACTIVE',
    department_id TEXT,
    department_name TEXT,
    location_id TEXT,
    location_name TEXT,
    manager_id TEXT,
    is_manager BOOLEAN NOT NULL DEFAULT FALSE,
    employee_id TEXT,
    business_id TEXT,                         -- User.business_id
    entity_id TEXT,
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (org_pk, user_id)
);
CREATE INDEX IF NOT EXISTS ramp_users_org_idx
    ON app_ramp.users(org_pk, sort_key, user_id);

-- Ramp cards (virtual/physical). carries the spend-control topology.
CREATE TABLE IF NOT EXISTS app_ramp.cards (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_ramp.organizations(id) ON DELETE CASCADE,
    card_id TEXT NOT NULL,                    -- wire `id` (uuid)
    display_name TEXT NOT NULL DEFAULT '',
    last_four TEXT NOT NULL DEFAULT '0000',
    cardholder_id TEXT,                       -- a users.user_id
    cardholder_name TEXT,
    card_program_id TEXT,
    entity_id TEXT,
    expiration TEXT,                          -- "MM-YY" style
    is_physical BOOLEAN NOT NULL DEFAULT FALSE,
    state TEXT NOT NULL DEFAULT 'ACTIVE',     -- ACTIVE|CHIP_LOCKED|SUSPENDED|TERMINATED|UNACTIVATED
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (org_pk, card_id)
);
CREATE INDEX IF NOT EXISTS ramp_cards_org_idx
    ON app_ramp.cards(org_pk, sort_key, card_id);

-- Ramp transactions (settled corporate-card spend) — the primary stream.
-- ``amount_cents`` is SIGNED integer minor units (a charge is POSITIVE — Ramp
-- shows spend as a positive number). The wire exposes BOTH the top-level
-- ``amount`` (dollars = cents/100) AND the nested ``original_transaction_amount``
-- CurrencyAmount (cents). ``user_transaction_time``/``settlement_date`` etc. are
-- TIMESTAMPTZ internally; the wire renders ISO-8601 with a ``+00:00`` offset.
CREATE TABLE IF NOT EXISTS app_ramp.transactions (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_ramp.organizations(id) ON DELETE CASCADE,
    txn_id TEXT NOT NULL,                     -- wire `id` (uuid)
    amount_cents BIGINT NOT NULL,             -- signed minor units (charge positive)
    currency_code TEXT NOT NULL DEFAULT 'USD',
    state TEXT NOT NULL DEFAULT 'CLEARED',    -- CLEARED|COMPLETION|DECLINED|ERROR|PENDING|PENDING_INITIATION
    sync_status TEXT NOT NULL DEFAULT 'SYNCED', -- NOT_SYNC_READY|SYNCED|SYNC_READY
    card_id TEXT,                             -- a cards.card_id
    card_present BOOLEAN NOT NULL DEFAULT FALSE,
    user_id TEXT,                             -- cardholder users.user_id
    cardholder_name TEXT,
    merchant_id TEXT,
    merchant_name TEXT,
    merchant_category_code TEXT,              -- MCC (string)
    sk_category_id INTEGER,                   -- Ramp category code (nullable)
    sk_category_name TEXT,
    memo TEXT,
    entity_id TEXT,
    user_transaction_time TIMESTAMPTZ NOT NULL,  -- when the user created the txn
    accounting_date TIMESTAMPTZ,
    settlement_date TIMESTAMPTZ NOT NULL,        -- when funds moved (drives keyset order)
    synced_at TIMESTAMPTZ,
    sort_key BIGINT NOT NULL DEFAULT 0,          -- monotonic keyset order
    version INTEGER NOT NULL DEFAULT 1,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (org_pk, txn_id)
);
CREATE INDEX IF NOT EXISTS ramp_transactions_org_idx
    ON app_ramp.transactions(org_pk, sort_key, txn_id);

-- Ramp reimbursements (employee out-of-pocket expense requests).
-- Note ``currency`` (NOT ``currency_code`` — the API is deliberately inconsistent
-- between transactions and reimbursements). ``transaction_date`` is DATE-only.
CREATE TABLE IF NOT EXISTS app_ramp.reimbursements (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_ramp.organizations(id) ON DELETE CASCADE,
    reimb_id TEXT NOT NULL,                   -- wire `id` (uuid)
    amount_cents BIGINT,                      -- signed minor units (nullable wire amount)
    currency TEXT NOT NULL DEFAULT 'USD',     -- reimbursements key `currency` (sic)
    state TEXT NOT NULL DEFAULT 'REIMBURSED',
    type TEXT NOT NULL DEFAULT 'OUT_OF_POCKET', -- MILEAGE|OUT_OF_POCKET|PAYBACK_*|PER_DIEM
    direction TEXT NOT NULL DEFAULT 'BUSINESS_TO_USER',
    user_id TEXT,
    user_email TEXT,
    user_full_name TEXT,
    merchant TEXT,
    merchant_id TEXT,
    transaction_date DATE,
    sync_status TEXT NOT NULL DEFAULT 'SYNCED',
    memo TEXT,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    submitted_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    synced_at TIMESTAMPTZ,
    sort_key BIGINT NOT NULL DEFAULT 0,       -- monotonic keyset order
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (org_pk, reimb_id)
);
CREATE INDEX IF NOT EXISTS ramp_reimbursements_org_idx
    ON app_ramp.reimbursements(org_pk, sort_key, reimb_id);
