-- =============================================================================
-- app_mercury.*  — Mercury (business banking) projection state
--
-- One Mercury organization (the business account holder) per run, identified by
-- its API token + webhook secret, like grafana's instance. Under it hang a
-- handful of bank ACCOUNTS (checking/savings/treasury) and a stream of
-- TRANSACTIONS — the cash movements a real Mercury account accumulates.
--
-- Mercury is a TWO-CHANNEL source. The historical pull surface is
-- ``GET /api/v1/accounts`` + ``GET /api/v1/account/{id}/transactions`` (offset
-- pagination, default 30-day window, newest-first). The live push surface is the
-- transaction webhook (a JSON-merge-patch event signed Stripe-style
-- ``Mercury-Signature: t=…,v1=…``), an independent stream.
--
-- MONEY IS STORED IN CENTS (BIGINT, signed: negative == debit/outflow) for
-- precision, and projected to DOLLARS (number, multipleOf 0.01) on the wire —
-- Mercury's accounts/transactions amounts are decimal dollars, NOT cents.
-- Timestamps are TIMESTAMPTZ; the wire ``createdAt``/``postedAt`` render as
-- RFC3339 UTC ``…Z`` (seconds precision).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_mercury;

CREATE TABLE IF NOT EXISTS app_mercury.organizations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.mercury.com/api/v1 (or the mock)
    legal_business_name TEXT NOT NULL,
    api_token TEXT NOT NULL,                 -- Bearer/Basic API token (secret-token:… prefix)
    webhook_secret TEXT NOT NULL,            -- HMAC-SHA256 webhook signing key (Mercury-Signature)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One bank account per row. ``account_id`` is Mercury's account UUID — the value
-- used as ``id`` on the wire AND as the {accountId} path segment. ``sort_key``
-- gives a stable order for the UUID-cursor pagination over /accounts.
CREATE TABLE IF NOT EXISTS app_mercury.accounts (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_mercury.organizations(id) ON DELETE CASCADE,
    account_id UUID NOT NULL,                -- wire `id` + {accountId} path segment
    name TEXT NOT NULL,
    nickname TEXT,                           -- nullable on the wire
    account_number TEXT NOT NULL,            -- full (Mercury's schema does not mask)
    routing_number TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',   -- AccountStatus: active|deleted|pending|archived
    type TEXT NOT NULL DEFAULT 'mercury',    -- AccountType: mercury|external|recipient
    kind TEXT NOT NULL DEFAULT 'checking',   -- free string (checking/savings/treasury/…)
    available_balance_cents BIGINT NOT NULL DEFAULT 0,
    current_balance_cents BIGINT NOT NULL DEFAULT 0,
    legal_business_name TEXT NOT NULL,
    dashboard_link TEXT NOT NULL DEFAULT '',
    can_receive_transactions BOOLEAN,        -- nullable on the wire
    sort_key INTEGER NOT NULL DEFAULT 0,     -- stable cursor order
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (org_pk, account_id)
);
CREATE INDEX IF NOT EXISTS mercury_accounts_org_idx
    ON app_mercury.accounts(org_pk, sort_key, account_id);

-- One transaction per row. ``txn_id`` is Mercury's transaction UUID (wire `id` +
-- the {transactionId} path segment for fetch-on-notify). ``amount_cents`` is
-- SIGNED: negative == debit/outflow (vendor payments), positive == credit/inflow
-- (deposits/wires). ``posted_at`` is NULL while ``status='pending'`` (cleared on
-- settle). ``version`` is the webhook ``resourceVersion`` counter (bumps on
-- update). Reads are newest-first within a [start, end] window over ``created_at``.
CREATE TABLE IF NOT EXISTS app_mercury.transactions (
    id UUID PRIMARY KEY,
    account_pk UUID NOT NULL REFERENCES app_mercury.accounts(id) ON DELETE CASCADE,
    txn_id UUID NOT NULL,                    -- wire `id` + {transactionId} path segment
    amount_cents BIGINT NOT NULL,            -- signed; negative == debit
    status TEXT NOT NULL DEFAULT 'sent',     -- pending|sent|cancelled|failed|reversed|blocked
    kind TEXT NOT NULL DEFAULT 'externalTransfer',  -- TransactionKind enum
    counterparty_id UUID,                    -- TransactionPartyId
    counterparty_name TEXT NOT NULL DEFAULT '',
    counterparty_nickname TEXT,              -- nullable
    note TEXT,                               -- nullable
    external_memo TEXT,                      -- nullable
    bank_description TEXT,                    -- nullable
    reason_for_failure TEXT,                 -- nullable
    check_number TEXT,                       -- nullable
    dashboard_link TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1,      -- webhook resourceVersion
    created_at TIMESTAMPTZ NOT NULL,         -- wire `createdAt`
    posted_at TIMESTAMPTZ,                   -- wire `postedAt` (NULL while pending)
    estimated_delivery_date TIMESTAMPTZ NOT NULL,  -- wire `estimatedDeliveryDate` (required)
    failed_at TIMESTAMPTZ,                   -- wire `failedAt` (nullable)
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (account_pk, txn_id)
);
-- The list endpoint is a windowed newest-first walk over created_at (default
-- order desc). Index that ordering per account.
CREATE INDEX IF NOT EXISTS mercury_transactions_account_created_idx
    ON app_mercury.transactions(account_pk, created_at DESC, txn_id DESC);
