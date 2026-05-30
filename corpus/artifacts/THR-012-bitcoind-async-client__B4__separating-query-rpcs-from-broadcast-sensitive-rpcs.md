## Goal

Separate the `bitcoind-async-client` RPC surface into two explicit classes:

1. **Query RPCs**: safe to retry automatically under transport failures, timeout expiry, and reconnect.
2. **Broadcast-sensitive RPCs**: not safe to retry blindly because the call may have committed side effects in `bitcoind` even if the client did not observe the response.

The immediate target is Strata node operations that depend on Bitcoin Core for chain tracking, wallet inspection, and transaction submission. The specific bug found in review by person:storopoli is that our generic retry layer can duplicate wallet-broadcast calls when a request times out after `bitcoind` accepts the transaction but before the JSON-RPC response reaches the caller. That is acceptable for `getblockhash`, `getblockheader`, `getrawtransaction`, `getmempoolentry`, etc. It is not acceptable for `sendrawtransaction`, wallet `send*` calls, or anything that mutates node/wallet state.

The design goal is to make unsafe retries hard to express in the API, not just documented as caller discipline.

## Non-goals

This does not redesign Strata’s transaction construction, fee policy, wallet ownership model, or bridge signing flow.

This does not add exactly-once semantics for Bitcoin transaction broadcast. Bitcoin Core does not give us exactly-once RPC semantics over HTTP. The best we can do is classify calls correctly, make retry behavior explicit, and expose enough result state for callers to reconcile.

This does not require replacing the existing async transport, JSON-RPC serialization, or connection pool.

This does not make regtest mocks fully production-equivalent. It does, however, require tests that simulate production-like restart, timeout, and partial-response behavior.

## Background

The current `bitcoind-async-client` was shaped as an infra-owned crate with protocol-owned callers. person:krsnapaudel owns most of the operational shape: async transport, timeouts, reconnect behavior, and compatibility with our deployed `bitcoind` instances. person:prajwolrg, person:MdTeach, person:delbonis, person:bewakes, and person:alexhui01 consume it from Strata protocol code where RPC failures become consensus-adjacent operational failures.

The original abstraction treated “Bitcoin RPC call” as one class. That made call sites simple, but it hid an important distinction. Some methods are pure reads from the node’s current view. Retrying them after a network failure is usually the right thing to do. Other methods can change state or cause external propagation. Retrying those calls can produce confusing or harmful behavior.

The concrete review finding in B4 was in the retry layer: a wallet-broadcast call could be sent, accepted by `bitcoind`, and then retried if the response was lost or timed out. Depending on the method and error mapping, the second attempt could return `txn-already-in-mempool`, `already known`, `insufficient funds`, or a wallet conflict. If the caller only sees the second error, it can misclassify a successful broadcast as a failed one.

Regtest hid this because our mocks generally model RPC calls as atomic request/response operations. They did not model the important failure mode: `bitcoind` performs the operation, then the client loses the response. Restart behavior also hid issues because regtest nodes come back cleanly and quickly, while production-like nodes may have wallet rescans, mempool reload, cookie rotation, and slower HTTP accept loops.

## Proposed Design

Introduce two client traits and make retry policy part of the type boundary.

```rust
trait ChainQueryClient {
    async fn get_block_hash(&self, height: u64) -> Result<BlockHash>;
    async fn get_block_header(&self, hash: BlockHash) -> Result<BlockHeader>;
    async fn get_raw_transaction(&self, txid: Txid) -> Result<Option<Transaction>>;
    async fn get_mempool_entry(&self, txid: Txid) -> Result<Option<MempoolEntry>>;
}

trait BroadcastClient {
    async fn send_raw_transaction_once(
        &self,
        tx: Transaction,
        opts: BroadcastOptions,
    ) -> Result<BroadcastOutcome>;
}
```

The exact method set can evolve, but the division should be stable:

Query methods use the existing bounded retry policy: reconnect on transport failure, retry on timeout where the request is classified as read-only, and preserve the current tracing spans. These calls may still fail after the retry budget is exhausted, but they should not require every Strata caller to implement duplicate retry loops.

