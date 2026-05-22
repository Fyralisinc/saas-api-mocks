# saas-api-mocks — Architecture & Design

Self-hosted, drop-in replicas of **Slack**, **Discord**, **GitHub**, and **Gmail**
that a data-ingestion product can point at without changing its ingestion code.
A single **Director** compiles a deterministic, organization-wide narrative; each
**mock** projects its slice through wire-accurate REST APIs and signed webhook
deliveries.

**Design contract:** *if a feature works against the spammers, it works against
the real services.*

> **"The consumer"** in this document means the data-ingestion product you point
> at the mocks (the thing that calls Slack's API, receives its webhooks, etc.).
> Example environment-variable names below sometimes use the historical prefix
> `FYRALIS_` — substitute whatever your consumer expects.

> **Status:** Slack is fully implemented. GitHub / Discord / Gmail are designed
> here but not yet built — their sections describe intended behavior, and the
> shared infrastructure (schema, signing, rate limiting, pagination, webhook
> delivery, OrgGen) already supports them.

---

## 1. Goals & non-goals

### Goals

- **Wire fidelity**: every endpoint the consumer touches matches the real
  provider on URL, method, headers, status codes, rate-limit headers, error
  JSON, pagination, and signature schemes.
- **Behavioral fidelity**: rate limits, retries, idempotency keys, replay
  caches, OAuth collision, the uninstall chokepoint, token-revoked 401s — all
  reproduce the same consumer-side observable behavior.
- **Correlated content**: the mocks expose one organization. Events
  cross-reference each other (a PR description mentions a Slack thread; a Slack
  message links to a GitHub issue; a weekly digest email aggregates the week's PRs).
- **Configurable scale**: `size ∈ {small, medium, large}` × `runtime ∈ {few_months, one_year, few_years}`.
- **Deterministic**: same `(size, runtime, seed)` produces the same timeline,
  byte-for-byte, every run.
- **Self-contained**: no production-API credentials, no internet access; OAuth
  flows complete inside the mocks.
- **Simple operation**: one script (`./dev.sh`) builds everything and drives the
  Director.

### Non-goals (v1)

- Human-facing UIs that mimic the real client surfaces. (Operators use the
  Director CLI.)
- Outbound mutation surfaces the consumer doesn't use (e.g. GitHub PR create,
  Gmail send).
- Multiple synthetic orgs per Director run. (Run multiple runs for that.)
- Compressed historical webhook replay during bootstrap. (Pull APIs expose
  history; webhooks only fire from virtual-now forward.)

---

## 2. Design decisions (locked)

| # | Decision | Choice |
|---|---|---|
| D1 | Consumer ↔ mock URL discovery | **Env-var base URL per provider**; defaults to the real URL |
| D2 | UI surface | **API-only** (no fake client UIs) |
| D3 | Event-content generation | **Jinja templates + slot fills**, deterministic via seed |
| D4 | Mock state storage | **Single shared Postgres** for all mocks + Director |
| D5 | Historical events at bootstrap | **Pull-API only**; no historical webhook replay |
| D6 | OAuth install | **Director walks the OAuth flow** into the consumer |
| D7 | Tenancy | **One synthetic org per run** |
| D8 | Packaging | **Standalone repo**; package name `spammers` (plural), no runtime coupling to any consumer |

---

## 3. Component map

```
                  ┌──────────────────────────────────────────────┐
                  │              Director (control plane)         │
                  │   • CLI via ./dev.sh / `spammer …`            │
                  │   • OrgGen → timeline.events                  │
                  │   • Walks OAuth install into the consumer     │
                  │   • Schedules + signs webhook emission        │
                  └──────────────────────────────────────────────┘
                                  │ asyncpg
                  ┌──────────────────────────────────────────────┐
                  │   Mock-Postgres (separate DB from consumer)   │
                  │   org.{runs,people,teams,projects}            │
                  │   timeline.events  (typed, cross-ref'd)       │
                  │   app_slack.{workspaces,channels,users,msgs}  │
                  │   app_discord.* · app_github.* · app_gmail.*  │
                  │   oauth.{installs,codes,states}               │
                  └──────────────────────────────────────────────┘
   ┌─────────────┬───────────────┬───────────────┬───────────────┐
slack-mock   discord-mock     github-mock     gmail-mock
:7001        :7002 (+WS)      :7003           :7004
(implemented)  (planned)      (implemented)    (planned)
   │
   └──── signed webhooks ────►  consumer  /webhooks/{provider}
```

---

## 4. Wire-compatibility strategy

### 4.1 Outbound (consumer → mock): env-var override

The consumer reads one base-URL env var per provider, defaulting to the real
public URL. Setting the env var redirects outbound traffic to the mock — no
other code change. Pattern: `BASE = os.environ.get("X", "<real_default>")`, so
production behavior is unchanged when unset.

| Provider | Env var | Default (production) |
|---|---|---|
| Slack — Web API | `SLACK_API_BASE_URL` | `https://slack.com/api` |
| Slack — OAuth | `SLACK_OAUTH_BASE_URL` | `https://slack.com` |
| Discord — REST | `DISCORD_API_BASE_URL` | `https://discord.com/api/v10` |
| Discord — OAuth authorize | `DISCORD_AUTHORIZE_BASE_URL` | `https://discord.com` |
| Discord — Gateway WS | `DISCORD_GATEWAY_WS_URL` | `wss://gateway.discord.gg` |
| GitHub — REST + App API | `GITHUB_API_BASE_URL` | `https://api.github.com` |
| GitHub — App install | `GITHUB_APP_INSTALL_BASE_URL` | `https://github.com` |
| Gmail — REST | `GMAIL_API_BASE_URL` | `https://gmail.googleapis.com/gmail/v1` |
| Directory — REST | `DIRECTORY_API_BASE_URL` | `https://admin.googleapis.com/admin/directory/v1` |
| Google — OAuth token | `GOOGLE_OAUTH_TOKEN_URL` | `https://oauth2.googleapis.com/token` |
| Google — OIDC JWKS | `GOOGLE_OIDC_JWKS_URL` | `https://www.googleapis.com/oauth2/v3/certs` |

### 4.2 Inbound (mock → consumer): same secrets, same endpoints

Each mock signs deliveries with the same keys the consumer's verifier loads, and
POSTs to the consumer's existing `/webhooks/{provider}` routes. The mock and
consumer share secrets at install time (the mock seeds the value the consumer
will verify against).

| Provider | Signature scheme | Header(s) |
|---|---|---|
| Slack | HMAC-SHA256 over `v0:{ts}:{body}` | `X-Slack-Signature`, `X-Slack-Request-Timestamp` |
| Discord (interactions) | Ed25519 over `{ts}{body}` | `X-Signature-Ed25519`, `X-Signature-Timestamp` |
| Discord (gateway) | none (auth via `IDENTIFY.token`) | n/a |
| GitHub | HMAC-SHA256 | `X-Hub-Signature-256`, `X-GitHub-Event`, `X-GitHub-Delivery`, `X-GitHub-Hook-Installation-Target-{Type,ID}` |
| Gmail Pub/Sub | OIDC JWT (RS256) | `Authorization: Bearer <jwt>` |

### 4.3 OAuth credentials lifecycle

Each mock's OAuth surface issues credentials that match the format/length/
character-class of real ones (Slack `xoxb-…`, GitHub `ghs_…`, Google opaque
tokens). The consumer stores them exactly as in production. The mock validates
them on every API call and can produce the documented revocation response shape
(401 for Slack/Discord/Gmail; 401 `Bad credentials` for GitHub) on demand — used
to exercise the consumer's uninstall chokepoint.

---

## 5. Per-mock fidelity inventory

### 5.1 slack-mock (:7001) — implemented

**Install flow**
- `GET /oauth/v2/authorize?client_id=&scope=&state=&redirect_uri=` — auto-approve
  page that redirects to the consumer's callback with `code` + `state`.
- `POST /api/oauth.v2.access` — exchanges `code` for a bot token (`xoxb-…`) and
  returns workspace + team metadata in the real `oauth.v2.access` shape.

**Web API** (POST or GET, as Slack accepts)
- `chat.postMessage`, `chat.update`, `chat.delete`
- `users.info`, `users.list`
- `conversations.info`, `conversations.list`, `conversations.history`,
  `conversations.replies`, `conversations.members`
- `team.info`, `auth.test`

All return Slack's real response shape (`ok` boolean, `error` string,
`response_metadata.next_cursor` opaque cursors), with `Content-Type:
application/json; charset=utf-8`.

