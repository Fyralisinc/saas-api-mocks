-- =============================================================================
-- app_carta.*  — Carta (cap-table / equity-management) state
--
-- One Carta ISSUER (the company on Carta) per run, identified by its OAuth client
-- (client_id/client_secret → a minted opaque Bearer access token). Under it hang
-- the four cap-table read collections the Fyralis flow doc names as its signal set
-- (shareholders, share classes, SAFE notes, option grants):
--
--   STAKEHOLDERS    — investors / employees / founders / board members
--   SHARE_CLASSES   — Common + the preferred rounds (Seed / Series A …)
--   OPTION_GRANTS   — ISO/NSO equity-incentive grants (the primary stream)
--   CONVERTIBLE_NOTES — SAFEs / convertible notes (Carta models SAFEs here)
--
-- Carta's REAL API (api.carta.com, ``/v1alpha1/``, Google-AIP-style) is NOTHING like
-- the QuickBooks-Online/Gusto SQL-``query`` clone the Fyralis flow doc carries (it
-- flags the whole read surface TODO(human)/UNVERIFIED). The mock honours the REAL
-- wire contract (pinned from docs.carta.com); the Fyralis-vs-real divergences are
-- LOGGED in the carta-fidelity-audit memory, not papered over.
--
-- The load-bearing wire facts this schema serves:
--   * READS are REST collections under ``/v1alpha1/issuers/{issuerId}/...`` with
--     Google **AIP-158 token pagination** — ``pageSize`` (default 25, per-endpoint
--     max) + opaque ``pageToken`` → response wraps the list under a PLURAL key
--     alongside ``nextPageToken`` (``{stakeholders:[…], nextPageToken}``); the
--     ``nextPageToken`` field is ABSENT on the last page (the EOF signal).
--   * Single-object GETs wrap under a SINGULAR key (``{issuer:{…}}``).
--   * MONEY + every decimal/quantity is a PROTOBUF WRAPPER object whose ``value`` is
--     a decimal STRING: Money = ``{currencyCode:{value:"USD"}, amount:{value:"<dec>"}}``;
--     a bare decimal/quantity = ``{value:"<dec>"}``. NOT a number, NOT integer cents.
--   * IDs are mixed: the issuer suite uses SHORT NUMERIC-STRING ids ("611"); the
--     cross-ref ``securityId``/``shareClassId`` are UUIDs.
--   * Timestamps are RFC3339 UTC with ``Z`` + microseconds (``lastModifiedDatetime``);
--     pure dates are ``YYYY-MM-DD`` (sometimes themselves ``{value}``-wrapped).
--   * NO SyncToken anywhere (a QBO-archetype carryover the Fyralis client expects);
--     only the security entities carry a ``lastModifiedDatetime`` to version on.
--   * POLL-ONLY: Carta has NO webhook / push of any kind — no signature scheme, no
--     event subscription. So there is no webhooks table / column here.
--
-- ``sort_key`` is a monotonic per-collection integer giving the deterministic
-- AIP-token order; the opaque ``pageToken`` carries the last row's sort_key.
-- Money / par value / quantities are stored as their numeric components and
-- projected to the decimal-string wrappers in the DTO layer.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_carta;

CREATE TABLE IF NOT EXISTS app_carta.issuers (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                   -- https://api.carta.com (or the mock)
    issuer_id TEXT NOT NULL,                  -- wire `id` — a SHORT NUMERIC STRING ("611")
    legal_name TEXT NOT NULL,
    doing_business_as_name TEXT,
    website TEXT,
    client_id TEXT NOT NULL,                  -- OAuth client_credentials client id
    client_secret TEXT NOT NULL,              -- OAuth client_credentials secret
    access_token TEXT NOT NULL,               -- a seed-stable minted opaque Bearer
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id),
    UNIQUE (run_id, issuer_id)
);

-- Stakeholders (investors / employees / founders / board). The shareholder set.
CREATE TABLE IF NOT EXISTS app_carta.stakeholders (
    id UUID PRIMARY KEY,
    issuer_pk UUID NOT NULL REFERENCES app_carta.issuers(id) ON DELETE CASCADE,
    stakeholder_id TEXT NOT NULL,             -- wire `id` (numeric string)
    full_name TEXT NOT NULL,
    email TEXT,
    employee_id TEXT,                         -- optional, <=256
    relationship TEXT NOT NULL,               -- EMPLOYEE|FOUNDER|EXECUTIVE|INVESTOR|BOARD_MEMBER|…
    grp TEXT,                                 -- wire `group` (optional)
    entity_type TEXT NOT NULL DEFAULT 'INDIVIDUAL', -- INDIVIDUAL|CORPORATION|…
    country TEXT,                             -- address.country
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (issuer_pk, stakeholder_id)
);
CREATE INDEX IF NOT EXISTS carta_stakeholders_idx
    ON app_carta.stakeholders(issuer_pk, sort_key, stakeholder_id);

