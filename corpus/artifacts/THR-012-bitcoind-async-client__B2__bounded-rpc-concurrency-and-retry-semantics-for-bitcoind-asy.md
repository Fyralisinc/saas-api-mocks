# Bounded RPC concurrency and retry semantics for bitcoind-async-client

## Summary

This RFC proposes hardening `repo:bitcoind-async-client` by adding explicit bounded RPC concurrency, bounded request queues, retry classification, and caller-visible timeout semantics suitable for Strata node operations.

The current client lets protocol callers treat Bitcoin Core RPC as effectively synchronous and always available. That assumption has held in many regtest paths, but it breaks under production-like behavior: `bitcoind` restarts, slow wallet/index operations, transient connection refusal, ZMQ/RPC skew, and node catch-up after downtime. When this happens, async tasks inside `product:strata` can accumulate unbounded in-flight RPCs, hold protocol progress hostage, or hide backpressure until unrelated subsystems start timing out.

The proposal is to make the client a small managed RPC execution layer rather than a thin async wrapper:

- Limit in-flight RPCs per client.
- Limit queued RPCs before execution.
- Attach per-method timeout defaults with caller override.
- Retry only errors classified as transient.
- Fail fast on queue saturation with typed errors.
- Emit structured metrics and traces for queueing, retries, timeout, and `bitcoind` health.
- Preserve a migration path for existing `repo:alpen` and `repo:strata-common` callers.

The immediate driver is the B2 design work: I will draft the client changes, person:MdTeach will map synchronous assumptions in `repo:alpen`, and person:krsnapaudel will own infra review and deployment behavior. person:bewakes should review protocol-facing failure semantics before we lock the API.

## Motivation

`repo:bitcoind-async-client` is infra-owned, but most of the risk is paid by protocol-owned callers. The protocol code asks for headers, blocks, transaction data, mempool state, and chain tip information during Strata node operation. If the RPC layer stalls, the failure mode is not isolated to “Bitcoin RPC is slow”; it can delay derivation, bridge monitoring, finality checks, and operator-facing health reporting.

The specific problems we have seen or are likely to see:

1. Unbounded task growth during `bitcoind` downtime.
   If `bitcoind` restarts or rejects connections, callers continue issuing RPCs. Without a bounded queue, we convert an external dependency outage into memory pressure and scheduler noise.

2. Ambiguous timeout behavior.
   Some callers want “wait until Bitcoin Core returns because the protocol cannot safely continue.” Others want “return quickly so the node can advance another path or report degraded health.” Today this is implicit and inconsistent.

3. Regtest mocks hide restart behavior.
   Our regtest mocks generally return clean JSON-RPC success/failure. They do not reproduce connection refused, TCP reset, half-open sockets, RPC warmup errors, or long pauses while indexes become available. This means tests pass while production-like recovery remains underspecified.

4. Retry policy is currently caller folklore.
   Retrying `getblockhash` after a connection reset is reasonable. Retrying malformed parameters is not. Retrying after a timeout may or may not be safe depending on the method. We need this classification in one place.

5. Observability is currently too late.
   We often learn the node is wedged from symptoms in higher-level Strata tasks. The RPC layer should expose queue depth, in-flight count, retry count, error class, timeout class, and observed latency directly.

This RFC intentionally does not try to redesign all Bitcoin Core integration. It gives `bitcoind-async-client` a stricter contract so protocol callers can reason about failure and backpressure.

## Detailed design

### Client model

The client will own an internal worker layer with:

- `max_in_flight: usize`
- `max_queue_depth: usize`
- `default_timeout: Duration`
- `method_timeout_overrides: HashMap<RpcMethod, Duration>`
- `retry_policy: RetryPolicy`
- `queue_policy: QueuePolicy`

Each outbound RPC request enters the queue before execution. If the queue is full, the client returns immediately with:

```rust
BitcoindRpcError::QueueFull {
    method,
    queue_depth,
    max_queue_depth,
}
```

Once dequeued, the request acquires an in-flight permit. The permit is held until the RPC attempt finishes or the request-level timeout expires. Retries reuse the same logical request budget unless the caller explicitly opts into per-attempt timeout behavior.

The default should be conservative for Strata node use:

- `max_in_flight = 16`
- `max_queue_depth = 256`
- `default_timeout = 10s`
- `max_retries = 3`
- exponential backoff with jitter: `100ms`, `250ms`, `500ms`, capped at `1s`

