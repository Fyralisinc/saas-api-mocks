# Alpen Labs Model-Layer Ground Truth

Generated/updated: 2026-06-13

This is the reference document for the Alpen Labs simulation in `corpus/`.
Use it as the answer key when Fyralis ingests the spammer data and creates
company models, predictions, recommendations, retrieval context, and Think
outputs.

The important framing: this corpus is not meant to be clean synthetic data.
It should feel like a real startup. Some decisions happen in public channels,
some in founder DMs, some in vague calendar events, some in Notion after work
has already started, and some only become clear after Jira assignments and
Drive notes move. Fyralis should infer the living company state from the
evidence trail, not from one neat source.

## Current Corpus State

Source of truth files:

- `corpus/facts/facts.yaml` - company, people, repos, milestones, departures.
- `corpus/facts/voices.yaml` - work voice, concerns, reaction patterns.
- `corpus/facts/patterns.yaml` - ship lag, review lag, hours, weekend factor, standup attendance.
- `corpus/facts/chatter.yaml` - non-work Slack behavior.
- `corpus/facts/office_life.yaml` - PTO, conferences, external events.
- `corpus/facts/company_truth.yaml` - explicit ground truth for belief changes, handovers, opaque work, conflicts, side quests.
- `corpus/build/events.jsonl` - rendered replay log consumed by the mock providers.

Rendered event stream after this enrichment:

| Provider | Events |
|---|---:|
| GitHub | 16,980 |
| Slack | 9,990 |
| Calendar | 2,392 |
| Notion | 2,035 |
| Jira | 1,447 |
| QuickBooks | 1,150 |
| Gmail | 893 |
| Discord | 726 |
| Drive | 323 |
| Org | 61 |
| Total | 35,997 |

People: 42 historical identities, 29 active as of 2026-06-11, 13 departed.
Every person now has a voice profile, behavior pattern, and chatter bank.

Important caveat: the operational render uses `--end 2026-05-29` for recurring
rituals. The GitHub/public mirror can still include sparse later events; when
running on or near 2026-06-13, treat anything after that date as future/scheduled
signal, not present company state.

## What Changed In This Pass

The enrichment added four major realism layers:

1. Departures are now explicit. Short-tenure or inactive people with stale
   `last_active` dates now have `ended_at` values. The renderer emits
   `org.person.depart`, and replay updates `org.people.ended_at`, so payroll,
   Deel/Gusto/HiBob, Slack volume, 1:1s, and future assignments reflect attrition.

2. Belief-change ground truth now exists. `company_truth.yaml` describes five
   expected belief deltas, and the renderer turns those into ordinary Slack,
   Notion, Drive, Jira, and Calendar events.

3. Opaque causes are deliberately encoded. Some work starts after generic
   meetings like "Coffee sync" or "Partner demo prep"; the reason is only
   partially visible later through downstream tickets and notes.

4. H1-2026 hires and founders now have explicit voices. Sabin, Nisha, Roshan,
   Lena, Simanta, and Chandan no longer fall back to generic work snippets.

## Phases

### Phase 1: Founding, Seed, And Public Thesis

Window: 2024-02-01 to 2024-04-30

Aim: prove Alpen is a Bitcoin-native financial system company, not just a
research lab.

Main activity:

- Founders start with a tight founder-only operating loop.
- Seed/founders capital work lives mostly in Gmail and cofounder DMs.
- Public thesis becomes "Bitcoin's own financial system" instead of only
  "Bitcoin ZK rollup".
- Tooling is fresh, so early decisions are fragmented.

Obstacles:

- Investors ask for concrete bridge and proof answers before much of the
  implementation exists.
- Founders need to sound ambitious without overcommitting to a mainnet timeline.
- Early work has little GitHub/Jira density, so the model layer should not
  over-interpret silence as inactivity.

Expected Fyralis beliefs:

- High confidence: founders are converging on the product narrative.
- Medium confidence: technical implementation is still mostly pre-public.
- Low confidence: stable team-level ownership exists yet.

### Phase 2: Strata Node Skeleton And Checkpoint Interface

