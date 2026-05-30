**Goal**

Harden `repo:bitcoind-async-client` for Strata node operations by moving reconnection into a single supervisor task and exposing explicit readiness states to `product:strata` startup. The design target is boring operational behavior: if `bitcoind` restarts, stalls, reindexes, or temporarily rejects RPC calls, Strata should stop pretending the Bitcoin backend is available, surface the degraded state, and recover without requiring node restart.

The immediate B9 implementation is owned by person:krsnapaudel and person:prajwolrg, with review from person:MdTeach, person:bewakes, person:delbonis, person:storopoli, and protocol callers represented by person:alexhui01. The boundary is intentionally shared: infra owns the client crate mechanics, while protocol owns the meaning of readiness during Strata startup and steady-state block ingestion.

**Non-goals**

This does not redesign the Strata Bitcoin adapter API end-to-end. We are not replacing JSON-RPC polling with ZMQ, adding a separate indexer, or making `bitcoind-async-client` a general Bitcoin node abstraction.

This does not solve finality, deposit confirmation policy, bridge liveness, or reorg handling by itself. Those remain protocol-layer decisions. The client only reports whether the underlying Bitcoin RPC transport is connected, authenticated, responsive, and consistent enough for callers to begin their own logic.

This also does not make regtest mocks fully production-equivalent. The rollout includes better restart and timeout tests, but we should not keep expanding mocks until they become a second implementation of `bitcoind`.

**Background**

The current async client grew from a narrow need: issue Bitcoin Core RPC calls from Strata services without blocking the Tokio runtime and with enough retry behavior to survive normal local development. That was acceptable for early Strata integration, but it hid several production-like failure modes.

The main problem is ownership of connection state. Today, each caller can observe failures only through individual RPC errors or timeout paths. During node startup, this leads to ambiguous behavior: a call to `getblockchaininfo` might fail because credentials are wrong, because `bitcoind` is not listening yet, because it is warming up, because it is reindexing, or because the TCP connection was reused across a daemon restart. These are not equivalent states for Strata. Some should block startup. Some should retry. Some should mark the Bitcoin dependency unhealthy but allow the process to keep serving diagnostics.

Regtest repeatedly hid this because tests usually start `bitcoind`, create a client, mine blocks, and assume immediate availability. That path does not exercise restart gaps, cookie rotation, slow RPC responses, or the half-open connection behavior we saw in longer Strata node runs. The safer timeout work also competed with initial release pressure: forcing every caller to reason about explicit timeouts delayed integration, so we accepted implicit behavior that was too optimistic.

The B9 decision is to centralize connection lifecycle in one supervisor task and make readiness explicit in `repo:alpen` startup. person:prajwolrg is rewriting reconnection around that supervisor, while I thread readiness states through the Strata node initialization path.

**Proposed Design**

`bitcoind-async-client` will expose a cloneable client handle backed by a single supervisor task. The handle remains cheap to pass into protocol components, but it no longer owns independent retry or connection lifecycle state. All transport initialization, health probing, backoff, timeout classification, and readiness publication happen in the supervisor.

The supervisor maintains a small state machine:

`Unconfigured`: client was constructed without enough endpoint or auth material. This is a terminal startup configuration error.

`Connecting`: supervisor is attempting to establish a usable RPC path. This includes TCP connect, HTTP client setup, auth discovery, and initial probe.

`Warming`: RPC is reachable, but Bitcoin Core reports a transient state such as loading block index, warming up, rescanning, or otherwise not ready for normal chain queries.

`Ready`: required probes have succeeded. At minimum this means `getblockchaininfo` returns a coherent chain, headers, blocks, initial block download flag, and best block hash. For Strata startup we also require network identity to match configured network/regtest parameters.

`Degraded`: the client was previously ready, but probes or normal RPCs now indicate timeout, connection reset, auth rotation, daemon restart, or inconsistent responses.

`FailedPermanent`: unrecoverable configuration or compatibility failure, for example unsupported Bitcoin Core RPC behavior or persistent auth rejection after reload.