**Outbound webhooks (Events API)**
The mock signs with the per-workspace signing secret and POSTs the consumer's
`/webhooks/slack`:
- `event_callback` envelope wrapping `message` events
- `url_verification` challenge (fired once on events-URL registration)
- `app_uninstalled` lifecycle event

Live messages are also projected back into `app_slack.messages` so later pulls
return them too.

**Rate limits** (token bucket; `429 {"ok":false,"error":"ratelimited"}` +
integer `Retry-After`):

| Method(s) | Tier | Sustained |
|---|---|---|
| `chat.postMessage` | special | ~1/sec per channel |
| `users.list`, `conversations.list` | Tier 2 | ~20/min |
| `team.info`, `conversations.info` | Tier 3 | ~50/min |
| `users.info`, `conversations.members`, `auth.test` | Tier 4 | ~100/min |
| `conversations.history`, `conversations.replies` | **Tier 1** | **1/min, max 15 objects/page** |

> The Tier-1 limit on history/replies reflects Slack's May-2025 change for
> non-Marketplace apps. It forces consumers to paginate and back off correctly —
> exactly the behavior a fidelity harness should stress. Switch these to Tier 3
> (`~50/min`, `limit` up to 1000) in `spammers/common/rate_limit.py` +
> `spammers/slack/routes/conversations.py` if you emulate a Marketplace app.

