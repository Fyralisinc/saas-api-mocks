-- =============================================================================
-- app_quickbooks.*  — QuickBooks Online projection state
--
-- One company (tenant) per run, identified by realm_id like the QBO API. The
-- chart of accounts, vendors, employees, deposits, purchases, and journal
-- entries hang off the company. Mirrors the QuickBooks Online v3 resource
-- shape closely enough that a real ingestion connector could query against it.
--
-- All monetary amounts are stored as cents (BIGINT) to avoid floating-point
-- rounding — QBO native is decimal/2; we collapse to cents at ingest.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_quickbooks;

CREATE TABLE IF NOT EXISTS app_quickbooks.companies (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    realm_id TEXT NOT NULL,
    company_name TEXT NOT NULL,
    legal_name TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'US',
    currency TEXT NOT NULL DEFAULT 'USD',
    fiscal_year_start TEXT NOT NULL DEFAULT 'January',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id, realm_id)
);

-- Chart of accounts. account_number is the conventional QBO numeric code
-- (1000=Bank, 3000=Equity, 5xxx=Expense). type/subtype follow the QBO taxonomy
-- (Bank, Income, Expense, Equity, AccountsPayable, etc.).
CREATE TABLE IF NOT EXISTS app_quickbooks.accounts (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_quickbooks.companies(id) ON DELETE CASCADE,
    account_id TEXT NOT NULL,                  -- QBO's API resource ID
    account_number TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    subtype TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT 'USD',
    current_balance_cents BIGINT NOT NULL DEFAULT 0,   -- running balance (signed)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (company_pk, account_id),
    UNIQUE (company_pk, account_number)
);
CREATE INDEX IF NOT EXISTS qb_accounts_company_idx ON app_quickbooks.accounts(company_pk);

CREATE TABLE IF NOT EXISTS app_quickbooks.vendors (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_quickbooks.companies(id) ON DELETE CASCADE,
    vendor_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    currency TEXT NOT NULL DEFAULT 'USD',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (company_pk, vendor_id),
    UNIQUE (company_pk, display_name)
);

CREATE TABLE IF NOT EXISTS app_quickbooks.employees (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_quickbooks.companies(id) ON DELETE CASCADE,
    employee_id TEXT NOT NULL,
    person_id UUID REFERENCES org.people(id),
    display_name TEXT NOT NULL,
    email TEXT,
    title TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL DEFAULT '',
    location_bucket TEXT NOT NULL DEFAULT 'other',
    annual_salary_cents BIGINT NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    hired_at DATE NOT NULL,
    released_at DATE,                          -- NULL if still employed
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (company_pk, employee_id)
);
CREATE INDEX IF NOT EXISTS qb_employees_company_idx ON app_quickbooks.employees(company_pk);

-- Deposit = money in. One per funding round (or other inbound cash event).
-- credit_account_pk is the equity/income account that gets credited;
-- deposit_to_account_pk is the bank account that gets debited.
CREATE TABLE IF NOT EXISTS app_quickbooks.deposits (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_quickbooks.companies(id) ON DELETE CASCADE,
    deposit_id TEXT NOT NULL,
    txn_date DATE NOT NULL,
    amount_cents BIGINT NOT NULL,
    deposit_to_account_pk UUID REFERENCES app_quickbooks.accounts(id),
    credit_account_pk UUID REFERENCES app_quickbooks.accounts(id),
    round_id TEXT,                             -- corpus round id ('round:seed' etc.)
    round_kind TEXT,                           -- 'seed' | 'strategic' | 'grant' | 'founders_capital'
    lead TEXT,
    participants JSONB NOT NULL DEFAULT '[]'::jsonb,
    memo TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (company_pk, deposit_id)
);
CREATE INDEX IF NOT EXISTS qb_deposits_company_date_idx
    ON app_quickbooks.deposits(company_pk, txn_date DESC);

-- Purchase = money out. Payroll, opex, conferences, audits, etc. Every
-- Purchase is a single-line transaction debiting expense_account and
-- crediting payment_account (bank).
CREATE TABLE IF NOT EXISTS app_quickbooks.purchases (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_quickbooks.companies(id) ON DELETE CASCADE,
    purchase_id TEXT NOT NULL,
    txn_date DATE NOT NULL,
    amount_cents BIGINT NOT NULL,
    vendor_pk UUID REFERENCES app_quickbooks.vendors(id),
    employee_pk UUID REFERENCES app_quickbooks.employees(id),     -- set for payroll
    expense_account_pk UUID REFERENCES app_quickbooks.accounts(id),
    payment_account_pk UUID REFERENCES app_quickbooks.accounts(id),
    category TEXT NOT NULL,                    -- payroll | compute | legal | travel | offsite | audit | recruiting | etc.
    memo TEXT NOT NULL DEFAULT '',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,   -- extra structured metadata (attendees list etc.)
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (company_pk, purchase_id)
);
CREATE INDEX IF NOT EXISTS qb_purchases_company_date_idx
    ON app_quickbooks.purchases(company_pk, txn_date DESC);
CREATE INDEX IF NOT EXISTS qb_purchases_company_category_idx
    ON app_quickbooks.purchases(company_pk, category, txn_date DESC);
