-- =============================================================================
-- app_hibob.*  — HiBob ("Bob") HR-platform projection state
--
-- One HiBob company (the org) per run, identified by its Basic credential
-- (service-user id + token) + a webhook signing secret + a company_id (the
-- webhook tenant field, a numeric string). Under it hang EMPLOYEES (the People
-- directory), TIMEOFF_CHANGES (the time-off change stream) and SALARIES (the
-- bulk salary/payroll-history stream).
--
-- HiBob's REAL API (api.hibob.com, ``/v1/``) is NOTHING like the Gusto/Brex
-- archetype clone the Fyralis flow doc carries (which it flags TODO(human)/
-- UNVERIFIED at nearly every external knob). The mock honours the REAL wire
-- contract (pinned from apidocs.hibob.com); the Fyralis-vs-real divergences are
-- LOGGED in the hibob-fidelity-audit memory, not papered over. The big ones:
--   * the directory read is ``POST /v1/people/search`` (a JSON-body search that
--     returns ALL matching employees in one ``{employees:[…]}`` array — NO
--     pagination), NOT a GET ``/v1/people`` with ``?limit=&offset=``;
--   * time-off is pulled via ``GET /v1/timeoff/requests/changes`` (``since``/``to``
--     date window, a BARE ARRAY) — NOT ``GET /v1/timeoff/requests`` offset/limit;
--   * payroll history is ``GET /v1/bulk/people/salaries`` (CURSOR pagination,
--     ``{results, response_metadata:{next_cursor}}``) — NOT ``GET /v1/payroll/history``
--     offset/limit; there is NO ``/v1/people/lifecycle`` read endpoint at all;
--   * salary money is ``base:{value:<number>, currency}`` (a plain number in MAJOR
--     units) — the mock stores integer cents internally + renders the number;
--   * the webhook is ``Bob-Signature`` = base64(HMAC-SHA512(secret, body)) (no
--     prefix, no timestamp) — that part the Fyralis flow doc got right.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app_hibob;

CREATE TABLE IF NOT EXISTS app_hibob.companies (
    id UUID PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES org.runs(id) ON DELETE CASCADE,
    base_url TEXT NOT NULL,                  -- https://api.hibob.com (or the mock)
    legal_business_name TEXT NOT NULL,
    company_id TEXT NOT NULL,                -- webhook tenant id (companyId, numeric string)
    service_user_id TEXT NOT NULL,           -- Basic username (public half)
    service_user_token TEXT NOT NULL,        -- Basic password (secret half)
    webhook_secret TEXT NOT NULL,            -- Bob-Signature HMAC-SHA512 key
    created_at TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id)
);

-- One employee per worker — the People directory. ``employee_id`` is HiBob's
-- opaque numeric-string id (the wire ``id``). Names + work/about sections are
-- denormalised. ``modified`` is the per-row version key (drives the webhook
-- ``{ver}`` + the Fyralis incremental high-water). ``is_active`` gates the
-- ``showInactive`` search flag. ``sort_key`` gives a stable directory order.
CREATE TABLE IF NOT EXISTS app_hibob.employees (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_hibob.companies(id) ON DELETE CASCADE,
    employee_id TEXT NOT NULL,               -- wire `id` (numeric string)
    first_name TEXT NOT NULL,
    surname TEXT NOT NULL,
    second_name TEXT NOT NULL DEFAULT '',
    full_name TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT NOT NULL,
    avatar_url TEXT,
    work_title TEXT NOT NULL DEFAULT '',     -- work.title
    work_department TEXT NOT NULL DEFAULT '',-- work.department
    work_site TEXT NOT NULL DEFAULT '',      -- work.site
    work_manager_name TEXT,                  -- work.manager (display name)
    work_reports_to_id TEXT,                 -- work.reportsTo (employee id)
    work_start_date DATE,                    -- work.startDate (rendered DD/MM/YYYY)
    work_is_manager BOOLEAN NOT NULL DEFAULT FALSE,
    work_employee_id_in_company TEXT,        -- work.employeeIdInCompany (short id)
    about_text TEXT NOT NULL DEFAULT '',     -- about.about
    creation_date_time TIMESTAMPTZ NOT NULL, -- creationDateTime (ISO no-Z µs)
    modified TIMESTAMPTZ NOT NULL,           -- version key + incremental high-water
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    sort_key INTEGER NOT NULL DEFAULT 0,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (company_pk, employee_id)
);
CREATE INDEX IF NOT EXISTS hibob_employees_company_idx
    ON app_hibob.employees(company_pk, sort_key ASC, employee_id ASC);