These numbers are starting points. person:krsnapaudel can tune infra defaults after testing against restart and reindex scenarios.

### Request timeout semantics

The client should support three timeout modes:

```rust
enum TimeoutMode {
    TotalBudget(Duration),
    PerAttempt(Duration),
    NoClientTimeout,
}
```

Default behavior is `TotalBudget`. This is the safest default because a logical request cannot live indefinitely through retries.

`PerAttempt` is available for long-running methods where the caller explicitly accepts a larger total wall-clock duration.

`NoClientTimeout` should be rare and require an explicit call site marker. It is acceptable only where cancellation would create worse behavior than waiting, and those call sites should be reviewed by person:MdTeach and the owning protocol reviewer.

We should not silently inherit transport-library defaults. Every request must have an effective timeout mode visible in traces.

### Retry classification

Errors should be classified into typed buckets before retry decisions:

```rust
enum RpcErrorClass {
    TransientTransport,
    BitcoindWarmingUp,
    Timeout,
    RateLimitedOrBusy,
    InvalidRequest,
    InvalidResponse,
    MethodNotFound,
    Auth,
    NotFound,
    ConsensusOrValidation,
    Unknown,
}
```

Retryable by default:

- `TransientTransport`
- `BitcoindWarmingUp`
- `RateLimitedOrBusy`
- `Timeout`, only for methods classified as idempotent/read-only

Not retryable by default:

- `InvalidRequest`
- `InvalidResponse`
- `MethodNotFound`
- `Auth`
- `ConsensusOrValidation`
- `NotFound`, unless the method explicitly marks not-found as possibly racey

Most Strata calls to Bitcoin Core are read-only and idempotent: `getblockhash`, `getblockheader`, `getblock`, `getrawtransaction`, `getbestblockhash`, `getblockchaininfo`. These can retry on transport errors and warmup errors.

Submission methods, especially transaction submission, need stricter handling. Retrying `sendrawtransaction` after a transport timeout can duplicate submission attempts. That may be operationally acceptable in Bitcoin because transaction relay is content-addressed, but the caller should opt in and handle “already known” or mempool rejection explicitly. We should not bake this into the generic default.

### Caller API

The ergonomic API should preserve simple calls while allowing explicit policy:

```rust
client.call(method, params).await?;

client
    .call_with_policy(method, params, RpcCallPolicy {
        timeout_mode: TimeoutMode::TotalBudget(Duration::from_secs(3)),
        retry: RetryMode::ReadOnlyDefault,
        priority: RpcPriority::Normal,
    })
    .await?;
```

I do not propose priority scheduling in the first implementation beyond reserving the enum. If we add it later, likely priorities are `Health`, `ProtocolCritical`, `Normal`, and `BulkBackfill`. For now, queue order should be FIFO.

### Backpressure behavior

Queue saturation is not an internal implementation detail. It is a signal that the Strata node is asking more of Bitcoin Core than the configured dependency budget allows.

Callers must handle `QueueFull` distinctly from `Timeout` and `Transport`. For example:

- A background block backfill task should pause and retry later.
- A health check should report degraded dependency state.
- A protocol-critical derivation path may propagate an explicit dependency-unavailable error.
- Bridge monitoring should avoid spawning more work on top of saturation.

person:MdTeach’s caller map should identify each call site and assign one of these behaviors. person:storopoli, person:delbonis, and person:alexhui01 should review the protocol call sites where backpressure changes could expose old assumptions.

### Observability

The client should emit metrics:

- `bitcoind_rpc_queue_depth`
- `bitcoind_rpc_in_flight`
- `bitcoind_rpc_requests_total{method,status,error_class}`
- `bitcoind_rpc_retries_total{method,error_class}`
- `bitcoind_rpc_latency_seconds{method,status}`
- `bitcoind_rpc_queue_wait_seconds{method}`
- `bitcoind_rpc_timeouts_total{method,timeout_mode}`
- `bitcoind_rpc_queue_full_total{method}`

Tracing spans should include:

- method
- timeout mode
- attempt count
- queue wait duration
- execution duration
- final error class
- whether retry budget was exhausted

This is intentionally enough for person:krsnapaudel and infra to distinguish “Bitcoin Core down” from “Strata overloaded the RPC client” from “a specific method is slow.”