The client handle exposes two categories of operations. First, normal RPC methods keep returning typed RPC results. They do not hide errors forever behind internal retry loops. Each call gets a caller-supplied or default deadline, and errors include classification: timeout, transport, auth, warmup, bad response, or permanent config. Second, readiness subscription exposes a watch channel or equivalent stream of state transitions. Strata startup consumes this directly instead of probing readiness ad hoc.

The supervisor runs an internal loop with bounded exponential backoff and jitter. Backoff is state-sensitive. `Connecting` can back off aggressively after repeated connection refusal. `Warming` probes more slowly and logs less noisily because Bitcoin Core warmup is expected. `Degraded` probes quickly at first to catch short restarts, then decays to avoid hammering a sick daemon.

Readiness is established by a probe set rather than one successful TCP connection. The initial probe should include:

- `getblockchaininfo` for chain identity, IBD status, height, headers, and best hash.
- `getnetworkinfo` for version and local services, mainly for diagnostics.
- Optional `getblockhash(height)` when Strata has a configured checkpoint or known genesis expectation.

For B9, Strata node startup should block protocol services that require Bitcoin data until readiness reaches `Ready`. The process itself may start HTTP metrics, admin endpoints, and logs while waiting. This makes the startup path more verbose, but it avoids the old behavior where dependent services began with an unusable client and failed later in unrelated code paths.

In `repo:alpen`, readiness becomes an explicit dependency in node composition. Bitcoin-backed components receive either a `ReadyBitcoindClient` wrapper or await a readiness gate before construction. This prevents accidental use during `Connecting` and makes tests update their assumptions. Old tests that relied on immediate availability need to drive the supervisor to `Ready` or use a test fixture that declares readiness intentionally.

Observability is part of the design, not a follow-up. The supervisor emits structured logs on state transitions, not every failed probe. Metrics include current readiness state, transition count by reason, last successful probe timestamp, probe latency, RPC timeout count, and reconnect attempts. person:krsnapaudel and person:arminsabouri should align metric names with infra conventions, while person:sapinb can use the state labels for ops runbooks.

**Trade-offs**

The main cost is startup complexity. The node composition path becomes more explicit: instead of “construct client and pass it everywhere,” we now model “construct client handle, wait for readiness, construct dependent services.” That is more code, and person:prajwolrg’s supervisor work forces tests to become less naive. I think this is the right cost because Bitcoin availability is already a real dependency; the old code only made it implicit.

A single supervisor is also a central point of behavior. If its state machine is wrong, all callers see the wrong readiness signal. The alternative, distributed retry logic, already failed us by producing inconsistent behavior across infra and protocol callers. Centralizing gives person:MdTeach, person:delbonis, and person:storopoli one place to review timeout semantics.

We also risk over-classifying errors. Bitcoin Core RPC errors are not always cleanly separated from HTTP failures or local environment issues. The implementation should keep classification conservative. If unsure, report `Degraded` with the raw source error attached rather than inventing a precise state.

Finally, readiness does not imply chain usefulness. A node can be `Ready` while still in IBD, depending on configured policy. For Strata startup we should make that policy explicit: either allow `Ready` with `initialblockdownload=true` for local dev, or require synced headers/blocks for production profiles.

**Rollout Plan**

First, land the supervisor inside `repo:bitcoind-async-client` behind the existing public client shape where possible. Add the readiness watch API and typed state enum without forcing every caller to migrate immediately.

Second, update `repo:alpen` startup to await readiness before constructing Bitcoin-dependent Strata services. Keep diagnostics and metrics live while waiting. person:bewakes and person:alexhui01 should review the protocol startup implications.

Third, replace fragile regtest assumptions. Add tests that start the client before `bitcoind`, restart `bitcoind` under load, simulate warmup-like RPC errors, and verify transition sequences. Mocks should validate caller behavior, while at least one integration test should use a real daemon restart.

Fourth, wire metrics and logs into the infra dashboards. The minimum useful alert is “Strata process up, Bitcoin readiness not `Ready` for N minutes.” That catches the operational failure mode without paging on short restarts.

Finally, remove old per-call reconnection behavior once protocol callers have moved to readiness-aware construction. At that point the crate has one lifecycle owner, Strata startup has explicit dependency gates, and `bitcoind` restarts become observable degraded events instead of confusing downstream RPC failures.