Window: 2024-05-01 to 2024-09-30

Aim: turn the thesis into an internal Strata node that can import blocks,
reason about checkpoints, and provide bridge-facing commitments.

Main activity:

- The `alpen` repo becomes the main protocol work surface.
- Prajwol, Trey, Bewakes, MdTeach, Rajil, and later Krsna shape the node.
- Bridge-facing checkpoint data starts forcing protocol internals to become
  explicit earlier than the protocol team wants.
- CI/test runtime starts to matter.

Obstacles:

- Common types are cross-repo and cross-team.
- Checkpoint names differ between protocol, bridge, explorer, and docs.
- Review queues grow around consensus traits.

Expected belief shift:

- From: "checkpoint data can stay internal while the node matures."
- To: "checkpoint data must become explicit, versioned, and bridge-observable."

### Phase 3: Common Types, Dogfood, And Strategic Round Pressure

Window: 2024-10-01 to 2025-03-31

Aim: make the Strata node repeatable enough for dogfood while closing the
strategic round.

Main activity:

- Common-module cleanup and stricter encoding/hashing helpers land.
- The strategic round creates hidden urgency around benchmarks and testnet dates.
- Investor diligence appears in Gmail while engineering keeps narrowing scope.
- Krsna starts turning local runs into CI-backed smoke tests.

Obstacles:

- Holiday availability thins reviewers.
- Investor pressure is not always visible in public engineering channels.
- Dogfood runbooks depend on a few people.

Expected belief shifts:

- Node work moves from one monolithic loop to separate sync, execution, and
  checkpoint tasks.
- Explorer scope moves from rich intermediate event streams to finalized,
  stable checkpoint summaries.

### Phase 4: Bridge Hardening, Public Testnet, And Glock Release

Window: 2025-04-01 to 2025-09-30

Aim: ship public testnet and a credible bridge devnet path without overstating
safety.

Main activity:

- Bridge work shifts from mocked reads to explicit Strata event subscriptions.
- Public testnet launches on 2025-08-04.
- Glock release lands on 2025-08-19.
- First-week testnet and bridge incidents create dashboard and restart side quests.
- ProofOfKeags and John Light leave during the critical launch window.

Obstacles:

- Bridge safety claims are stronger than implementation early in the phase.
- Withdrawal construction and peg-out builder ownership partially leaves with
  ProofOfKeags.
- DevRel/docs ownership thins when John Light exits.
- Public messaging stays optimistic while internal Jira shifts toward reliability.

Expected belief shifts:

- From: "testnet stability implies mainnet is close."
- To: "testnet proves demand, but mainnet readiness depends on restart, reorg,
  operator, dashboard, and runbook maturity."

### Phase 5: Starknet, Prague, Mosaic, And Operator Reliability

Window: 2025-10-01 to 2026-05-29

Aim: make Glock/Mosaic credible shared infrastructure and support a repeatable
operator-grade launch story.

Main activity:

- Starknet shared verifier collaboration becomes public.
- Prague testnet support lands.
- Armin leaves with bitcoind-async-client tacit knowledge in January 2026.
- H1-2026 hires join: Sabin, Nisha, Roshan, Lena.
- Mosaic moves from research demo to operator-grade manifest/retrieval reliability.
- Public Mosaic launch lands on 2026-05-07.

Obstacles:

- Partner pressure is often visible only as vague prep meetings and downstream tasks.
- Research laptop demos diverge from operator environments.
- New hires need context and ask questions that expose missing docs.
- Infra ownership arrives late but changes the outcome.

Expected belief shift:

- From: "Mosaic is research-complete enough for launch."
- To: "Mosaic is launchable only after operator manifest, retrieval, and fault
  injection reliability catch up."

## Month-By-Month Timeline

### 2024-02

Event volume: 382.

Founders bootstrap the company. Signal is mostly org bootstrap, Slack workspace,
finance/founders capital, and founder communication. Fyralis should infer a
small founder-led organization with high narrative uncertainty and little
engineering evidence.

### 2024-03

Event volume: 340.