### Testing

We need tests that stop relying only on clean mocks.

Unit tests:

- Queue full returns typed `QueueFull`.
- In-flight limit is respected under concurrent calls.
- Total timeout budget includes queue wait and retries.
- Per-attempt timeout can exceed total wall-clock relative to one attempt.
- Retry classifier does not retry invalid requests or auth failures.
- Read-only methods retry transient transport failures.

Integration tests:

- Start `bitcoind`, issue calls, stop it, continue issuing calls, restart it, verify recovery.
- Simulate warmup responses.
- Simulate slow RPC response and verify timeout metrics.
- Run concurrent block/header calls above `max_in_flight` and verify bounded memory/task behavior.

Regtest mocks in `repo:alpen` should be extended, but we should also keep at least one real `bitcoind` restart test. The restart behavior is the thing the mocks have repeatedly hidden.

### Rollout

Phase 1: Add the managed execution layer behind existing APIs with default limits high enough to avoid surprising callers.

Phase 2: Add metrics and tracing, then run Strata node tests with instrumentation.

Phase 3: Lower defaults to production-intended values and update callers that mishandle `QueueFull`, `Timeout`, or retry exhaustion.

Phase 4: Require explicit `RpcCallPolicy` for any method that is non-read-only or uses `NoClientTimeout`.

## Drawbacks

This adds complexity to a crate that was previously mostly a wrapper. The complexity is justified, but it means `repo:bitcoind-async-client` becomes policy-bearing infra rather than a neutral transport adapter.

Bounded queues can surface bugs that were previously hidden by unbounded waiting. This may delay initial release work if protocol callers are not ready to handle backpressure. That said, hiding those bugs until production is worse.

Retries can mask real dependency degradation if observability is weak. This is why metrics are part of the design, not follow-up polish.

Timeout defaults are hard to choose globally. A value that is right for chain tip polling may be wrong for historical block fetches. We should expect method overrides and caller-specific policies to grow.

## Alternatives considered

### Keep the client thin and push policy to callers

This keeps ownership clean but fails in practice. Protocol callers will implement inconsistent timeout and retry behavior, and infra will still be paged when the dependency layer overloads. Centralizing the policy gives us one place to audit.

### Use only a semaphore, no queue

A semaphore bounds in-flight RPCs, but callers waiting on the semaphore can still accumulate without an explicit queue limit. We need both: bounded execution and bounded waiting.

### Fail immediately when in-flight is full

This is simpler and gives strong backpressure, but it is too harsh for normal Strata bursts. A small bounded queue absorbs harmless bursts while still preventing unbounded buildup during outages.

### Infinite retries for read-only methods

Read-only does not mean harmless. Infinite retries can block protocol tasks forever and hide dependency failure. Retry budget exhaustion must be visible.

### Rely on Bitcoin Core’s own work queue limits

Bitcoin Core limits its own RPC handling, but that does not protect the Strata process from unbounded local tasks, queueing, and caller confusion. We need local limits before requests reach Bitcoin Core.

## Open questions

1. What are the production defaults for `max_in_flight` and `max_queue_depth`?
   I propose `16` and `256`, but person:krsnapaudel should validate against expected node hardware and deployment topology.

2. Which methods require custom timeout overrides?
   person:MdTeach’s caller map should identify long-running historical fetches, health checks, bridge-related calls, and protocol-critical calls separately.

3. How should bridge callers treat transaction submission retries?
   person:Rajil1213, person:ProofOfKeags, and person:uncomputable should review whether `sendrawtransaction` retry policy belongs in this crate or bridge-specific code.

4. Should queue wait count against total timeout?
   I propose yes. If a request waits too long before execution, the caller has already missed its dependency budget.

5. Do we need priority queues in the first release?
   I propose no. We should instrument FIFO first and add priority only if health checks or protocol-critical calls are demonstrably starved by bulk work.

6. Where should method classification live?
   It can live in `repo:bitcoind-async-client`, but `repo:strata-common` may already define method-like abstractions that should not be duplicated. person:prajwolrg and person:bewakes should decide this during implementation review.

7. What is the minimum real-`bitcoind` integration test set for release?
   At least restart recovery and slow response timeout should block release. Reindex and prune-mode behavior can follow if setup cost is high.