**Error catalog**: `invalid_auth`, `channel_not_found`, `user_not_found`,
`message_not_found`, `ratelimited`, `invalid_client_id`, `invalid_code`.

### 5.2 discord-mock (:7002 HTTP + WS) — implemented

**Install**: `GET /oauth2/authorize` (auto-approves, redirects with `code` +
`guild_id`) and `POST /api/v10/oauth2/token` (records `oauth.installs`).
**REST** (`Authorization: Bot <token>`): `users/@me` & `users/{id}`,
`guilds/{id}` (+ `/channels`, `/members/{user}`), `channels/{id}` (+ `/messages`
read with `before`/`after`/`around` snowflake paging, plus create/edit/delete),
global + guild application-command registration, and interaction
callback/followup endpoints. Real Discord object shapes; `Content-Type:
application/json; charset=utf-8`; `{code,message}` error bodies.
**Gateway (WebSocket, `/gateway`)**: full opcode handshake — HELLO(10),
IDENTIFY(2) (token + intents validated; closes 4004/4013/4014), READY(0,s=1) with
`resume_gateway_url` + unavailable guilds, then GUILD_CREATE(0) per guild;
HEARTBEAT(1)/ACK(11) with a heartbeat-timeout monitor (4009); MESSAGE_CREATE(0)
fan-out; RESUME(6) replays a per-session ring buffer with original seq numbers
then RESUMED, INVALID_SESSION(9) when unresumable; close codes 4001/4002/4003.
**Intents gating**: no `GUILD_MESSAGES` → no MESSAGE_CREATE; `GUILD_MESSAGES`
without `MESSAGE_CONTENT` → content/embeds/attachments stripped. **No historical
replay**: a bot connecting after the clock advanced receives only new messages.
Live dispatch is owned by the mock process (an in-process `GatewayDispatcher`
drains `discord.message` events and pushes to connected bots), since the Gateway
sockets aren't reachable from the Director.
**Interactions webhook**: live `discord.interaction` events are Ed25519-signed
(`X-Signature-Ed25519` / `X-Signature-Timestamp` over `ts+body`) and POSTed to
the consumer by the Director (ping / command / component).
**Rate limits**: per-route token buckets with `X-RateLimit-*` headers; `429
{"message":"You are being rate limited.","retry_after":…,"global":false}` +
`Retry-After`. (Per-route only; a strict global 50/sec is not yet modeled.)