Stealth build continues. Low public engineering volume is expected. Do not infer
low execution quality from low GitHub/Jira density yet.

### 2024-04

Event volume: 449.

Public thesis and seed/fundraising story form. The company moves from private
research direction into a public financial-system narrative.

### 2024-05

Event volume: 624.

`alpen` work begins in earnest. Trey, Bewakes, and Prajwol create the first
node skeleton and early checkpoints. New-hire onboarding begins.

### 2024-06

Event volume: 588.

Protocol interface work expands. MdTeach becomes a review/invariant voice.
Early signs of type-boundary ambiguity appear.

### 2024-07

Event volume: 1,022.

Rajil joins bridge-facing checkpoint conversations. Conference travel and
engineering volume rise. The first durable cross-team tension appears:
protocol wants internal freedom, bridge wants stable fields.

### 2024-08

Event volume: 958.

The checkpoint boundary problem becomes unavoidable. Work stays active but
fixtures and shared type migrations create review drag.

### 2024-09

Event volume: 1,003.

The node loop starts being split into sync, execution, and checkpoint tasks.
Burnout drift begins for Rajil and MdTeach over this broader period; Fyralis
should see late-hour activity creep, not just isolated late nights.

### 2024-10

Event volume: 1,374.

Hiring wave and common-module cleanup. Aaron joins as a research/audit-style
review voice. The company becomes less founder-only and more team-shaped.

### 2024-11

Event volume: 1,166.

Strata Bridge hardening starts. State machine boundaries are unclear. The model
should see bridge ownership crossing protocol, infra, and research.

### 2024-12

Event volume: 973.

Holiday availability thins reviewers. Belove Bist leaves on 2024-12-20. This
is the first explicit departure signal.

### 2025-01

Event volume: 1,478.

Strategic round pressure combines with dogfood work. Krsna begins helping from
infra. First explicit belief arc starts: checkpoint explorer feed scope.

Expected belief before change:

- The explorer should stream rich intermediate events for debugging.

### 2025-02

Event volume: 1,328.

Opaque hallway arc: on 2025-02-06, Prajwol, Rajil, and Krsna have a generic
"Coffee sync". Afterward, Drive and Jira show scope narrowing without a long
public Slack rationale.

Belief changed on 2025-02-12:

- From rich explorer stream.
- To finalized checkpoint summaries first.

Bridge contract belief arc starts on 2025-02-24:

- From bridge mocked reads are okay for now.
- To explicit Strata event subscription is required.

### 2025-03

Event volume: 1,362.

Bridge/event boundary becomes a formal decision. Dogfood milestone closes.
Fyralis should connect review pressure, reassignments, and follow-up churn to
interface-contract risk.

### 2025-04

Event volume: 1,476.

Bridge audit-style work begins. Aaron pushes for separate invariants around
deposit finality, withdrawal liveness, and key rotation.

### 2025-05

Event volume: 1,456.

Research and bridge hiring wave. Some short-tenure contributors begin to appear.
State snapshots and replay tests become a side path.

### 2025-06

Event volume: 1,527.

Bridge recovery and dashboard needs increase. Ceyhun exits on 2025-06-24.
This month should look busy but fragmented.

### 2025-07

Event volume: 1,815.

Pre-testnet push. GitHub/Slack/Jira density rises. CI and long-running tests
become noisy. Rajil and Krsna are important for bridge and dashboards.

### 2025-08

Event volume: 1,553.

Public testnet launches on 2025-08-04. Glock release lands on 2025-08-19.
Two belief arcs happen:

- Testnet confidence starts optimistic on 2025-08-04.
- Glock claims boundary changes between 2025-08-12 and 2025-08-18.

John Light gives notice around 2025-08-19 and leaves 2025-09-05. ProofOfKeags
gives notice 2025-08-21 and leaves 2025-09-15.

### 2025-09

Event volume: 1,654.

Testnet belief changes on 2025-09-03. Fyralis should reduce mainnet-readiness
confidence while keeping launch-demand confidence high.

ProofOfKeags handover is partial. Peg-out/key-rotation cleanup slips. DevRel
docs lag after John Light exits. Hakkush, just-erray, cyphersnake, aunth, and
Arniiiii are near the end of short-tenure windows.

