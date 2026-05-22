# saas-api-mocks

Wire-compatible, self-hosted replicas of **Slack / Discord / GitHub / Gmail**.

Point any data-ingestion product at them via environment variables and they look
exactly like the real services — same endpoints, same headers, same rate limits,
same error shapes, same webhook signatures. A single deterministic generator
("OrgGen") builds a synthetic company's history; each mock projects its slice
through wire-accurate REST APIs and signed webhooks.

Use it to develop and test an ingestion pipeline against a controllable,
reproducible fake world instead of live third-party APIs.

> The installed Python package is `spammers/` (plural), which differs from the
> repo name. This is the same convention as `requests` / `Pillow` and is intentional.

---

## Requirements

- **Python 3.11+**
- **PostgreSQL** reachable on `localhost:5432` (any recent version). The mocks
  use their own database, separate from your consumer's. The setup script
  creates the databases it needs.
- *(optional)* **Docker** — if your Postgres runs in a container, the setup
  script will start it for you.

---

## Quickstart

Everything is driven by `./dev.sh`. The only thing you run by hand is `setup`,
once — it builds the virtualenv, installs dependencies, verifies Postgres, and
caches the database connection in `.env` so you never have to export anything.

```bash
git clone <this-repo> && cd saas-api-mocks

./dev.sh setup        # venv + deps + database (run once)
./dev.sh test         # run the test suite (optional sanity check)
./dev.sh prepare      # generate a synthetic org + message history
./dev.sh serve        # start the Slack mock on http://localhost:7001
```

Check it's alive:

```bash
curl -s http://localhost:7001/_health      # -> {"ok":true,"service":"slack-mock"}
```

**Database credentials.** `./dev.sh setup` auto-detects them (it tries
`postgres:postgres` first). If your Postgres uses different credentials:

```bash
SPAMMERS_DB_CREDS=myuser:mypassword ./dev.sh setup
```

The resolved connection string is written to `.env` and reused by every command.

---

## `./dev.sh` commands

| Command | What it does |
|---|---|
| `./dev.sh setup` | Build everything: venv, dependencies, database. Run once. |
| `./dev.sh test [pytest args]` | Run the fidelity test suite (spins up its own throwaway test DB). |
| `./dev.sh prepare` | Create a run and generate the synthetic org + historical timeline. |
| `./dev.sh serve` | Start the Slack mock (port `$PORT`, default 7001). |
| `./dev.sh stop` | Stop the mock / free the port. |
| `./dev.sh token` | Print a bot token you can use to call the mock. |
| `./dev.sh status` | Show the current run, virtual clock, and counts. |
| `./dev.sh inject --text=… --channel=#general` | Queue one live message. |
| `./dev.sh emit --speed=1.0 --live-rate=10` | Advance virtual time and fire live webhooks. |
| `./dev.sh reset --confirm=yes` | Drop all mock data and start clean. |
| `./dev.sh install --provider=slack …` | Walk the OAuth install into your consumer. |

Overrides (inline env vars): `SIZE`, `RUNTIME`, `SEED`, `PORT`, `SPAMMERS_DB_CREDS`.
Example: `SEED=7 ./dev.sh prepare`, or `PORT=7002 ./dev.sh serve`.

---

## Providers

| Provider | Status |
|---|---|
| **Slack** | ✅ Implemented — OAuth, Web API, signed Events webhooks, tiered rate limits. |
| **GitHub** | ✅ Implemented — App install + JWT, installation tokens, REST reads, signed webhooks, fidelity-audited. |
| **Discord** | 🚧 Planned — REST + interactions + Gateway WebSocket. |
| **Gmail** | 🚧 Planned — domain-wide delegation, REST, Pub/Sub push. |

### Slack

After `./dev.sh prepare`, start the mock and talk to it exactly as you would
real Slack.

```bash
./dev.sh serve                       # http://localhost:7001
TOKEN=$(./dev.sh token)              # a bot token from the seeded workspace
```