### 5.3 github-mock (:7003) — implemented

**Install**: `GET /apps/{slug}/installations/new` — auto-approves and redirects to
the consumer's callback with `installation_id` + `setup_action`.
**App API** (validates the inbound App-JWT, RS256, against the App's public key —
`algorithms=["RS256"]` blocks the alg-confusion forgery): `GET /app`,
`/app/installations[/{id}]`, and `POST /app/installations/{id}/access_tokens`
which mints a `ghs_…` installation token (201) recorded in `oauth.installs`.
**REST** (`Authorization: Bearer ghs_…` or `token ghs_…`): `installation/repositories`
and `repos/{owner}/{repo}`, plus repo content — `pulls` (+`/reviews`), `issues`
(+`/comments`, and **PRs appear in the issues list** with a `pull_request` key,
like real GitHub), `commits` (+`/check-runs`), list & get. `per_page`/`page` with
`Link` headers.
**Standard headers on every response**: `ETag` (with `If-None-Match` → **304**
that does **not** count against the rate limit), `X-GitHub-Media-Type`,
`X-GitHub-Api-Version`, `X-GitHub-Request-Id`.
**Webhooks** (HMAC-SHA256, `X-Hub-Signature-256`, plus `X-GitHub-Event` /
`X-GitHub-Delivery` / `X-GitHub-Hook-Installation-Target-*`): `pull_request`,
`issues`, `pull_request_review`, `issue_comment`, `check_run`. Historical events
are pull-only; live events (`inject --provider=github`) are signed + POSTed and
projected for subsequent reads.
**Rate limits**: 5000/hr per installation as a fixed hourly window —
`X-RateLimit-Limit/Remaining/Used/Reset/Resource`, **403** with the documented
body on exhaustion. (Secondary "abuse" limits + `Retry-After` and
`Last-Modified`/`If-Modified-Since` are not yet modeled.)

### 5.4 gmail-mock (:7004) — planned

**OAuth (domain-wide delegation)**: `POST /token` consumes a service-account JWT
(RS256), returns a bearer token.
**Gmail API**: `users/me/watch`, `users/me/stop`, `users/me/history` (paginated),
`users/me/messages/{id}`, `users/me/threads/{id}`, `users/me/profile`.
**Directory API**: `users`, `groups`, `orgunits` (paginated).
**Pub/Sub push**: mock holds an OIDC keypair, publishes a JWKS at `/jwks`, and
POSTs OIDC-JWT-signed push notifications to the consumer.
**Rate limits**: 250 quota units/sec/user; `429` / `403` with
`rateLimitExceeded` / `quotaExceeded` reasons.

---

## 6. OrgGen — content authoring

### 6.1 Pipeline

```
(size, runtime, seed)
  → 1. Skeleton   personas; hiring trajectory across the runtime
  → 2. Topology   teams, reporting chain, on-call rotations
  → 3. Projects   project graph linking repos + Slack/Discord channels + email threads
  → 4. Timeline   typed events respecting business hours/TZ, weekly rhythms,
                  sprint cadence, release cycles, incidents, holidays
  → 5. Cross-ref  weave references between apps (PR ↔ Slack thread ↔ issue ↔ digest)
  → 6. Persist    write timeline.events + per-app projection tables
```

### 6.2 Profile dial (tunable)

| size × runtime | People | Teams | Repos | Slack ch. | Daily events |
|---|---:|---:|---:|---:|---:|
| small × few_months | 8 | 2 | 3 | 6 | ~60 |
| small × one_year | 12 | 3 | 5 | 8 | ~80 |
| medium × one_year | 100 | 8 | 20 | 30 | ~900 |
| large × few_years | 2000 | 80 | 350 | 350 | ~15000 |