### 2025-10

Event volume: 1,674.

Starknet shared verifier public collaboration. Several short-tenure exits are
now explicit. Fyralis should see attrition as a source of context loss rather
than simply lower individual productivity.

### 2025-11

Event volume: 1,719.

Prague/testnet reliability pressure. Opaque cost-freeze/CI side effect happens
on 2025-11-06: finance pressure is visible in spending, but engineering feels
it as CI runner and flaky test pain.

### 2025-12

Event volume: 1,882.

Prague support, winter break, and Armin's notice on 2025-12-23. The bitcoind
async-client area enters a handover-risk period.

### 2026-01

Event volume: 2,271.

Armin leaves on 2026-01-12. Sabin starts 2026-01-20. Mosaic belief arc starts:
research demo is initially treated as enough for public launch narrative.

### 2026-02

Event volume: 1,839.

Nisha starts 2026-02-17 and turns bridge handoff notes into checklists. Fyralis
should see handover quality improving after Nisha, not immediately after
ProofOfKeags left.

### 2026-03

Event volume: 2,295.

Roshan starts 2026-03-09. Opaque partner demo pressure on 2026-03-18 causes
Mosaic demo/environment work to appear downstream. Alex joins protocol on
2026-03-27.

### 2026-04

Event volume: 2,693.

Lena starts 2026-04-13. Mosaic belief changes on 2026-04-21: operator-grade
manifest/retrieval/fault-injection reliability becomes launch criteria.

### 2026-05

Event volume: 2,501.

Mosaic public release on 2026-05-07. The company is active but visibly more
mature: decisions are narrower, handovers more explicit, and reliability has
become a first-class launch concern.

### 2026-06 To 2026-10

Sparse future/scheduled mirror volume: 2026-06 has 48 events, 2026-07 has 45,
2026-08 has 34, 2026-09 has 18, and 2026-10 has 5. Treat these as future or
scraped/public mirror artifacts if evaluating against the 2026-06-13 current
date.

## Belief-Change Answer Key

### 1. Checkpoint Explorer Feed Scope

Initial date: 2025-01-20.
Changed: 2025-02-12.
Owners: Prajwol, Rajil, Krsna.

Old belief:

- The checkpoint explorer should stream rich intermediate node events because
  it helps debugging.

New belief:

- The explorer feed should expose finalized summaries first. Intermediate node
  events stay internal until contracts are versioned.

Why changed:

- A hallway sync after noisy CI made it clear bridge/explorer consumers would
  accidentally depend on unstable event names.

Opaque clue:

- The decisive meeting is only "Coffee sync"; the work shows up later as
  Drive/Jira follow-ups.

Expected Fyralis output:

- Lower confidence in "Alpen optimizes for rich explorer visibility."
- Higher confidence in "Alpen prioritizes stable finalized checkpoint summaries."

### 2. Bridge Mocks Vs Explicit Events

Initial date: 2025-02-24.
Changed: 2025-03-07.
Owners: Rajil, Prajwol, MdTeach.

Old belief:

- The bridge can integrate against mocked Strata checkpoint reads until devnet.

New belief:

- The bridge needs explicit Strata event subscription before further feature work.

Why changed:

- Review exposed that mocked reads hid reorg and cancellation semantics.

Expected Fyralis output:

- Attribute bridge churn to interface-contract risk, not simple lateness.
- Connect branch churn, Jira reassignment, and review notes.

### 3. Testnet Mainnet Readiness

Initial date: 2025-08-04.
Changed: 2025-09-03.
Owners: Simanta, Pramod, Rajil, Krsna.

Old belief:

- Stable public testnet is strong evidence mainnet sequencing and bridge
  operation are close.

New belief:

- Testnet proves demand, but mainnet readiness depends on restart, reorg,
  operator, dashboard, and runbook maturity.

Why changed:

- First-week incidents showed operational failure paths, not crypto failure paths.

Expected Fyralis output:

- Preserve positive belief about demand.
- Lower confidence in near-term mainnet readiness.
- Recommend reliability/runbook/dashboard work over feature expansion.

### 4. Public Glock Claims

Initial date: 2025-08-12.
Changed: 2025-08-18.
Owners: Aaron, Pramod, Simanta.

Old belief:

- Alpen can publish broad performance and verifier-readiness claims around Glock.

New belief:

- Public claims must be limited to measured verifier behavior, reproducible
  benchmarks, and clearly named assumptions.

Why changed:

- Research review found benchmark facts were being mixed with product-readiness
  language.

Expected Fyralis output:

- Learn Aaron is a claim-risk governor, not a blocker.
- Detect narrower release copy and more benchmark workflow review.

### 5. Mosaic Demo To Operator-Grade Launch

Initial date: 2026-01-15.
Changed: 2026-04-21.
Owners: Trey, Jose, Zk2u, Roshan, Lena.

Old belief:

- A research-laptop Mosaic demo is enough to support public launch narrative.

New belief:

- Mosaic needs operator-grade manifests, retrieval fault injection, and
  reproducible demo environments before launch.

Why changed:

- Partner demo rehearsal failed outside the research setup.

Expected Fyralis output:

- Revise "Mosaic is research-complete" into "Mosaic is launchable only after
  operator reliability catches up."
- Explain why infra joins a research-owned thread.

## Handover Answer Key

### ProofOfKeags Bridge Exit

Notice: 2025-08-21.
End: 2025-09-15.
Previous owner: withdrawal transaction construction and peg-out builder helpers.
New owners: Rajil, Krsna, Nisha.

Expected pattern:

- Partial handover.
- Code context exists; runbook and ownership map are weak.
- Key rotation and peg-out cleanup slip about three weeks.
- Nisha's later arrival improves handover quality by turning notes into checklists.

### John Light DevRel Exit

Notice: 2025-08-19.
End: 2025-09-05.
Previous owner: public docs, quickstart language, launch community replies.
New owners: Chandan and Simanta.

Expected pattern:

- Docs fixes move into founder/ops queues.
- Quickstart updates slow after testnet week.
- Founder time is consumed by docs/community context.

### Armin Bitcoind Exit

Notice: 2025-12-23.
End: 2026-01-12.
Previous owner: bitcoind-async-client reconnection behavior and RPC failure fixtures.
New owners: Krsna, Lena, Rajil.

Expected pattern:

- Good written notes, but tacit debugging knowledge is missing.
- Cancelled-checkpoint and reconnection issues reopen in January.
- Reliability stabilizes after Lena has enough context, not immediately at handover.

## Opaque Work Arcs

| Arc | Date | Visible cause | Actual effect |
|---|---:|---|---|
| hallway-checkpoint-api | 2025-02-06 | Calendar says "Coffee sync" | Explorer feed gets narrowed; bridge compatibility notes appear |
| partner-demo-pressure | 2026-03-18 | Calendar says "Partner demo prep" | Mosaic manifest/retrieval reliability becomes launch blocker |
| cost-freeze-ci-side-effect | 2025-11-06 | Finance/burn pressure visible elsewhere | CI runner changes increase flaky long-running test pain |

Fyralis should not expect every initiative to have a clean public Slack origin.
The correct reasoning pattern is consequence-first: detect downstream work,
search for weak or private signals, then lower confidence in causal explanation
until enough evidence appears.

## Conflicts

### Protocol Vs Bridge API

Window: 2025-02-24 to 2025-04-04.

Protocol view:

- Keep internals moving until checkpoint abstractions settle.

Bridge view:

- Freeze event names early so bridge safety can be tested.

Resolution:

- Explicit event subscription boundary and compatibility note.

### Research Vs Marketing Claims

Window: 2025-08-12 to 2025-08-19.

Research view:

- Publish only measured claims and assumptions.

Founder/marketing view:

- Make Glock legible as a public launch milestone.

Resolution:

- Narrower release copy, reproducible benchmark workflow, Aaron as claims reviewer.

### Research Vs Infra On Mosaic

Window: 2026-03-18 to 2026-05-07.

Research view:

- Protocol demo proves the important thing.

Infra view:

- Launch demos need repeatable operator behavior.

Resolution:

- Manifest, retrieval reliability, demo checklist, and fault-injection harness.

## Side Quests

| Side quest | Window | Owner | Why it exists | Payoff |
|---|---|---|---|---|
| Dashboard probes | 2025-06-20 to 2025-09-10 | Krsna | Devnet-only bridge failures are invisible | Faster public testnet incident response |
| Docs after DevRel | 2025-09-05 to 2025-10-10 | Chandan | John Light leaves docs context thin | Quickstart stabilizes, but consumes founder time |
| Bitcoind reconnect | 2026-01-12 to 2026-04-30 | Lena | Armin leaves tacit reconnection debugging knowledge | Readiness states reduce false incident pages |
| Mosaic demo env | 2026-03-18 to 2026-05-07 | Zk2u | Laptop demo diverges from operator setup | Demo manifest and fault injection become launch criteria |

## Employee Directory And Behavioral Patterns

Pattern fields:

- `ship_lag_h`: hours late or early relative to planned Jira done date. Negative
  means tends to ship early.
- `review_h`: expected PR review response time.
- `standup`: attendance probability.
- `wknd`: weekend message factor.
- `hours`: normal active-hour profile.
- `bad_news`: reaction style when something breaks.