Broadcast-sensitive methods use single-attempt transport semantics by default. A timeout or connection reset after write returns `BroadcastOutcome::Unknown { txid, cause }`, not a normal failure. The caller must then reconcile by querying mempool, wallet transaction state where applicable, or chain inclusion. This makes the ambiguous state explicit.

`BroadcastOutcome` should distinguish:

```rust
enum BroadcastOutcome {
    Accepted { txid: Txid },
    AlreadyKnown { txid: Txid },
    Rejected { txid: Txid, reason: BroadcastRejectReason },
    Unknown { txid: Txid, cause: RpcUncertainty },
}
```

`AlreadyKnown` is not an error for Strata broadcast paths. If we broadcast a transaction and Bitcoin Core says it already has it, the node should treat that as converged state unless policy requires checking exact transaction bytes. `Rejected` is a terminal policy or validation failure. `Unknown` is a reconciliation-required state.

For wallet RPCs, we should be stricter. Calls that create, fund, sign, or send wallet transactions should not sit behind the same trait as chain queries. If Strata needs wallet-backed methods in this crate, they should live behind a `WalletMutationClient` with no automatic retry and explicit idempotency keys only where Bitcoin Core supports a stable identifier. In most Strata paths, I would prefer we avoid wallet mutation RPCs entirely and submit fully constructed raw transactions.

Implementation steps:

- person:krsnapaudel adds the retry classifier inside `bitcoind-async-client`, with method metadata represented as an enum rather than string matching at the call site.
- person:storopoli and person:prajwolrg review the initial method classification for protocol impact.
- person:MdTeach and person:delbonis update Strata callers to depend on `ChainQueryClient` or `BroadcastClient` instead of the broad client.
- person:bewakes and person:alexhui01 help remove downstream retry loops that become redundant or unsafe.

Observability should also follow the split. Query retries should emit retry count, method, elapsed time, and final status. Broadcast calls should emit whether the request reached the transport write phase, whether the response was observed, and whether the outcome is accepted, rejected, already-known, or unknown. This gives ops enough signal without requiring logs to infer semantic ambiguity.

## Trade-offs

The main cost is API churn. Existing callers that accepted a generic client now need narrower bounds. This is annoying during the Strata milestone window, but it is the right kind of churn: it forces each caller to state whether it is reading chain state or attempting an externally visible mutation.

The second cost is more reconciliation code. A broadcast timeout can no longer be treated as “retry and hope.” Callers need to ask “is this tx in mempool, in a block, or absent?” That makes code longer, but it matches the Bitcoin operational reality.

The third cost is that the method classifier can become stale as we add RPCs. We should make adding an unclassified RPC impossible in the typed wrapper. Raw escape hatches can exist for tests and diagnostics, but production Strata code should not use them without review.

The benefit is that we eliminate a class of duplicate side-effect bugs at the crate boundary. We also get better incident behavior: if `bitcoind` restarts during a broadcast, Strata records an unknown broadcast state and reconciles, rather than converting ambiguity into a misleading failure.

## Rollout Plan

Phase 1: Add the new traits and outcome types in `bitcoind-async-client` while keeping the old broad client available behind a deprecated interface. Add tests for response-lost-after-accept using a fake transport that commits a broadcast and drops the response.

Phase 2: Migrate Strata chain index, block polling, and proof input fetch paths to `ChainQueryClient`. These should be mostly mechanical and can retain automatic retry behavior.

Phase 3: Migrate transaction submission paths to `BroadcastClient`. Any caller that retries broadcast on timeout must be changed to handle `Unknown` and run reconciliation before deciding whether to rebroadcast.

Phase 4: Add production-like restart tests. The test should rotate cookie auth or drop HTTP connections while preserving node state, then verify that query RPCs recover and broadcast RPCs surface ambiguity.

Phase 5: Remove the deprecated broad retrying interface after person:krsnapaudel confirms no remaining Strata production caller depends on it. person:storopoli should do the final review pass specifically on method classification and broadcast outcome handling.