**Read data** (the API a consumer's bot would call):

```bash
# list channels
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:7001/api/conversations.list?limit=50" | python3 -m json.tool

# read a channel's history (use a channel id from the call above)
curl -s -H "Authorization: Bearer $TOKEN" \
  "http://localhost:7001/api/conversations.history?channel=C…&limit=15" | python3 -m json.tool
```

Implemented endpoints: `auth.test`, `team.info`, `users.info`, `users.list`,
`conversations.{info,list,history,replies,members}`,
`chat.{postMessage,update,delete}`, plus `oauth/v2/authorize` and
`oauth.v2.access`. Each returns Slack's real response shape, error strings,
pagination cursors, and tiered `429` + `Retry-After` rate limits.

**Send live events** (signed Slack Events delivered to your consumer's webhook):

```bash
# one-off message
./dev.sh inject --text="rolling out the new pipeline" --channel="#general"

# continuous: advance the clock and generate ~10 msgs/min, signed and POSTed
./dev.sh emit --speed=1.0 --live-rate=10
```

**Install into your consumer via OAuth** (drives the full authorize → callback →
token-exchange flow):

```bash
FYRALIS_API_TOKEN=<your consumer's bearer> \
  ./dev.sh install --provider=slack --slack-mock-base=http://localhost:7001
```

**Stop it:**

```bash
./dev.sh stop          # or Ctrl-C if running in the foreground
```

### GitHub

After `./dev.sh prepare`, start the mock and call it exactly as you would real
GitHub. Authentication is two-legged, just like the real App API: sign a short
App JWT with the App's private key, exchange it for an installation token, then
use that for REST calls.

```bash
./dev.sh serve github                # http://localhost:7003
```

```bash
# 1. App JWT (RS256, iss = app id) → installation token (ghs_…)
#    (the App's private key lives in app_github.apps.private_key)
# 2. REST reads with the installation token:
curl -s -H "Authorization: Bearer ghs_…" \
  "http://localhost:7003/installation/repositories" | python3 -m json.tool

curl -s -H "Authorization: Bearer ghs_…" \
  "http://localhost:7003/repos/acme/core/pulls?state=all" | python3 -m json.tool
```

Implemented surface: `/apps/{slug}/installations/new` (install page), `/app`,
`/app/installations[/{id}]`, `POST /app/installations/{id}/access_tokens`
(mints `ghs_…`), `/installation/repositories`, `/repos/{owner}/{repo}`, and the
content reads `/pulls`, `/issues`, `/commits`, `/pulls/{n}/reviews`,
`/issues/{n}/comments`, `/commits/{ref}/check-runs`. Every response carries
GitHub's real headers — `ETag` (with `If-None-Match` → `304`),
`X-RateLimit-*` (5000/hr fixed window), `X-GitHub-Media-Type`,
`X-GitHub-Request-Id` — and objects/error shapes match real GitHub.

**Send live events** (signed webhooks to your consumer's `/webhooks/github`):

```bash
./dev.sh inject --provider=github --kind=pull_request   # or --kind=issues
./dev.sh emit                                           # drains + POSTs signed events
```

Webhooks are signed with `X-Hub-Signature-256` (HMAC-SHA256) and carry
`X-GitHub-Event` / `X-GitHub-Delivery` / `X-GitHub-Hook-Installation-Target-*`.

The mock serves the install page at `/apps/{slug}/installations/new` (auto-approves
and redirects to your consumer's callback with `installation_id`). The automated
Director `install` walk is currently Slack-only.

### Discord / Gmail

Not implemented yet. The database schema and shared infrastructure (signing,
rate limiting, pagination, webhook delivery, OrgGen) already support them; each
needs its API surface built out, after which it gets a `serve` target and a
section here.

---

## Pointing your consumer at the mock

The mocks are additive: your consumer keeps its real-service URLs by default and
only redirects when you set the override env vars. For Slack:

```bash
# Slack
export SLACK_API_BASE_URL=http://localhost:7001/api
export SLACK_OAUTH_BASE_URL=http://localhost:7001

# GitHub
export GITHUB_API_BASE_URL=http://localhost:7003
export GITHUB_APP_INSTALL_BASE_URL=http://localhost:7003
```

Restart your consumer so the new environment takes effect. For Slack, run
`./dev.sh install --provider=slack` to complete the OAuth handshake; for GitHub,
point your consumer's install flow at the mock's install page.

---

## How "historical" vs "live" works

- **Historical** messages (everything before the run's virtual "now") are
  generated by `prepare` and stored so they're discoverable **only** through
  pull APIs (`conversations.history`, `conversations.replies`). No webhooks fire
  — matching how a fresh install gets no backlog push.
- **Live** messages (`inject` / `emit`) are delivered as **signed webhooks** to
  your consumer *and* projected back into the mock so later pulls return them too.

`emit` advances a virtual clock; events become "live" as the clock passes them.

---

## Determinism

The same `(size, runtime, seed)` produces a byte-identical org and timeline, so
test runs are reproducible. Control the knobs via `prepare`:

```bash
SIZE=medium RUNTIME=one_year SEED=123 ./dev.sh prepare
```

---

## Testing

```bash
./dev.sh test               # whole suite
./dev.sh test -k oauth      # filter (any pytest args pass through)
./dev.sh test spammers/tests/fidelity
```

The suite asserts the mock matches **real Slack's documented behavior** —
request/response shapes, headers, signing, pagination, rate-limit tiers, error
codes, and the historical-vs-live flow. It creates and tears down its own
temporary database (`SPAMMERS_TEST_DB_URL`), independent of your `prepare` data.

---

## Configuration

| Variable | Purpose |
|---|---|
| `SPAMMERS_DB_URL` | Connection string for the mock database. Written to `.env` by `setup`. |
| `SPAMMERS_TEST_DB_URL` | Connection string for the test database. Written to `.env` by `setup`. |
| `SPAMMERS_DB_CREDS` | `user:password` for `setup` to auto-build the DSNs. |
| `PORT` | Port for `serve` / `stop` (default 7001). |
| `SIZE` / `RUNTIME` / `SEED` | `prepare` knobs (defaults: `small` / `few_months` / `42`). |

---

## Layout

```
saas-api-mocks/
├── dev.sh                        # setup + task runner (start here)
├── README.md
├── pyproject.toml
└── spammers/                     # the installed package
    ├── common/                   # signing, rate_limit, clock, db, errors, webhook_emitter, pagination, ids
    ├── orggen/                   # synthetic org + timeline generator
    ├── director/                 # CLI: prepare / install / emit / inject / jump / status / reset
    ├── slack/                    # Slack mock: routes/, app, events, state, auth, ratelimit
    ├── github/                   # GitHub mock: routes/, app, auth, jwt_verify, webhooks, dto, ratelimit
    ├── discord/ · gmail/         # planned
    ├── db/migrations/            # schema (all four providers)
    └── tests/                    # contract / behavior / fidelity suites
```

For the full design, see [ARCHITECTURE.md](ARCHITECTURE.md).