| Person | Role/team | Status | Work pattern |
|---|---|---|---|
| Rajil Bajracharya (`Rajil1213`) | senior engineer, bridge | active | high-volume bridge owner; evenings; owns/debugs bad news; ship_lag_h -12.2, review_h 10.0, standup 0.80, wknd 0.50 |
| Prajwol Gyawali (`prajwolrg`) | senior engineer, protocol | active | protocol invariant voice; evenings; owns/debugs; ship_lag_h 13.1, review_h 10.5, standup 0.89, wknd 0.29 |
| Jose Storopoli (`storopoli`) | senior engineer, protocol | active | spec/serialization cleanup; evenings; owns/debugs; ship_lag_h -2.7, review_h 12.5, standup 0.82, wknd 0.37 |
| Abishek Bashyal (`MdTeach`) | senior engineer, protocol | active | late-night invariant reviewer; owns/debugs; ship_lag_h -7.3, review_h 6.5, standup 0.86, wknd 0.89 |
| Dilli Raj Paudel (`krsnapaudel`) | senior engineer, infra | active | dashboards/CI/probes; normal hours; owns/debugs; ship_lag_h 14.0, review_h 16.3, standup 0.84, wknd 0.26 |
| Trey Del Bonis (`delbonis`) | engineer, protocol | active | common types, Mosaic, founder-like technical context; evenings; owns/debugs; ship_lag_h -4.6, review_h 8.4, standup 0.91, wknd 0.23 |
| Sapin Bajracharya (`sapinb`) | ops, ops | active | ops/access/vendor glue; normal hours; owns/debugs; ship_lag_h 1.0, review_h 7.5, standup 0.95, wknd 0.20 |
| `Zk2u` | infra, research | active | late-night research/infra hybrid; owns/debugs; ship_lag_h -2.8, review_h 6.4, standup 0.90, wknd 0.72 |
| Christian Lewe (`uncomputable`) | research, bridge | active | deep bridge/research reviewer; evenings; owns/debugs; ship_lag_h 14.7, review_h 30.3, standup 0.76, wknd 0.24 |
| Purushotam Sangroula (`purusang`) | engineer, infra | active | explorer/faucet/dashboards; evenings; owns/debugs but ships late; ship_lag_h 45.5, review_h 10.1, standup 0.77, wknd 0.31 |
| Bibek Pandey (`bewakes`) | engineer, protocol | active | persistence/common code; evenings; owns/debugs; ship_lag_h 18.7, review_h 9.8, standup 0.76, wknd 0.27 |
| `voidash` | engineer, protocol | active | protocol implementation/review; evenings; owns/debugs; ship_lag_h 6.7, review_h 8.7, standup 0.89, wknd 0.37 |
| Mukesh Tiwari (`mukeshdroid`) | research, research | active | bridge/research design depth; evenings; owns/debugs; ship_lag_h 10.5, review_h 30.6, standup 0.93, wknd 0.20 |
| Hakan Karakus (`Hakkush-07`) | research, research | left 2025-10-10 | short-tenure math/research; normal hours; owns/debugs; ship_lag_h 4.2, review_h 28.7, standup 0.83, wknd 0.38 |
| Evgeny Zdanovich (`evgenyzdanovich`) | engineer, protocol | active | protocol/asm contributor; evenings; owns/debugs; ship_lag_h -7.5, review_h 6.3, standup 0.80, wknd 0.42 |
| Belove Bist (`bbist`) | engineer, bridge | left 2024-12-20 | early bridge contributor; normal hours; owns/debugs but slips; ship_lag_h 45.0, review_h 6.5, standup 0.84, wknd 0.38 |
| Aaron Feickert (`AaronFeickert`) | research, research | active | cryptographic claims/audit governor; normal hours; owns/debugs; ship_lag_h 1.6, review_h 26.1, standup 0.79, wknd 0.37 |
| Nakul Khambhati (`nakkstar123`) | research, research | active | garbled circuits/Mosaic; evenings; owns/debugs; ship_lag_h -6.3, review_h 28.7, standup 0.89, wknd 0.46 |
| `sistemd` | engineer, bridge | left 2026-01-16 | short bridge tenure; evenings; owns/debugs; ship_lag_h 16.6, review_h 12.9, standup 0.91, wknd 0.50 |
| Hamid (`irnb`) | engineer, protocol | active | protocol/asm support; evenings; owns/debugs; ship_lag_h 0.8, review_h 18.0, standup 0.92, wknd 0.27 |
| Keags (`ProofOfKeags`) | engineer, bridge | left 2025-09-15 | withdrawal construction owner; evenings; owns/debugs but ships late; ship_lag_h 45.1, review_h 12.9, standup 0.79, wknd 0.56 |
| `manishbista28` | research, research | active | g16/Mosaic/circuit work; evenings; owns/debugs; ship_lag_h 7.8, review_h 25.6, standup 0.85, wknd 0.32 |
| John Light (`john-light`) | devrel, devrel | left 2025-09-05 | public docs/community context; normal hours; owns/debugs; ship_lag_h 9.7, review_h 9.6, standup 0.94, wknd 0.34 |
| erray (`just-erray`) | research, research | left 2025-10-17 | short research contributor; evenings; owns/debugs; ship_lag_h 12.3, review_h 33.0, standup 0.90, wknd 0.22 |
| `cyphersnake` | engineer, research | left 2025-10-24 | late-night progcrypto contributor; owns/debugs; ship_lag_h 10.7, review_h 8.9, standup 0.92, wknd 0.68 |
| `barakshani` | engineer, protocol | active | protocol support; normal hours; owns/debugs; ship_lag_h 22.5, review_h 11.9, standup 0.87, wknd 0.58 |
| Abishkar Chhetri (`chhetri22`) | cofounder/COO, exec | active | ops/funding/people operator; evenings; owns/debugs; ship_lag_h 17.0, review_h 7.6, standup 0.95, wknd 0.28 |
| Ceyhun Sen (`ceyhunsen`) | engineer, research | left 2025-06-24 | very short research/low-level tenure; normal hours; owns/debugs; ship_lag_h 62.4, review_h 12.9, standup 0.77, wknd 0.20 |
| `sampkaALP` | ops, ops | left 2025-08-29 | short ops/helper tenure; normal hours; owns/debugs; ship_lag_h 21.1, review_h 4.6, standup 0.84, wknd 0.21 |
| Alex Yang (`alexhui01`) | engineer, protocol | active | H1-2026 protocol hire; normal hours; owns/debugs; ship_lag_h 12.3, review_h 6.6, standup 0.89, wknd 0.19 |
| Pramod Kandel (`pramodkandel`) | cofounder/CTO, exec | active | CTO, diligence and technical narrative; normal hours; owns/debugs; ship_lag_h 11.0, review_h 5.9, standup 1.00, wknd 0.38 |
| Vlad Maslianko (`aunth`) | engineer, protocol | left 2025-10-10 | short p2p/protocol tenure; normal hours; owns/debugs; ship_lag_h 16.8, review_h 11.7, standup 0.75, wknd 0.18 |
| `madan-oss` | research, research | active | H1-2026 research contributor; evenings; owns/debugs; ship_lag_h 17.4, review_h 33.2, standup 0.76, wknd 0.25 |
| Ali (`ali-rezai`) | engineer, protocol | left 2025-08-01 | short protocol contributor; normal hours; owns/debugs; ship_lag_h 18.8, review_h 10.8, standup 0.81, wknd 0.35 |
| Armin Sabouri (`arminsabouri`) | infra, infra | left 2026-01-12 | bitcoind/reconnect knowledge owner; normal hours; owns/debugs; ship_lag_h 23.1, review_h 12.9, standup 0.81, wknd 0.34 |
| `Arniiiii` | engineer, protocol | left 2025-10-10 | short p2p contributor; normal hours; owns/debugs; ship_lag_h 24.2, review_h 7.5, standup 0.81, wknd 0.52 |
| Simanta Gautam (`simanta-gautam`) | cofounder/CEO, exec | active | warm founder voice; product/investor/partner narrative; ship_lag_h 62.7, review_h 4.0, standup 1.00, wknd 0.37 |
| Chandan Sharma Subedi (`chandansharmasubedi`) | cofounder, exec | active | partner/docs/planning connective tissue; ship_lag_h 22.2, review_h 8.8, standup 0.89, wknd 0.46 |
| Sabin Thapa (`sabin-thapa`) | engineer, protocol | active, joined 2026-01-20 | careful new-hire protocol engineer; evenings; asks for context; ship_lag_h 8.0, review_h 11.0, standup 0.90, wknd 0.30 |
| Nisha Maharjan (`nisha-maharjan`) | senior engineer, bridge | active, joined 2026-02-17 | handoff/runbook bridge owner; normal hours; ship_lag_h -3.5, review_h 7.0, standup 0.93, wknd 0.25 |
| Roshan KC (`roshan-kc`) | research, research | active, joined 2026-03-09 | cautious Mosaic/reproducibility researcher; evenings; ship_lag_h 28.0, review_h 26.0, standup 0.80, wknd 0.35 |
| Lena Fischer (`lena-fischer`) | infra/SRE, infra | active, joined 2026-04-13 | SRE/reconnect/on-call clarity; mornings; ship_lag_h 2.0, review_h 9.0, standup 0.88, wknd 0.20 |

