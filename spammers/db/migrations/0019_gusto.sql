-- =============================================================================
-- app_gusto.*  — Gusto (payroll + HR platform) state
--
-- One Gusto company per run, identified by its OAuth access token (minted at
-- ``POST /oauth/token``) + a webhook subscription verification_token (the HMAC
-- signing secret). Under it hang EMPLOYEES (the workforce directory) and a
-- stream of PAYROLLS (the periodic payroll runs — the primary finance signal).
--
-- Gusto's REAL API (api.gusto.com, ``/v1/companies/{company_uuid}/...``) is
-- NOTHING like the QuickBooks-Online SQL-``query`` clone the Fyralis flow doc
-- carries (which it flags TODO(human)/UNVERIFIED: host, query-vs-REST,
-- pagination, entity taxonomy = Invoice/Bill/BillPayment/Payment, webhook sig).
-- The mock honours the REAL wire contract (pinned from docs.gusto.com's
-- Embedded Payroll reference + the official auto-generated SDK); the
-- Fyralis-vs-real divergences are LOGGED in the gusto-fidelity-audit memory,
-- not papered over.
--
-- The load-bearing wire facts this schema serves:
--   * READS are REST list endpoints that return a BARE JSON ARRAY at the top
--     level (NO body envelope), with pagination metadata in RESPONSE HEADERS:
--       GET /v1/companies/{id}/employees → [Employee, …]   (X-Page/X-Total-Count/…)
--       GET /v1/companies/{id}/payrolls  → [Payroll, …]    (X-Page/X-Total-Count/…)
--       GET /v1/companies/{id}           → {Company}        (single object)
--     ``page``/``per`` query params (per default 25 / max 100); NO Link header.
--   * MONEY is a decimal STRING in MAJOR units (dollars to the cent), e.g.
--     ``"80000.00"`` / ``"2500.00"`` — NOT cents, NOT a number. We store cents
--     and project the decimal string.
--   * Datetimes are ISO-8601 with a ``Z`` suffix (``2025-06-16T16:58:03Z``);
--     date-only fields (check_date, pay_period dates, hire_date) are ``YYYY-MM-DD``.
--   * The payrolls list defaults to a 6-MONTH window (start_date=6mo ago) and
--     rejects a span > 1 year (422) — a faithful wrinkle, so a full backfill
--     walks ≤1-year windows (like mercury's 30-day / hibob's 6-month windows).
--
-- ``sort_key`` is a monotonic per-entity integer giving the deterministic page
-- order (offset-paginated by ``page``/``per``). Money is stored as integer cents.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_gusto;

CREATE TABLE IF NOT EXISTS app_gusto.companies (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                    -- https://api.gusto.com (or the mock)
    company_uuid TEXT NOT NULL,                -- wire `uuid` (the {company_uuid} path seg)
    name TEXT NOT NULL,
    trade_name TEXT,
    ein TEXT,                                  -- "88-1234567"
    entity_type TEXT NOT NULL DEFAULT 'C-Corporation',
    company_status TEXT NOT NULL DEFAULT 'Approved',
    tier TEXT,                                 -- e.g. 'complete'
    join_date DATE,
    pay_schedule_uuid TEXT,                    -- the company's regular pay schedule
    client_id TEXT NOT NULL,                   -- OAuth app client id
    client_secret TEXT NOT NULL,               -- OAuth app client secret
    access_token TEXT NOT NULL,                -- a seed-stable minted Bearer token
    refresh_token TEXT NOT NULL,               -- the (unused, rotated) refresh token
    webhook_secret TEXT NOT NULL,              -- subscription verification_token = HMAC key
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- Gusto employees (the workforce directory). The employee `version` string is the
-- optimistic-concurrency / dedup token (real Gusto field). `rate_cents` is the
-- current job compensation; the wire renders it as a decimal STRING in dollars.
CREATE TABLE IF NOT EXISTS app_gusto.employees (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_gusto.companies(id) ON DELETE CASCADE,
    employee_uuid TEXT NOT NULL,               -- wire `uuid`
    first_name TEXT NOT NULL DEFAULT '',
    middle_initial TEXT,
    last_name TEXT NOT NULL DEFAULT '',
    email TEXT,                                -- personal email
    work_email TEXT,
    department TEXT,
    employee_code TEXT,
    current_employment_status TEXT NOT NULL DEFAULT 'full_time',
    onboarding_status TEXT NOT NULL DEFAULT 'onboarding_completed',
    terminated BOOLEAN NOT NULL DEFAULT FALSE,
    onboarded BOOLEAN NOT NULL DEFAULT TRUE,
    hire_date DATE,
    termination_date DATE,
    date_of_birth DATE,
    manager_uuid TEXT,
    job_uuid TEXT,                             -- the primary job's uuid
    job_title TEXT,
    rate_cents BIGINT NOT NULL DEFAULT 0,      -- current compensation (annual or hourly)
    payment_unit TEXT NOT NULL DEFAULT 'Year', -- Year|Hour|Month|Week|…
    flsa_status TEXT NOT NULL DEFAULT 'Exempt',
    version TEXT NOT NULL,                      -- wire `version` (dedup token)
    sort_key INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (company_pk, employee_uuid)
);
CREATE INDEX IF NOT EXISTS gusto_employees_company_idx
    ON app_gusto.employees(company_pk, sort_key, employee_uuid);

-- Gusto payrolls (the periodic payroll runs) — the primary stream. Totals are
-- stored as integer cents; the wire renders each as a decimal STRING in dollars.
-- ``check_date`` / pay-period dates are DATE-only; ``processed_at``/``calculated_at``
-- are ISO-8601 ``Z`` datetimes. The list endpoint omits ``employee_compensations``
-- (that comes from the single-payroll GET) and omits ``totals`` unless
-- ``include=totals`` is requested.
CREATE TABLE IF NOT EXISTS app_gusto.payrolls (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_gusto.companies(id) ON DELETE CASCADE,
    payroll_uuid TEXT NOT NULL,                -- wire `uuid` / `payroll_uuid`
    pay_period_start DATE NOT NULL,
    pay_period_end DATE NOT NULL,
    check_date DATE NOT NULL,
    pay_schedule_uuid TEXT,
    processed BOOLEAN NOT NULL DEFAULT TRUE,
    off_cycle BOOLEAN NOT NULL DEFAULT FALSE,
    external BOOLEAN NOT NULL DEFAULT FALSE,
    payroll_type TEXT NOT NULL DEFAULT 'regular', -- regular|off_cycle|external
    processed_at TIMESTAMPTZ,                   -- ISO-8601 Z datetime
    calculated_at TIMESTAMPTZ,
    payroll_deadline TIMESTAMPTZ,
    gross_pay_cents BIGINT NOT NULL DEFAULT 0,
    net_pay_cents BIGINT NOT NULL DEFAULT 0,
    employer_taxes_cents BIGINT NOT NULL DEFAULT 0,
    employee_taxes_cents BIGINT NOT NULL DEFAULT 0,
    benefits_cents BIGINT NOT NULL DEFAULT 0,
    reimbursements_cents BIGINT NOT NULL DEFAULT 0,
    sort_key BIGINT NOT NULL DEFAULT 0,        -- monotonic page order (by check_date)
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (company_pk, payroll_uuid)
);
CREATE INDEX IF NOT EXISTS gusto_payrolls_company_idx
    ON app_gusto.payrolls(company_pk, sort_key, payroll_uuid);