Daily-event total is summed across all providers (~Slack 50% / Gmail 25% /
GitHub 15% / Discord 10%) with daily/weekly variance.

### 6.3 Personas & templates

- **Personas**: role × level, with a handle, timezone, voice signature
  (terse/verbose/formal/casual), online windows, and relationship/domain anchors.
  Archetypes live in `spammers/orggen/archetypes/` (v1 ships `early_saas`).
- **Templates**: per event type (`slack.message.standup`, `…banter`,
  `github.pr.description`, `gmail.thread.weekly_digest`, …), a Jinja template with
  slot fills drawn from the persona's voice + domain vocab + cross-ref tokens.
  Live in `spammers/orggen/templates/`.

### 6.4 Determinism guarantee

- A single seeded RNG facade (`spammers/orggen/seed.py::RunRandom`) is threaded
  through the pipeline; no global `random` / `time.time()` calls elsewhere.
- All time math in UTC; persona timezone applied at render only.
- Message timestamps are made unique per channel (Slack `ts` is the per-channel
  message id), so projection is collision-free.
- Re-running with the same `(size, runtime, seed)` reproduces the timeline.

---

## 7. Time model

### 7.1 Virtual clock

- One **virtual now** per run, stored in `org.runs.virtual_now`.
- Each mock reads virtual time via `spammers.common.clock`.
- API responses (Slack `ts`, etc.) and webhook scheduling use virtual time;
  deliveries fire at wall-clock per the speed multiplier.

### 7.2 Modes (`org.runs.mode`)

| Mode | Behavior | Use case |
|---|---|---|
| `frozen` | Virtual now pinned; no webhooks; pull APIs return data ≤ now | Bootstrap / snapshot testing |
| `live` | Virtual now advances at (wall × `speed_multiplier`); webhooks fire as the clock passes events | Demo / dogfood / accelerated testing |
| `step` | Director advances the clock manually (`jump`) | Step-debugging |

### 7.3 Bootstrap → live handoff

1. `prepare` sets `virtual_now = now`, mode `frozen`.
2. OrgGen generates the timeline backwards from `virtual_now` by `runtime`
   (historical events get `virtual_ts < virtual_now`).
3. Historical events are exposed via pull APIs only — no webhooks.
4. Walk the OAuth install into the consumer so it stores tokens.
5. `emit` switches to `live`; events with `virtual_ts ≥ virtual_now` fire as
   signed webhooks as the clock passes them.

This matches reality: a freshly-installed app gets no backlog push.

---

## 8. Director (control plane)

### 8.1 Commands

The Director is driven through `./dev.sh` (which loads the DB connection and
wraps the `spammer` CLI). Equivalent raw CLI in parentheses.

```bash
./dev.sh prepare            # (spammer prepare --size --runtime --seed --tenant-id --fyralis-base)
                            #   apply migrations, create a run, OrgGen the historical timeline
./dev.sh serve              # (python -m spammers.slack run --port 7001)  start the Slack mock
./dev.sh install --provider=slack --slack-mock-base=http://localhost:7001
                            #   walk OAuth into the consumer ($FYRALIS_API_TOKEN)
./dev.sh emit --speed=1.0 --live-rate=10
                            #   enter live mode, advance the clock, generate + fire webhooks
./dev.sh inject --text=… --channel=#general     # queue one live message
./dev.sh jump  --by=1d   |  --to=2026-06-01T09:00:00Z   # advance virtual time
./dev.sh status                                 # run + clock + counts
./dev.sh reset --confirm=yes                    # drop all mock schemas
```

Knobs: `SIZE`, `RUNTIME`, `SEED`, `PORT` (env-var overrides on `./dev.sh`).

> An HTTP control surface and `/metrics` endpoint on a dedicated Director port
> are designed but not yet implemented; today the CLI is the control plane.