## What Fyralis Should Discover

Strong signals:

- Rajil is an early/fast bridge owner but absorbs handover risk from others.
- Prajwol is the protocol boundary/invariant person, often causing scope to
  narrow for safety.
- MdTeach and Aaron are risk reviewers; they may slow claims but improve safety.
- Krsna becomes a reliability side-quest owner when bridge/devnet failures get
  operational.
- Nisha improves bridge handover quality after joining.
- Lena converts tacit infra failure modes into observable readiness states.
- Simanta keeps external optimism high even when internal readiness confidence
  should drop.

Important non-obvious causal patterns:

- Vague meetings can precede real work. Do not require public channel rationale.
- Departures create lag in the area the person owned, not just lower message count.
- Founder/investor pressure changes priorities through Gmail/DMs before Jira
  explains why.
- Reliability work is often side-quest-shaped: it appears as dashboards, runbooks,
  and small tickets before it becomes the main story.

Recommended model-layer checks:

- Does Fyralis distinguish public optimism from internal readiness risk?
- Does Fyralis infer the checkpoint-feed belief reversal from weak evidence?
- Does Fyralis connect ProofOfKeags leaving to bridge cleanup lag?
- Does Fyralis revise Mosaic readiness only after the partner demo pressure and
  operator-environment work?
- Does Fyralis avoid treating short-tenure departed people as still-current owners?
- Does retrieval find cross-source context for a question like: "Why did bridge
  key rotation slip after testnet?"
