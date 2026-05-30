## Goal

Build a repeatable fault-injection harness for Mosaic data availability retrieval across `repo:mosaic` and `repo:mosaic-torrent`, with enough scenario coverage to turn intermittent retrieval failures into specific, reproducible bugs.

For this B7 audit pass, the goal is not to prove that Mosaic is fully adversary-resistant. The immediate goal is narrower: when a Strata node depends on Mosaic for blob availability, we should be able to simulate realistic peer, transport, storage, and timing failures and observe whether retrieval either succeeds within policy or fails with a useful, attributed reason.

The output should be:

- a scenario runner that can drive long-lived Mosaic nodes under controlled faults;
- a catalog of availability scenarios with expected behavior;
- structured metrics/logs consumable by `repo:alpen-dashboards`;
- bug reports against `repo:mosaic`, `repo:mosaic-torrent`, and `repo:strata-p2p` where behavior is ambiguous, flaky, or unsafe.

person:uncomputable remains driver for the audit thread. I will own the harness shape from research/infra. person:cyphersnake should review fault model realism, person:krsnapaudel should review operational fit, and person:Rajil1213 / person:ProofOfKeags should review bridge-facing failure semantics.

## Non-goals

We are not designing the Mosaic incentive market in this pass. That work keeps getting displaced by reliability issues, but combining it with fault injection will make both worse. The harness may later be reused for market tests, but B7 should not add payment, reputation, slashing, or pricing logic.

We are not changing the DA security model or introducing a new transport. Small transport changes are expensive because they cut across `repo:mosaic`, `repo:mosaic-torrent`, `repo:strata-p2p`, and bridge integration. The harness should expose transport weaknesses before we decide whether protocol changes are needed.

We are not attempting exhaustive Byzantine simulation. The initial focus is on failures we already suspect from flaky retrieval: partial peer availability, slow peers, corrupted chunks, stalled torrent sessions, peer churn, local store inconsistency, restart behavior, and long-running node degradation.

We are not replacing unit tests. This is an integration and soak-test layer with deterministic scenario definitions where possible and bounded randomness where useful.

## Background

Mosaic currently sits in the uncomfortable middle between protocol assumptions and operator reality. Strata needs blob data to remain retrievable when proofs or bridge actions depend on it. Operators need to know whether DA is healthy before a user-facing failure appears. Researchers need confidence that retrieval assumptions survive adverse network conditions. The implementation, meanwhile, is spread across Mosaic core and torrent-based transport, so a small change in peer selection or retry behavior can create a cross-repo debugging session.

Recent failures have been hard to classify. Some retrieval failures appear as ordinary timeouts. Some reproduce only after nodes have been running long enough to accumulate stale peer state or local store fragmentation. Some look like transport-level stalls but may actually be scheduler, backpressure, or retry-budget bugs. Because these are usually observed while bridge implementation continues moving, they get triaged as one-off bugs rather than as symptoms of a missing test surface.

The B7 audit should force the issue. We should run controlled failures first, then decide which transport or protocol changes are justified. This also gives person:krsnapaudel and ops a cleaner observability target: instead of dashboards that say “retrieval failed,” we can emit scenario labels, peer states, retry stages, chunk counts, and local-store events.

## Proposed design

The harness has four layers: topology, workload, fault injection, and assertions.

The topology layer starts N Mosaic nodes and optional Strata-facing clients in isolated local networks. Each node gets explicit identity, storage directory, advertised address, peer list, bandwidth limits, and log namespace. For early B7, Docker Compose is enough if it gives us deterministic network controls. If Compose becomes too blunt for latency and packet behavior, we can move the network layer to a small `tc/netem` wrapper without changing scenario definitions.

The workload layer publishes and retrieves fixed blob sets. We should use three corpus classes:

- small blobs that fit within normal happy-path retrieval assumptions;
- medium blobs split across enough chunks to exercise peer selection and partial retry;
- large blobs kept around for soak runs where long-running state matters.

Each blob should have a manifest containing content hash, chunk count, expected encoded size, publisher node, replication target, and retrieval policy. For ZK protocol realism, scenarios should include bursts shaped like proof-generation output rather than uniform random traffic: many small commitments, a few large witnesses or block payloads, then quiet periods.

The fault layer applies named faults at specific phases: before publish, after publish before replication, during retrieval, after partial retrieval, and across node restart. Initial faults:

- `peer_offline`: kill or isolate one or more peers;
- `slow_peer`: add latency and bandwidth caps;
- `corrupt_chunk`: mutate stored chunk bytes on selected nodes;
- `missing_chunk`: delete selected chunks from local storage;
- `stale_advertisement`: keep peer metadata visible after the peer is unreachable;
- `torrent_stall`: allow connection but block payload progress;
- `restart_with_partial_state`: restart node after partially completed retrieval;
- `long_run_churn`: repeatedly cycle peers while preserving client workload.

The assertion layer should treat “failed” as too coarse. Every scenario gets an expected terminal condition:

- retrieved content hash matches manifest within deadline;
- retrieval fails because insufficient honest availability is known;
- retrieval fails because local corruption is detected;
- retrieval retries alternate peers after timeout;
- retrieval refuses corrupted data and records the offending peer;
- node recovers after restart without poisoning future retrieval.

Assertions should inspect both API result and emitted telemetry. For example, a corrupt chunk scenario only passes if the client rejects the chunk, does not mark the blob as available, and emits a metric tagged with chunk verification failure. A slow peer scenario only passes if retry behavior stays inside configured budget and does not starve unrelated retrievals.

We should define scenarios as versioned YAML or TOML files checked into the test harness area, not as one-off scripts. A scenario file should contain topology, corpus, schedule, faults, deadlines, expected terminal condition, and metric expectations. The runner can produce a single JSON report per run with scenario ID, git SHAs, seed, node logs path, metrics summary, and pass/fail reason.

Observability should be designed with `repo:alpen-dashboards` in mind from the start. Minimum metrics:

- publish latency by blob class;
- retrieval latency by blob class;
- retrieval terminal status;
- chunk verification failures;
- peer retry count;
- active peers by state;
- stalled sessions;
- local store read/write/delete errors;
- restart recovery duration;
- bytes served and received per peer.

person:Zk2u and person:krsnapaudel should agree on metric names before we add dashboards. person:mukeshdroid can help decide which scenario summaries are useful for research review without dragging the harness into formal modeling.

## Trade-offs

A black-box harness is easier to keep stable across repos, but it may miss internal scheduler and store bugs. A white-box harness gives better fault precision but risks coupling to implementation details that are already moving. The proposed design is mostly black-box, with narrow test hooks for storage mutation, network shaping, and peer/session state. That gives us useful fault control without making the test suite a second implementation.

Running real torrent transport is slower than mocking it. We should still run the real path because most current ambiguity is transport-shaped: stalled sessions, stale peer state, and retry timing. Unit tests can mock those paths separately, but the audit needs end-to-end behavior.

Long-running scenarios will consume infra time. This is unavoidable because several failures only reproduce under long-lived nodes. To keep this bounded, we should split runs into smoke, audit, and soak tiers. Smoke runs finish quickly and gate ordinary PRs only after stabilization. Audit runs are manual or scheduled. Soak runs are nightly or pre-release.

There is also a product tension: operators want observability before Mosaic is feature-complete. I think we should accept that pull. If retrieval failures remain opaque, adding features will make the system harder to operate and harder to debug.

## Rollout plan

Phase 1: harness skeleton. person:Zk2u builds the scenario runner, topology definition, blob corpus generator, and JSON report format. person:krsnapaudel reviews how this runs in CI or infra without special local assumptions.

Phase 2: core scenarios. Add happy path, peer offline, slow peer, corrupt chunk, missing chunk, and restart-with-partial-state cases. person:cyphersnake and person:uncomputable review whether these cover the failures we have actually seen.

Phase 3: telemetry wiring. Add stable metrics and log fields in `repo:mosaic` and `repo:mosaic-torrent`. Wire initial dashboard panels in `repo:alpen-dashboards`. Failures should be attributable by scenario, blob, peer, and terminal condition.

Phase 4: long-running audit. Run churn and soak scenarios over multi-hour windows. File bugs instead of expanding scenario scope whenever behavior is flaky or unexplained. person:Rajil1213 and person:ProofOfKeags should verify bridge-facing semantics: which failures are retryable, which are operator alerts, and which must block dependent actions.

Phase 5: gate and maintain. Promote stable smoke scenarios into regular CI. Keep expensive audit and soak scenarios scheduled. Every new transport feature should add or update at least one scenario before merge.