-- One time-off CHANGE per row — the ``/v1/timeoff/requests/changes`` stream. Each
-- change is a snapshot at change time, filtered by its ``created_on`` (the change
-- date, NOT the leave start/end). ``request_id`` is HiBob's int64 request id (the
-- wire ``requestId``). ``change_type`` ∈ Created|Canceled|Deleted|Pending.
CREATE TABLE IF NOT EXISTS app_hibob.timeoff_changes (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_hibob.companies(id) ON DELETE CASCADE,
    request_id BIGINT NOT NULL,              -- wire `requestId` (int64)
    employee_id TEXT NOT NULL,               -- wire `employeeId`
    employee_display_name TEXT NOT NULL,
    employee_email TEXT NOT NULL,
    policy_type_display_name TEXT NOT NULL,  -- e.g. "Holiday" / "Sick"
    change_type TEXT NOT NULL,               -- Created|Canceled|Deleted|Pending
    status TEXT NOT NULL DEFAULT 'approved', -- approved|declined|cancelled|pending (request status)
    created_on TIMESTAMPTZ NOT NULL,         -- wire `createdOn` (the CHANGE date — `since`/`to` filter key)
    start_date DATE,                         -- wire `startDate` (leave window, YYYY-MM-DD)
    end_date DATE,                           -- wire `endDate`
    duration_unit TEXT NOT NULL DEFAULT 'days', -- days|hours
    total_duration NUMERIC NOT NULL DEFAULT 0,  -- wire `totalDuration`
    total_cost NUMERIC NOT NULL DEFAULT 0,      -- wire `totalCost`
    request_type TEXT NOT NULL DEFAULT 'days',  -- wire `type` (days|hours|…)
    sort_key BIGINT NOT NULL DEFAULT 0,
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    timeline_event_id UUID,
    UNIQUE (company_pk, request_id)
);
CREATE INDEX IF NOT EXISTS hibob_timeoff_company_created_idx
    ON app_hibob.timeoff_changes(company_pk, created_on ASC, sort_key ASC);

-- One salary ENTRY per row — the ``/v1/bulk/people/salaries`` (payroll-history)
-- stream, CURSOR-paginated. ``base_value_cents`` is integer cents internally and
-- rendered as ``base:{value:<number>, currency}`` (a plain number in major units).
-- ``is_current`` marks the active salary; raises add a second (older) entry.
CREATE TABLE IF NOT EXISTS app_hibob.salaries (
    id UUID PRIMARY KEY,
    company_pk UUID NOT NULL REFERENCES app_hibob.companies(id) ON DELETE CASCADE,
    salary_id BIGINT NOT NULL,               -- wire `id` (integer)
    employee_id TEXT NOT NULL,               -- wire `employeeId` (the link back)
    base_value_cents BIGINT NOT NULL,        -- base.value (-> number in major units)
    currency TEXT NOT NULL DEFAULT 'USD',    -- base.currency (ISO-4217)
    pay_period TEXT NOT NULL DEFAULT 'Annual',    -- Annual|Hourly|Daily|Weekly|Monthly
    pay_frequency TEXT NOT NULL DEFAULT 'Monthly',-- Monthly|Semi Monthly|Weekly|Bi-Weekly
    effective_date DATE NOT NULL,            -- wire `effectiveDate` (YYYY-MM-DD)
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    creation_date TIMESTAMPTZ NOT NULL,      -- wire `creationDate` (ISO no-Z µs)
    modification_date TIMESTAMPTZ NOT NULL,  -- wire `modificationDate`
    sort_key BIGINT NOT NULL DEFAULT 0,      -- stable cursor order
    is_historical BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (company_pk, salary_id)
);
CREATE INDEX IF NOT EXISTS hibob_salaries_company_sort_idx
    ON app_hibob.salaries(company_pk, sort_key ASC, salary_id ASC);