### 8.2 OAuth install sequence

```
1. Director → consumer:  GET /integrations/{provider}/install   (Bearer-authed)
2. consumer → Director:  302 to <mock>/oauth/authorize?...&state=...
3. Director → mock:      "approve" → mock issues code + redirects to the consumer callback
4. Director → consumer:  GET /integrations/{provider}/callback?code=&state=
5. consumer → mock:      POST /api/oauth.v2.access (or provider equivalent)
6. mock → consumer:      access_token + team/guild/installation metadata
7. consumer persists:    its install row + token in its own secret store
```

---

## 9. Mock-Postgres schema

A single database (default `mock_orgs`), separate from the consumer's, with one
schema per concern. Everything is scoped to a `run_id` (one synthetic org per
run).

| Schema | Purpose |
|---|---|
| `org` | `runs` (the run + virtual clock), `people`, `teams`, `projects` — the synthetic organization |
| `timeline` | `events` — the single source of narrative truth (typed, timestamped, cross-referenced); `is_historical` + `emitted_at` drive the pull-vs-webhook split |
| `app_slack` | `workspaces`, `channels`, `users`, `channel_membership`, `messages` — projection state for Slack API responses |
| `app_discord` · `app_github` · `app_gmail` | Per-provider projection tables (schema present; mocks pending) |
| `oauth` | `installs`, `codes`, `states` — OAuth install/token state |

The **authoritative schema is [`spammers/db/migrations/0001_init.sql`](spammers/db/migrations/0001_init.sql)** —
refer to it rather than duplicating DDL here. Key relationships: `timeline.events`
references `org.people`/`org.projects`; each `app_*` projection row links back to
its `timeline.events` row via `timeline_event_id`; `app_slack.messages` is unique
on `(channel_pk, ts)`.

---

## 10. Repository layout

The installed package is `spammers/` (plural), which differs from the repo name —
intentional, same convention as `requests` / `Pillow`. Directories marked
*planned* arrive with later providers.

```
saas-api-mocks/                   # repo root
├── dev.sh                        # setup + task runner (start here)
├── README.md
├── ARCHITECTURE.md               ← this file
├── pyproject.toml
└── spammers/                     # installed package
    ├── common/                   # rate_limit, signing, pagination, clock,
    │                             #   webhook_emitter, errors, ids, db
    ├── director/                 # cli, installer, orchestrator, runs
    ├── orggen/                   # seed, profiles, personas, projects, render,
    │                             #   timeline, compile, live, templates/, archetypes/
    ├── slack/                    # implemented mock
    │   ├── app.py, auth.py, ratelimit.py, state.py, events.py, responses.py
    │   └── routes/               # oauth, chat, users, conversations, team, auth_test
    ├── github/                   # implemented mock
    │   ├── app.py, auth.py, jwt_verify.py, ratelimit.py, state.py, webhooks.py, dto.py, responses.py
    │   └── routes/               # install, app_api, installation, repos, repo_content
    ├── discord/ · gmail/         # planned
    ├── db/migrations/            # 0001_init.sql (all four providers' schema)
    └── tests/
        ├── conftest.py           # deterministic DB fixture + ASGI client
        ├── contract/             # per-endpoint request/response/header/error shape
        ├── behavior/             # history-vs-live, threads, pagination, soft-delete,
        │                         #   rate-limit, the real prepare/compile flow
        └── fidelity/             # signing, golden envelopes, ID formats, rate tiers
```

---

## 11. Verification strategy

The test suite encodes **real Slack's documented behavior** as the baseline and
asserts the mock matches it — so "passes on the mock ⇒ behaves like the real
service." It is fully offline and deterministic (golden fixtures + a hand-built
seed dataset); no live account is required.

- **`tests/contract/`** — per-endpoint: HTTP status, `Content-Type`, the full
  response envelope field-by-field, and error shapes.
- **`tests/behavior/`** — stateful flows: historical pulls vs signed live
  delivery, threading, cursor pagination, soft-delete, the `429` + `Retry-After`
  shape, and the real `prepare`/`compile_run` path.
