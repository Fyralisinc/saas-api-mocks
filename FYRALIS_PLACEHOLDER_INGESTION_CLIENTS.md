# Fyralis ingestion connectors — placeholder clients that don't match the real APIs

**Audience:** Fyralis ingestion team
**Summary:** Nine of Fyralis's source ingestion clients (`brex`, `deel`, `ramp`, `gusto`,
`carta`, `linkedin`, `figma`, `fireflies`, `hibob`) are **placeholders cloned from two
internal archetypes** (Mercury and QuickBooks-Online) rather than built against the real
provider APIs. They call **endpoints, auth flows, pagination, and money/date formats that
do not exist on the real services**, so against production (or any real-API-faithful mock)
they return **404 / 401 / empty** and ingest **nothing**. This is a total-failure bug, not
a data-quality nuance.

---

## 1. How this was found

We pointed Fyralis's **real** ingestion clients (`services/ingest/ingestion/fetchers/_clients.py`
+ `services/ingest/integrations/<source>/client.py`) at a **real-API-faithful mock suite**
(the "spammer", which reproduces each provider's documented wire contract). The mocks are
single-tenant and accept any non-empty token, so auth is not the variable — only the
**request shape** is. Real clients for slack, discord, github, notion, quickbooks, grafana,
**ashby**, **miro**, mercury, jira, etc. pulled live data successfully. The nine clients
below **failed at the first request** (404 "resource not found" / 401 / empty enumerate).
Every divergence below is corroborated against the provider's **official docs** (links inline).

---

## 2. Root cause — two archetypes copied wholesale

The placeholder clients were not written against the providers' real APIs. They were copied
from two existing connectors and reskinned:

### Archetype A — "Mercury clone" (banking-shaped)
Path shape `GET /accounts`, `GET /account/{id}`, `GET /account/{id}/transactions`, Bearer +
opaque cursor/offset. Applied to: **brex, deel**.

### Archetype B — "QuickBooks-Online clone" (SQL-query-shaped)
Path shape `GET /v{n}/{company|firms|organizations}/{id}/query?query=SELECT…STARTPOSITION…&minorversion=NN`
plus a `…/companyinfo|firminfo|orginfo` probe, Bearer + `STARTPOSITION` paging. **None of
these providers expose a SQL-`query` endpoint.** Applied to: **ramp, gusto, carta, linkedin**.

The remaining three are individually wrong: **figma** calls non-existent endpoints,
**fireflies** uses a fake REST surface (the real API is GraphQL-only), **hibob** is the
closest but still uses wrong verbs/paths/pagination.

You can confirm the archetype at a glance:

```
brex     client paths: /accounts, /account/{id}/transactions          (Mercury archetype)
deel     client paths: /contracts, /contract/{id}/payments            (Mercury archetype)
ramp     client paths: /v3/company/{businessId}/query?query=SELECT…   (QBO archetype)
gusto    client paths: /v3/company/{company_uuid}/query?query=SELECT… (QBO archetype)
carta    client paths: /v1/firms/{firm_id}/query?query=SELECT…        (QBO archetype)
linkedin client paths: /v1/organizations/{org}/query?query=SELECT…    (QBO archetype)
figma    client paths: /v1/files (list), /v1/files/{key}/events       (endpoints don't exist)
fireflies client paths: GET /transcripts, /workspace, /transcript/{id} (real API is GraphQL)
hibob    client paths: GET /v1/people, /v1/timeoff/requests, /v1/payroll/history (wrong verbs/paths)
```

---

## 3. Per-source detail (current Fyralis → real API → fix)

### 3.1 brex — Mercury clone
- **Fyralis does:** `GET /accounts`, `GET /account/{id}`, `GET /account/{id}/transactions`;
  Bearer; treats money as Mercury-style dollars.
- **Real Brex API** ([developer.brex.com](https://developer.brex.com/)): base
  `https://platform.brexapis.com`, **`/v2`** surface. Accounts are split:
  `GET /v2/accounts/cash` (cursor `{next_cursor, items}`), `GET /v2/accounts/card`
  (bare array); transactions `GET /v2/transactions/cash/{id}`,
  `GET /v2/transactions/card/primary` (cursor; `posted_at_start` filter). **Money is a
  signed-integer-CENTS object** (`{amount:<int>, currency}`), dates are **DATE-only**,
  pagination is an **opaque base64url cursor** (`limit` default 100/max 1000), and webhooks
  are **Svix** (`Webhook-Signature: v1,<base64>`). `GET /accounts` returns **404**.
- **Fix:** new `/v2` client with cash/card split, opaque-cursor paging, cents-int money,
  date-only fields; Svix webhook verification.

### 3.2 deel — Mercury clone
- **Fyralis does:** `GET /contracts`, `GET /contract/{id}`, `GET /contract/{id}/payments`;
  Bearer; offset paging.
- **Real Deel API** (api.letsdeel.com, [developer.deel.com](https://developer.deel.com/)):
  base **`/rest/v2`**. `GET /rest/v2/contracts` (cursor `{data, page:{cursor, total_rows}}`),
  `GET /rest/v2/contracts/{id}` (wrapped `{data:{…}}`), and the real "payments" stream is
  **`GET /rest/v2/invoices`** (HYBRID `limit`+`offset`+`cursor`). Requires an **`X-Version`
  date header**; money is a **decimal STRING in major units**; timestamps RFC3339 ms+Z;
  webhook is `x-deel-signature` (bare-hex HMAC over **`"POST"+body`**). `GET /contracts`
  (no `/rest/v2`) returns **404**; `/contract/{id}/payments` doesn't exist.
- **Fix:** `/rest/v2` base; `/invoices` for payments; `{data, page}` envelope + hybrid
  paging; `X-Version` header; decimal-string money.

### 3.3 ramp — QuickBooks clone (egregious)
- **Fyralis does:** `GET /v3/company/{businessId}/query?query=SELECT…STARTPOSITION…&minorversion`
  + `/v3/company/{businessId}/companyinfo` — a **QuickBooks SQL-query API**.
- **Real Ramp API**
  ([docs.ramp.com/developer-api/v1](https://docs.ramp.com/developer-api/v1/api/transactions)):
  base **`/developer/v1`**, plain **REST collections** — `GET /developer/v1/transactions`,
  `/reimbursements`, `/cards`, `/users`. Auth is **OAuth 2.0 client-credentials** (mint a
  token at `POST /developer/v1/token` → `Bearer ramp_business_tok_…`). Pagination is
  **KEYSET** (`page.next` = a full URL embedding `start=<last entity id>`, `null` at EOF;
  `page_size` default 20/max 100). Money is **dual** (top-level `amount` number-dollars +
  nested `CurrencyAmount` int-cents). There is **no `/v3/company/{id}/query` endpoint** →
  404, and no token-mint step → it never even authenticates correctly.
- **Fix:** rebuild entirely against `/developer/v1` REST + the client-credentials token
  mint + keyset cursor; drop the QBO SQL shape completely.

### 3.4 gusto — QuickBooks clone (egregious)
- **Fyralis does:** `GET /v3/company/{company_uuid}/query?query=SELECT…STARTPOSITION…&minorversion=75`
  + `/companyinfo`.
- **Real Gusto API**
  ([docs.gusto.com](https://docs.gusto.com/app-integrations/reference/get-v1-companies-company_id-employees)):
  base **`/v1`**. `GET /v1/companies/{uuid}/employees`, `/payrolls` return a **bare JSON
  array** with pagination in **`X-Total-Count`/`X-Page`/`X-Per-Page` headers** (`page`/`per`
  params, `per` default 25/max 100, **no `Link` header**). OAuth Bearer (`POST /oauth/token`).
  Money is a **decimal STRING in dollars**; `X-Gusto-API-Version` header echoed. The QBO
  SQL endpoint does not exist → 404.
- **Fix:** `/v1/companies/{uuid}/employees|payrolls` bare-array + header pagination.

### 3.5 carta — QuickBooks clone (egregious)
- **Fyralis does:** `GET /v1/firms/{firm_id}/query?query=SELECT…STARTPOSITION…` + `/firminfo`.
- **Real Carta API** ([docs.carta.com](https://docs.carta.com/carta/reference/v1alpha1issuersliststakeholders)):
  base **`/v1alpha1`**, an **issuer** cap-table suite: `GET /v1alpha1/issuers`,
  `/v1alpha1/issuers/{id}/stakeholders|shareClasses|optionGrants|convertibleNotes`.
  Pagination is **Google AIP-158** (`pageSize` + opaque `pageToken` → `nextPageToken`,
  absent at EOF). **Money/decimals are protobuf wrappers** (`{value:"<decimal string>"}`).
  Auth is **OAuth client-credentials** at `POST /o/access_token/` (no refresh token).
  POLL-only (no webhook). The `firms/{id}/query` shape does not exist → 404.
- **Fix:** `/v1alpha1` issuer REST + AIP-token pagination + protobuf-wrapper decimal parsing
  + `/o/access_token/` client-creds.

### 3.6 linkedin — QuickBooks clone (egregious)
- **Fyralis does:** `GET /v1/organizations/{org}/query?query=SELECT…STARTPOSITION…` + `/orginfo`.
- **Real LinkedIn Community-Management API**
  ([Microsoft Learn](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api)):
  base **`/rest`**, **Rest.li finders** — `GET /rest/posts?q=author&author={urn:li:organization:N}`,
  `/rest/organizationalEntityShareStatistics`, `/rest/organizationalEntityFollowerStatistics`,
  `/rest/organizations/{id}`. Pagination is **OFFSET** (`start`/`count`, max 100). **Every
  call requires `Linkedin-Version: YYYYMM` + `X-Restli-Protocol-Version: 2.0.0` headers**
  (missing → 400/426). Timestamps are **epoch-millis integers**. POLL-only. The
  `organizations/{org}/query` SQL shape does not exist → 404.
- **Fix:** `/rest` Rest.li finders + version headers + OFFSET paging + epoch-millis parsing.

### 3.7 figma — non-existent endpoints
- **Fyralis does:** `GET /v1/files` (a file LIST), `GET /v1/files/{key}/events` (an events
  stream), `/v1/files/{key}/meta`.
- **Real Figma API** ([developers.figma.com](https://developers.figma.com/docs/rest-api/file-endpoints/)):
  **there is no `/v1/files` list and no `/events` endpoint.** You enumerate
  `GET /v1/teams/{team_id}/projects` → `GET /v1/projects/{project_id}/files` → then per file
  **merge** `GET /v1/files/{key}/versions` (cursor `before`/`after`, full-URL links,
  page_size 30/50) and `GET /v1/files/{key}/comments` (no pagination). Auth is `X-Figma-Token`
  **or** Bearer; a missing token on file reads is **403** (not 401). Webhook is **Webhooks-v2
  plaintext passcode** (no HMAC). `GET /v1/files` and `/events` return **404**.
- **Fix:** teams→projects→files enumeration + versions/comments merge; 403 on unauth;
  passcode webhook.

### 3.8 fireflies — REST clone of a GraphQL-only API
- **Fyralis does:** `GET /transcripts?limit&offset&start`, `GET /workspace`,
  `GET /transcript/{id}` — a REST surface.
- **Real Fireflies API**
  ([docs.fireflies.ai/graphql-api](https://docs.fireflies.ai/graphql-api/query/transcript)):
  **GraphQL only** — a single `POST https://api.fireflies.ai/graphql` exposing
  `transcripts(skip, limit≤50, fromDate, toDate)`, `transcript(id)`, and `user` (which is
  the **API-key owner** — there is **no workspace id**). Bearer api-key. `transcript.date`
  is **epoch-millis**. The REST paths (`/transcripts`, `/workspace`, `/transcript/{id}`) do
  not exist → 404, and there is no "workspace" object to fetch.
- **Fix:** GraphQL client (`POST /graphql` with field selection); identity = `user` query;
  drop the fake `/workspace`.

### 3.9 hibob — closest, but still wrong verbs/paths/pagination
- **Fyralis does:** `GET /v1/people`, `/v1/people/lifecycle`, `GET /v1/timeoff/requests`,
  `GET /v1/payroll/history`, `/v1/company/named-lists`; offset paging.
- **Real HiBob API** ([apidocs.hibob.com](https://apidocs.hibob.com/)): base `/v1`, but:
  the directory is **`POST /v1/people/search`** (returns ALL, no pagination — not a `GET`),
  time-off is **`GET /v1/timeoff/requests/changes`** (bare array, `since`/`to` window ≤6
  months by change date — not `/timeoff/requests`), and salaries are
  **`GET /v1/bulk/people/salaries`** (cursor `{results, response_metadata:{next_cursor}}` —
  not `/payroll/history`). Auth is a service-user **HTTP Basic `base64(service_user_id:token)`**.
  Webhook is `Bob-Signature` = base64(HMAC-**SHA512**). Several Fyralis paths return 404 and
  the directory verb is wrong (GET vs POST search).
- **Fix:** `POST /v1/people/search`, `/v1/timeoff/requests/changes` with date window,
  `/v1/bulk/people/salaries` cursor; Basic auth.

---

## 4. Impact

1. **These nine sources ingest nothing from production.** It's a hard failure (wrong base
   paths / non-existent endpoints / wrong auth), not partial data. Any tenant connecting
   Brex/Deel/Ramp/Gusto/Carta/LinkedIn/Figma/Fireflies/HiBob gets 0 observations.
2. **Downstream model layer is starved.** With these finance/HR/equity/design/recruiting
   sources dark, the cross-source org graph (people→comp/equity, vendor spend, hiring,
   design activity) never fills in. In testing, single-source model-layer runs produced
   correct-but-**under-scoped** models ("the external sender", "commitment not identified")
   precisely because the graph was sparse.
3. **CI did not catch it** because each placeholder client is only ever exercised against
   its **own** in-repo synthetic mock, which was built with the **same wrong shapes** — so
   the client and its test agree with each other and disagree with reality. (This is the
   core testing gap: the connector and its fixture share an author and an assumption.)

---

## 5. Recommended fixes

**Per source:** rebuild each client against the real contract in §3 — correct base + paths,
auth flow (OAuth client-credentials for ramp/carta; `POST /oauth/token` for gusto; Basic for
hibob; GraphQL for fireflies; `X-Version`/`Linkedin-Version` headers for deel/linkedin),
pagination (keyset/cursor/offset/AIP-token), and money/date parsing (cents-int vs
decimal-string vs protobuf-wrapper vs number; date-only vs epoch-millis vs RFC3339).

**Process fixes (so this can't recur):**
- **Don't clone archetypes blind.** A "banking-shaped" or "query-shaped" guess is not a
  spec. Each connector must be written from the provider's official OpenAPI/GraphQL schema.
- **Test connectors against a real-API-faithful fixture, not a self-authored mock.** A
  fixture written from the same wrong assumption as the client validates nothing. Either
  validate payloads against the provider's published OpenAPI/spec, or test against
  fixtures authored independently from the provider docs (which is exactly what surfaced
  these — pointing the clients at independently-built faithful mocks 404'd them immediately).
- **Add a smoke test per connector** that asserts the **first real request path + auth +
  pagination key** match the provider's documented contract (path-shape assertions catch
  `/v3/company/{id}/query` vs `/developer/v1/transactions` instantly).

## 6. Severity / priority order
1. **ramp, gusto, carta, linkedin** — *entirely wrong* (QBO SQL API that doesn't exist;
   wrong auth, paths, pagination, money). Full rewrites.
2. **brex, deel** — wrong base/paths/money (Mercury clone). Full rewrites, smaller surface.
3. **fireflies, figma** — wrong protocol/endpoints (GraphQL; non-existent file-list/events).
4. **hibob** — closest; fix verbs/paths/pagination/auth (smallest delta).

---
*Generated from: Fyralis client code (`services/ingest/integrations/<source>/client.py`),
empirical runs against a real-API-faithful mock suite, and the providers' official docs
(linked inline). Each "real API" claim above is corroborated by an official-doc citation.*
