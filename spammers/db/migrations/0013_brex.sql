-- =============================================================================
-- app_brex.*  — Brex (corporate cards + cash management) projection state
--
-- One Brex organization (the business) per run, identified by its API token
-- (Bearer ``bxt_…``) + webhook secret (Svix ``whsec_…``), like mercury's org.
-- Under it hang ACCOUNTS (cash + card) and a stream of TRANSACTIONS — the cash
-- movements and card charges a real Brex account accumulates.
--
-- Brex's REAL API (api.brex.com, ``/v2/``) is NOTHING like the Mercury clone the
-- Fyralis flow doc carries (which it flags as TODO(human)/UNVERIFIED). The mock
-- honours the REAL wire contract; the Fyralis-vs-real divergences are LOGGED in
-- the brex-fidelity-audit memory, not papered over.
--
-- Two account FAMILIES in one table, discriminated by ``kind``:
--   * cash  — GET /v2/accounts/cash  (CURSOR page {next_cursor, items}); a
--             CashAccount has name/status/account_number/routing_number/primary +
--             current_balance/available_balance (Money).
--   * card  — GET /v2/accounts/card  (BARE ARRAY, no pagination); a CardAccount
--             has status + current/available_balance/account_limit (Money) +
--             current_statement_period{start_date,end_date}.
--
-- TRANSACTIONS hang off an account; two endpoints, both CURSOR-paginated:
--   * GET /v2/transactions/cash/{id}      (cash account id)
--   * GET /v2/transactions/card/primary   (the primary card account)
-- A txn has id/description/amount(Money)/initiated_at_date/posted_at_date (DATE
-- only, YYYY-MM-DD) + a per-family ``type`` enum. MONEY IS INTEGER CENTS, SIGNED
-- (Brex amounts are minor units on the wire — NOT dollars like mercury; emitted
-- verbatim, no conversion). An internal ``posted_at`` TIMESTAMPTZ drives the
-- ``posted_at_start`` (date-time) filter and the deterministic page order; the
-- wire only exposes the DATE.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_brex;

CREATE TABLE IF NOT EXISTS app_brex.organizations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.brex.com (or the mock)
    legal_business_name TEXT NOT NULL,
    company_id TEXT NOT NULL,                -- Team-API company id (webhook `company_id`)
    api_token TEXT NOT NULL,                 -- Bearer user token (bxt_…)
    webhook_secret TEXT NOT NULL,            -- Svix signing secret (whsec_…)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One account per row. ``account_id`` is Brex's opaque account id — the wire `id`
-- AND the {id} path segment for cash transactions. ``kind`` ∈ (cash|card).
-- ``is_primary`` marks the single primary cash/card account (/cash/primary,
-- /transactions/card/primary). ``sort_key`` gives a stable order for the cash
-- cursor page. Card-only columns (account_limit, statement period) are NULL for
-- cash rows; cash-only columns (account_number, routing_number) NULL for card.
CREATE TABLE IF NOT EXISTS app_brex.accounts (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_brex.organizations(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL,                -- wire `id` + {id} path segment
    kind TEXT NOT NULL,                      -- 'cash' | 'card'
    name TEXT,                               -- CashAccount.name (cash only)
    status TEXT NOT NULL DEFAULT 'ACTIVE',   -- Status enum (only ACTIVE)
    account_number TEXT,                     -- cash only
    routing_number TEXT,                     -- cash only
    currency TEXT NOT NULL DEFAULT 'USD',
    current_balance_cents BIGINT,            -- Money.amount (nullable on card)
    available_balance_cents BIGINT,          -- Money.amount (nullable on card)
    account_limit_cents BIGINT,              -- card only (Money, nullable)
    statement_start DATE,                    -- card current_statement_period.start_date
    statement_end DATE,                      -- card current_statement_period.end_date
    is_primary BOOLEAN NOT NULL DEFAULT FALSE,
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (org_pk, account_id)
);
CREATE INDEX IF NOT EXISTS brex_accounts_org_idx
    ON app_brex.accounts(org_pk, kind, sort_key, account_id);

-- One transaction per row. ``txn_id`` is Brex's opaque transaction id (wire `id`).
-- ``amount_cents`` is SIGNED integer minor units (NOT dollars): on the CARD ledger
-- a PURCHASE is positive (a charge), a REFUND/COLLECTION negative (a credit/payment
-- to Brex); on the CASH ledger incoming is positive, outgoing negative. ``txn_type``
-- is the per-family enum (CashTransactionType / CardTransactionType). ``posted_at``
-- (TIMESTAMPTZ) is internal — the wire exposes only ``initiated_at_date`` /
-- ``posted_at_date`` (DATE), but ``posted_at`` drives the ``posted_at_start``
-- date-time filter and the stable cursor order.
CREATE TABLE IF NOT EXISTS app_brex.transactions (
    id UUID PRIMARY KEY,
    account_pk UUID NOT NULL REFERENCES app_brex.accounts(id) ON DELETE CASCADE,
    account_kind TEXT NOT NULL,              -- 'cash' | 'card' (the endpoint family)
    txn_id TEXT NOT NULL,                    -- wire `id`
    description TEXT NOT NULL DEFAULT '',
    amount_cents BIGINT,                     -- Money.amount, signed (nullable on cash)
    currency TEXT NOT NULL DEFAULT 'USD',
    txn_type TEXT,                           -- Cash/CardTransactionType enum (nullable)
    initiated_at TIMESTAMPTZ NOT NULL,       -- -> wire `initiated_at_date` (DATE)
    posted_at TIMESTAMPTZ NOT NULL,          -- -> wire `posted_at_date` (DATE) + filter/order
    transfer_id TEXT,                        -- cash only (join into Payments API)
    card_id TEXT,                            -- card only (nullable)
    merchant_raw_descriptor TEXT,            -- card only
    merchant_mcc TEXT,                       -- card only
    merchant_country TEXT,                   -- card only (ISO 3166-1 alpha-3)
    expense_id TEXT,                         -- card only
    sort_key BIGINT NOT NULL DEFAULT 0,      -- stable secondary order within a posted_at
    version INTEGER NOT NULL DEFAULT 1,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (account_pk, txn_id)
);
-- The list endpoints are a forward cursor walk ordered by (posted_at, sort_key)
-- ascending, bounded below by the ``posted_at_start`` filter. Index that ordering.
CREATE INDEX IF NOT EXISTS brex_transactions_account_posted_idx
    ON app_brex.transactions(account_pk, posted_at ASC, sort_key ASC);