-- Share classes (Common + preferred rounds). Reference data the securities point at.
CREATE TABLE IF NOT EXISTS app_carta.share_classes (
    id UUID PRIMARY KEY,
    issuer_pk UUID NOT NULL REFERENCES app_carta.issuers(id) ON DELETE CASCADE,
    share_class_id TEXT NOT NULL,             -- wire `id` (numeric string)
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,                     -- e.g. "CS", "PA"
    type TEXT NOT NULL,                       -- COMMON|PREFERRED
    authorized_shares BIGINT NOT NULL,        -- projected to {value:"<n>.00"} (decimal-string)
    par_value TEXT NOT NULL DEFAULT '0.0001', -- decimal STRING (Money.amount.value)
    currency_code TEXT NOT NULL DEFAULT 'USD',
    seniority INTEGER NOT NULL DEFAULT 0,
    pari_passu BOOLEAN NOT NULL DEFAULT FALSE,
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (issuer_pk, share_class_id)
);
CREATE INDEX IF NOT EXISTS carta_share_classes_idx
    ON app_carta.share_classes(issuer_pk, sort_key, share_class_id);

-- Option grants (ISO/NSO equity-incentive grants) — the primary multi-page stream.
-- Quantities are projected to decimal-string {value} wrappers; exercise price to a
-- Money wrapper. ``security_id`` is a UUID (the cross-ref id, distinct from the
-- numeric-string grant ``id``). Only this + convertible_notes carry a
-- ``last_modified`` (RFC3339-µs-Z) — Carta has NO SyncToken.
CREATE TABLE IF NOT EXISTS app_carta.option_grants (
    id UUID PRIMARY KEY,
    issuer_pk UUID NOT NULL REFERENCES app_carta.issuers(id) ON DELETE CASCADE,
    grant_id TEXT NOT NULL,                   -- wire `id` (numeric string)
    security_id TEXT NOT NULL,                -- wire `securityId` (UUID)
    share_class_id TEXT,                      -- wire `shareClassId` (UUID|null)
    stakeholder_id TEXT NOT NULL,             -- the grantee stakeholder.id
    plan_name TEXT NOT NULL DEFAULT 'Equity Incentive Plan',
    stock_option_type TEXT NOT NULL DEFAULT 'ISO',  -- ISO|NSO|OTHER
    quantity BIGINT NOT NULL,                 -- projected to {value:"<n>.00"}
    vested_quantity BIGINT NOT NULL DEFAULT 0,
    exercised_quantity BIGINT NOT NULL DEFAULT 0,
    exercise_price TEXT NOT NULL DEFAULT '0.00',  -- decimal STRING (Money.amount.value)
    currency_code TEXT NOT NULL DEFAULT 'USD',
    early_exercisable BOOLEAN NOT NULL DEFAULT FALSE,
    issue_date DATE NOT NULL,                 -- YYYY-MM-DD
    vesting_start_date DATE,
    grant_expiration_date DATE,
    last_modified TIMESTAMPTZ NOT NULL,       -- wire `lastModifiedDatetime` (RFC3339-µs-Z)
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (issuer_pk, grant_id)
);
CREATE INDEX IF NOT EXISTS carta_option_grants_idx
    ON app_carta.option_grants(issuer_pk, sort_key, grant_id);

-- Convertible notes / SAFEs. Carta models SAFEs as convertible notes. Money fields
-- (cash_paid / interest / price_cap) are projected to Money wrappers; rates to
-- bare decimal-string {value} wrappers. Carries ``last_modified``.
CREATE TABLE IF NOT EXISTS app_carta.convertible_notes (
    id UUID PRIMARY KEY,
    issuer_pk UUID NOT NULL REFERENCES app_carta.issuers(id) ON DELETE CASCADE,
    note_id TEXT NOT NULL,                    -- wire `id` (numeric string)
    security_id TEXT NOT NULL,                -- wire `securityId` (UUID)
    stakeholder_id TEXT NOT NULL,             -- the holder stakeholder.id
    security_label TEXT NOT NULL,             -- e.g. "SAFE-0007"
    cash_paid_cents BIGINT NOT NULL,          -- principal (Money, projected from cents)
    price_cap_cents BIGINT,                   -- valuation cap (Money|null)
    currency_code TEXT NOT NULL DEFAULT 'USD',
    interest_rate TEXT NOT NULL DEFAULT '0.00',     -- bare decimal-string {value}
    discount_percentage TEXT NOT NULL DEFAULT '0.00',
    interest_compounding_period TEXT NOT NULL DEFAULT 'ANNUALLY',
    day_count_basis TEXT NOT NULL DEFAULT 'COUNT_ACTUAL_365',
    issue_datetime TIMESTAMPTZ NOT NULL,      -- RFC3339-µs-Z
    maturity_datetime TIMESTAMPTZ,
    last_modified TIMESTAMPTZ NOT NULL,
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (issuer_pk, note_id)
);
CREATE INDEX IF NOT EXISTS carta_convertible_notes_idx
    ON app_carta.convertible_notes(issuer_pk, sort_key, note_id);