- **`tests/fidelity/`** — exact spec conformance: independent signature
  recomputation (`v0:{ts}:{body}` HMAC-SHA256), golden Events envelopes, ID/`ts`
  formats, and per-method rate-limit tiers.

Run with `./dev.sh test` (creates and tears down its own temporary database).

Both implemented providers carry this suite. The GitHub layer additionally
asserts the standard response headers, `ETag`/`If-None-Match` → `304` (and that a
304 doesn't consume quota), the fixed-window `X-RateLimit-*` values, the
issues-includes-PRs rule, and App-JWT forgery rejection. As the remaining
providers land, each gets the same layers, plus an OrgGen determinism layer
(same seed ⇒ byte-identical timeline).

---

## 12. Risks & mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Mock drift vs the real provider as APIs evolve | Fidelity tests encode documented behavior; refresh when the upstream spec moves. |
| R2 | Webhook emission overload at large scale (~15k events/day live) | Bounded emission queue; per-mock rate limits obeyed; configurable max emission rate. |
| R3 | OAuth secret leakage | Mock secrets are generated fresh per run, never from production. |
| R4 | Cross-app reference cycles (an email mentions a not-yet-created PR) | Two-pass build: emit skeletons with placeholder refs, then resolve once all events are placed. |
| R5 | Determinism breakage from non-pure RNG | Keep `random`/`time.time()` out of generation code; route through the seeded facade + virtual clock. |
| R6 | Postgres footprint for large × few_years (~5M timeline rows) | Partition `timeline.events` by month; bulk COPY during OrgGen; archive completed runs. |
| R7 | Discord slash-command ack deadlock | Enforce Discord's 3s ack timeout; emit a failure event if the consumer misses it. |
| R8 | Gmail OIDC key not trusted by the consumer | Mock publishes a JWKS the consumer points `GOOGLE_OIDC_JWKS_URL` at. |

---

## 13. Roadmap

| Slice | Scope | Status |
|---|---|---|
| Substrate | mock-Postgres schema, `spammers/common/*`, Director skeleton | ✅ done |
| OrgGen v1 | small × few_months end-to-end; Slack timeline; Jinja templates | ✅ done |
| slack-mock | OAuth + Web API + Events + tiered rate limits + install walk | ✅ done |
| Slack test suite | contract + behavior + fidelity layers | ✅ done |
| github-mock | App install + App-JWT validate + REST reads + signed webhooks + fidelity audit | ✅ done |
| discord-mock | OAuth + REST + interactions + Gateway WS (the hardest) | ⏳ next |
| gmail-mock | DwD `/token` + Gmail/Directory REST + Pub/Sub OIDC push | ⏳ planned |
| Cross-app refs | weave references across providers in OrgGen | ⏳ planned |
| Profiles fill-out | medium + large dials; load characterization | ⏳ planned |

---

## 14. Open questions

- **Noise dial** for chaos engineering (occasional 5xx, late/out-of-order
  deliveries) — useful for hardening consumer retry logic.
- **Sampled emission** for the largest profiles (fire webhooks for a fraction of
  events) to keep wall-clock emission manageable.
- **Operator replay** endpoint (`/debug/replay-last-webhook`) for debugging
  consumer-side handlers.

---

## 15. Glossary

- **Director** — control-plane process that orchestrates mocks + timeline + emission.
- **OrgGen** — the deterministic timeline generator.
- **Timeline event** — a typed, timestamped fact about the synthetic org (a Slack
  message, a PR open, an email reply…).
- **Mock** — one of the four wire-compatible services.
- **The consumer** — the data-ingestion product under test that points at the mocks.
- **Virtual time** — the clock the synthetic org runs on; controlled by the Director.
- **Wire fidelity** — bit-identical match on URL, headers, status, body shape vs
  the real provider.
- **Behavior fidelity** — identical consumer-side observable outcome (retry
  timing, chokepoint firing, etc.).
```
