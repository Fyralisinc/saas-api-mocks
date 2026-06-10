-- =============================================================================
-- app_deel.*  — Deel (global payroll / contractor payments) projection state
--
-- One Deel organization (the company) per run, identified by its API token
-- (Bearer, a long-lived org/personal token) + webhook signing secret + an
-- organization_id (the webhook tenant field). Under it hang CONTRACTS (one per
-- worker — contractor / EOR-employee / direct employee) and a stream of INVOICES
-- (the paid worker-invoice history — Deel's real "payments" surface).
--
-- Deel's REAL API (api.letsdeel.com, ``/rest/v2/``) is NOTHING like the Mercury
-- clone the Fyralis flow doc carries (which it flags TODO(human)/UNVERIFIED at
-- every external knob). The mock honours the REAL wire contract; the
-- Fyralis-vs-real divergences are LOGGED in the deel-fidelity-audit memory, not
-- papered over. The big divergences the real API forces:
--   * payments are NOT contract-nested — they are org-wide ``GET /rest/v2/invoices``
--     (each invoice carries ``contract_id``), NOT ``GET /contract/{id}/payments``;
--   * the list envelope is ``{data:[…], page:{…}}`` — NOT ``{total, payments:[…]}``;
--   * contracts paginate CURSOR-only (``limit`` + ``after_cursor``); invoices are
--     HYBRID (``limit`` + ``offset`` + ``cursor``);
--   * money is decimal STRINGS in major units (``"1000.00"``), NOT integer cents
--     and NOT a dollar number — the mock stores cents internally and renders the
--     decimal string on the wire;
--   * the webhook is ``x-deel-signature`` BARE HEX HMAC-SHA256 over ``"POST"+body``
--     (method string prepended), NOT ``Deel-Signature: sha256=<hex>`` over the body.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_deel;

CREATE TABLE IF NOT EXISTS app_deel.organizations (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.letsdeel.com/rest/v2 (or the mock)
    legal_business_name TEXT NOT NULL,
    organization_id TEXT NOT NULL,           -- webhook tenant id (data.meta.organization_id)
    api_token TEXT NOT NULL,                 -- Bearer org/personal token
    webhook_secret TEXT NOT NULL,            -- x-deel-signature HMAC key
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One contract per worker. ``contract_id`` is Deel's opaque contract id — the wire
-- ``id`` AND the {id} path segment for GET /rest/v2/contracts/{id}. Compensation is
-- stored as integer cents + currency internally and rendered as a decimal STRING on
-- the wire. ``status`` is Deel's large workflow enum (in_progress / onboarding /
-- completed / cancelled / …). ``sort_key`` gives a stable order for the cursor page.
CREATE TABLE IF NOT EXISTS app_deel.contracts (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_deel.organizations(id) ON DELETE CASCADE,
    contract_id TEXT NOT NULL,               -- wire `id` + {id} path segment
    type TEXT NOT NULL,                       -- ContractType enum (ongoing_time_based, eor, …)
    title TEXT NOT NULL,
    status TEXT NOT NULL,                     -- ContractStatus enum (in_progress, onboarding, …)
    worker_name TEXT NOT NULL,
    worker_email TEXT NOT NULL,
    worker_country TEXT NOT NULL,             -- ISO 3166-1 alpha-2
    client_name TEXT NOT NULL,
    job_title TEXT NOT NULL,
    comp_amount_cents BIGINT NOT NULL,        -- compensation_details.amount (-> decimal string)
    comp_currency TEXT NOT NULL DEFAULT 'USD',-- compensation_details.currency_code
    comp_frequency TEXT NOT NULL DEFAULT 'monthly',  -- compensation_details.frequency
    comp_scale TEXT NOT NULL DEFAULT 'monthly',       -- compensation_details.scale
    external_id TEXT,
    is_archived BOOLEAN NOT NULL DEFAULT FALSE,
    start_date DATE,                          -- contract start (DATE only)
    termination_date DATE,                    -- nullable
    created_at TIMESTAMPTZ NOT NULL,          -- -> wire `created_at` (RFC3339 ms Z)
    updated_at TIMESTAMPTZ NOT NULL,          -- -> wire `updated_at`
    sort_key INTEGER NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (org_pk, contract_id)
);
CREATE INDEX IF NOT EXISTS deel_contracts_org_idx
    ON app_deel.contracts(org_pk, created_at ASC, sort_key ASC, contract_id ASC);

-- One invoice per row — Deel's real "payment" record (a paid worker invoice).
-- ``invoice_id`` is the wire ``id``. ``contract_id`` is the wire contract link
-- (denormalised from the parent contract). Amounts are integer cents internally,
-- rendered as decimal STRINGS on the wire. ``status`` ∈ pending|paid|processing|
-- credited|refunded. ``issued_at`` drives the issued_from_date/issued_to_date
-- filter + the stable (issued_at, invoice_id) page order. A no-``status`` probe
-- returns ONLY paid invoices; ``status=all`` returns every status (a real Deel
-- wrinkle — a full backfill MUST pass ``status=all``).
CREATE TABLE IF NOT EXISTS app_deel.invoices (
    id UUID PRIMARY KEY,
    org_pk UUID NOT NULL REFERENCES app_deel.organizations(id) ON DELETE CASCADE,
    contract_pk UUID NOT NULL REFERENCES app_deel.contracts(id) ON DELETE CASCADE,
    invoice_id TEXT NOT NULL,                 -- wire `id`
    contract_id TEXT NOT NULL,                -- wire `contract_id`
    label TEXT NOT NULL,
    total_cents BIGINT NOT NULL,              -- invoice.total (-> decimal string)
    amount_cents BIGINT NOT NULL,             -- invoice.amount (-> decimal string)
    vat_cents BIGINT NOT NULL DEFAULT 0,      -- invoice.vat_total
    deel_fee_cents BIGINT NOT NULL DEFAULT 0, -- invoice.deel_fee
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL,                     -- InvoiceStatus enum
    issued_at TIMESTAMPTZ NOT NULL,           -- -> wire `issued_at` + filter/order key
    due_date TIMESTAMPTZ,                     -- -> wire `due_date`
    paid_at TIMESTAMPTZ,                      -- -> wire `paid_at` (null unless paid)
    created_at TIMESTAMPTZ NOT NULL,          -- -> wire `created_at`
    is_overdue BOOLEAN NOT NULL DEFAULT FALSE,
    recipient_legal_entity_id TEXT,
    sort_key BIGINT NOT NULL DEFAULT 0,
    version INTEGER NOT NULL DEFAULT 1,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (org_pk, invoice_id)
);
-- The invoices list is a forward walk ordered by (issued_at, sort_key) ascending,
-- optionally bounded by the issued_from_date/issued_to_date window. Index it.
CREATE INDEX IF NOT EXISTS deel_invoices_org_issued_idx
    ON app_deel.invoices(org_pk, issued_at ASC, sort_key ASC);
