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
| `./dev.sh serve [provider]` | Start a mock: `slack`:7001 `discord`:7002 `github`:7003 `gmail`:7004 `calendar`:7005 `notion`:7006 (`$PORT` overrides). |
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
| **Discord** | ✅ Implemented — OAuth, REST, command registration, Ed25519-signed interactions, full Gateway WebSocket, fidelity-audited. |
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

### Discord

After `./dev.sh prepare`, start the mock and talk to it exactly as you would real
Discord — both the HTTP API and the realtime Gateway. Authenticate REST calls
with `Authorization: Bot <token>`.

```bash
./dev.sh serve discord               # http://localhost:7002 (HTTP + WS)
```

**Read data** (the API a consumer's bot would call):

```bash
TOKEN=…  # app_discord.applications.bot_token for the run

# the bot's own user, a guild's channels, a channel's recent messages
curl -s -H "Authorization: Bot $TOKEN" \
  "http://localhost:7002/api/v10/users/@me" | python3 -m json.tool

curl -s -H "Authorization: Bot $TOKEN" \
  "http://localhost:7002/api/v10/channels/<channel_id>/messages?limit=50" | python3 -m json.tool
```

Implemented surface: `oauth2/authorize` + `oauth2/token`, `users/@me` &
`users/{id}`, `guilds/{id}` (+ `/channels`, `/members/{user}`), `channels/{id}`
and its messages (snowflake `before`/`after`/`around` paging, plus create / edit
/ delete), global + guild application-command registration, and interaction
callback/followup endpoints. Responses use Discord's real object shapes,
`Content-Type: application/json; charset=utf-8`, `{code, message}` error bodies,
and per-route `X-RateLimit-*` token buckets with the documented `429`
`{"message":"You are being rate limited.","retry_after":…,"global":false}`.

**Connect a bot to the Gateway** (`GET /api/v10/gateway` returns the WS URL):

```text
HELLO(10) → IDENTIFY(2) → READY(0) + GUILD_CREATE(0) per guild
HEARTBEAT(1) ⇄ HEARTBEAT_ACK(11);  RESUME(6) replays a per-session ring buffer
```

Intents are honored: without `GUILD_MESSAGES` the bot receives no
`MESSAGE_CREATE`; with it but without `MESSAGE_CONTENT`, message content is
stripped. A bot connecting after the clock advanced gets **no** historical flood
— only new messages, like real Discord.

**Send live events:**

```bash
# a live channel message — pushed as MESSAGE_CREATE to connected bots over the Gateway
./dev.sh inject --provider=discord --channel=general --text="rolling out the pipeline"

# a slash-command interaction — Ed25519-signed POST to your consumer's webhook
./dev.sh inject --provider=discord --kind=interaction

# advance the clock so the mock's dispatcher (Gateway) and the Director
# (signed interactions) deliver queued live events
./dev.sh emit
```

Live `discord.message` events are pushed over the Gateway by the mock itself;
live `discord.interaction` events are Ed25519-signed (`X-Signature-Ed25519` /
`X-Signature-Timestamp`) and POSTed to your consumer by the Director.

### Gmail

```bash
./dev.sh serve gmail                 # http://localhost:7004
```

Domain-wide delegation: the consumer POSTs its service-account JWT assertion to
`POST /token`; the mock mints an opaque `ya29.…` bearer (it doesn't hold the SA
key, so it decodes the assertion's `sub`/`scope` rather than verifying it).
`userId` "me" resolves to that subject. Surface: `users/{id}/messages` (list +
`/messages/{id}` with `format=full|metadata|minimal|raw`), `threads/{id}`,
`history` (`startHistoryId` drain), `watch`/`stop`, `profile`, and the Admin
Directory `users`/`groups`/`members`/`orgunits` (mailbox enumeration).

```bash
# point the consumer's overrides at the mock:
export GMAIL_API_BASE_URL=http://localhost:7004/gmail/v1
export DIRECTORY_API_BASE_URL=http://localhost:7004/admin/directory/v1
export GOOGLE_OAUTH_TOKEN_URL=http://localhost:7004/token
export GOOGLE_OIDC_JWKS_URL=http://localhost:7004/jwks
```

**Pub/Sub push (full OIDC):** the mock serves its OIDC public key at `/jwks` and
signs push envelopes with an RS256 JWT the consumer verifies against it — so the
live path works end-to-end. The envelope `data` is base64(`{emailAddress,
historyId}`); the drain then reads `history` from the bookmark.

### Google Calendar

```bash
./dev.sh serve calendar              # http://localhost:7005
export GOOGLE_CALENDAR_API_BASE_URL=http://localhost:7005/calendar/v3
export GOOGLE_OAUTH_TOKEN_URL=http://localhost:7005/token   # (shared with Gmail)
```

One calendar per person (`calendarId` = their email = `primary`).
`GET /calendar/v3/calendars/{calendarId}/events` serves all three sync modes —
full (`timeMin`/`singleEvents`/`orderBy=startTime` + `nextPageToken`, final page
yields `nextSyncToken`), incremental (`syncToken` + `showDeleted`), and the
`updatedMin` reconcile probe. An expired/`EXPIRED` `syncToken` returns **410
`fullSyncRequired`**. Poll-only (no watch in v1).

### Notion

```bash
./dev.sh serve notion                # http://localhost:7006
export NOTION_API_BASE_URL=http://localhost:7006
```

API version `2022-06-28`, integration bot token (`Authorization: Bearer …`).
Backfill tree-walk: `POST /v1/search` (filter `value=page|database`) →
`POST /v1/databases/{id}/query` → `GET /v1/blocks/{id}/children` →
`GET /v1/comments?block_id=`; webhook hydration via `GET /v1/pages/{id}`; bot
identity at `GET /v1/users/me`. Opaque cursor pagination
(`start_cursor`/`next_cursor`/`has_more`).

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

# Discord
export DISCORD_API_BASE_URL=http://localhost:7002/api/v10
export DISCORD_OAUTH_BASE_URL=http://localhost:7002
export DISCORD_GATEWAY_URL=ws://localhost:7002/gateway
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
    ├── discord/                  # Discord mock: routes/, gateway/ (WS), app, dto, interactions_out, ratelimit
    ├── gmail/                    # planned
    ├── db/migrations/            # schema (all four providers)
    └── tests/                    # contract / behavior / fidelity suites
```

For the full design, see [ARCHITECTURE.md](ARCHITECTURE.md).
