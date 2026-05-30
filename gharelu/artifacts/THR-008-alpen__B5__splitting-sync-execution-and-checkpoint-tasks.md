**Goal**

Split the Strata node’s monolithic main loop into three explicit async task families: sync, execution, and checkpoint publication/serving. The immediate goal is to make retry behavior, failure isolation, and downstream API contracts tractable before the bridge and explorer integrations harden around unstable internals.

Author: person:MdTeach  
Driver: person:prajwolrg  
Review context: person:bewakes, person:delbonis, person:Rajil1213

The proposed structure should let us answer three questions independently:

1. What L1/L2 data has the node observed and validated enough to persist?
2. What state transition has the node executed from that data?
3. What checkpoint output can bridge and explorer consumers safely depend on?

This is not a rewrite of Strata consensus. It is a maturation step for `repo:alpen` that reduces accidental coupling across `repo:strata-common`, `repo:bitcoind-async-client`, and `repo:checkpoint-explorer`.

**Non-goals**

We are not changing the checkpoint proof format, DA assumptions, bridge withdrawal semantics, or Bitcoin finality policy in this design. Those remain protocol-level questions owned separately by person:prajwolrg, person:delbonis, person:storopoli, and research reviewers such as person:AaronFeickert.

We are not introducing a new networking layer or peer-to-peer gossip protocol. The node continues to derive its canonical external inputs from Bitcoin RPC and existing Strata data sources.

We are not making the checkpoint API maximally generic. The point is a stable consumer-facing contract, not a universal internal state inspection interface.

We are not solving all cross-repo type ownership issues here. We should, however, stop adding new ad hoc conversions at task boundaries. Where the design exposes shared domain types, we should move those types into the owning crate deliberately instead of copying structs between repos.

**Background**

The first internal node loop was intentionally simple: poll Bitcoin, derive relevant Strata inputs, execute pending work, update local state, and emit checkpoint data from the same control flow. That got us to early integration quickly, but it made every retry path ambiguous. A failed Bitcoin RPC call, a transient execution error, and an unavailable checkpoint sink all looked like “loop failed; sleep; try again.”

Early tests made this worse. Retrying the whole loop after a checkpoint write failure could re-enter sync code that had already advanced local cursors. Retrying after execution failure could leave checkpoint state stale but apparently available to bridge consumers. We also saw that bridge and explorer callers needed stable outputs before the internal state model felt settled. person:Rajil1213 was blocked on knowing which checkpoint fields were authoritative enough for bridge-side integration, while `repo:checkpoint-explorer` needed an API that would not move every time execution structs changed.

The current shape also caused three operational problems.

First, observability was too coarse. Metrics told us the node loop was lagging, but not whether it was lagging on Bitcoin sync, state execution, checkpoint derivation, or output serving.

Second, CI instability hid real regressions. Because the loop mixed concerns, integration tests had to stand up too much surface area to test one behavior. This made failures noisy and rebases painful across the three open PRs in this beat.

Third, type boundaries were backwards. Internal execution structs leaked outward because there was no clear checkpoint task boundary. That forced consumers to depend on fields that were convenient for the node, not fields that represented a stable product contract for `product:strata`.

**Proposed Design**

The node should run three long-lived task families coordinated by durable local state and typed channels, not by one shared procedural loop.

**Sync task**

The sync task owns external data ingestion. It talks to Bitcoin through `repo:bitcoind-async-client`, tracks L1 cursor state, validates headers and relevant Strata commitments, and persists normalized sync records. It does not execute state transitions and does not construct bridge-facing checkpoints.

Its core output is an append-only stream of `SyncEvent`s:

```text
BitcoinTipObserved
RelevantBlockImported
DepositCommitmentObserved
BatchDataObserved
ReorgDetected
SyncGapDetected
```

The important property is idempotence. If the task restarts at height N, replaying imported records from N-k should not duplicate execution inputs. Sync persistence should key records by Bitcoin block hash plus intra-block locator, not just height, so reorg handling can be explicit.

Retry policy belongs here for transport and indexing failures. RPC timeout, rate limiting, and temporary bitcoind unavailability should back off inside the sync task. A detected reorg is not an error; it is a sync event that downstream tasks consume.

**Execution task**

The execution task owns deterministic state transition. It consumes finalized or policy-accepted sync records, applies Strata transition rules, and persists execution results keyed by input commitment and parent state root.

Its input should not be raw Bitcoin RPC responses. It should consume the normalized records emitted by sync. That keeps Bitcoin-specific retry and reorg handling out of the execution engine.

Execution should produce:

```text
ExecutionResult {
  input_id,
  parent_state_root,
  new_state_root,
  l2_block_range,
  withdrawal_root,
  deposit_root,
  status,
}
```

The execution task may fail deterministically for invalid data or operationally for local resource problems. We should distinguish those. Invalid batch data becomes a persisted rejected result with enough context for diagnostics. Local DB lock contention or prover-side unavailability remains retryable and should not advance the execution cursor.

For now, execution should run single-writer. Parallel execution is tempting, but the state root chain gives us a natural serial dependency. We can parallelize validation later around pre-execution checks if profiling justifies it.

**Checkpoint task**

The checkpoint task owns derivation, persistence, and serving of checkpoint outputs. It consumes execution results and emits stable checkpoint records for bridge and explorer consumers.

This is the key boundary introduced by the review. The checkpoint task must not expose arbitrary execution internals. Its public record should be shaped around consumer semantics:

```text
CheckpointRecord {
  checkpoint_id,
  l1_anchor,
  l2_range,
  state_root,
  withdrawal_root,
  deposit_root,
  proof_status,
  publication_status,
  created_at,
}
```

The exact field names can change during implementation, but the ownership cannot: this record is a product/API type, not a scratch execution struct. person:Rajil1213 should review it from bridge needs; person:krsnapaudel should review operational serving and persistence concerns; person:MdTeach and person:prajwolrg should keep protocol meaning tight.

Checkpoint publication is separate from checkpoint availability. A checkpoint can be locally derived and queryable before it is published or finalized on Bitcoin. The API should represent that distinction explicitly instead of overloading “latest checkpoint.”

Suggested query surfaces:

```text
GET /checkpoints/latest?min_status=derived|published|finalized
GET /checkpoints/{checkpoint_id}
GET /checkpoints/by-l1-anchor/{block_hash}
GET /health/checkpoint-lag
```

`repo:checkpoint-explorer` should depend on these stable outputs. Bridge integration should use status-gated queries and never infer finality from ordering alone.

**Task coordination**

Coordination should happen through persisted cursors plus bounded async channels. Channels are for wakeups and near-real-time flow; the DB is the source of truth. On restart, each task reconstructs its cursor from durable state.

Each task should expose independent metrics:

```text
sync_l1_height
sync_reorg_count
execution_input_lag
execution_failure_count
checkpoint_latest_derived
checkpoint_latest_published
checkpoint_publication_failure_count
```

This lets CI and staging isolate regressions. It also gives person:sapinb and infra a way to distinguish Bitcoin RPC problems from execution stalls.

**Trade-offs**

This design adds more moving parts. We will have more tasks, more cursors, and more state transition edges to test. The upside is that each edge is now named and testable. The current monolithic loop is simpler in code shape but more complex in behavior.

Persisted task boundaries may feel heavy before the node is production-grade. I think that cost is justified because bridge and explorer consumers are already treating outputs as stable. If we do not define the boundary now, we will keep shipping internal structs as accidental APIs.

Single-writer execution limits throughput. That is acceptable for this stage because correctness and restart behavior matter more than peak processing. If throughput becomes a bottleneck, we can add speculative validation or batched prefetching without changing the checkpoint contract.

The checkpoint task creates a new ownership question for shared types. We should resist defining these types independently in every repo. My recommendation is to move consumer-facing checkpoint types into `repo:strata-common` once reviewed, while keeping execution-only types in `repo:alpen`.

**Rollout Plan**

First, person:MdTeach and person:prajwolrg should land the task-boundary interfaces behind feature flags or internal modules without changing external behavior. The initial PR should define the task structs, cursors, and domain events, even if the old loop still drives them.

Second, split sync out of the monolithic loop. Tests should cover Bitcoin RPC retry, reorg event creation, cursor restart, and idempotent record import. person:bewakes and person:delbonis should review the reorg semantics closely.

Third, split execution. The execution task should consume persisted sync records and produce persisted execution results. Existing integration tests should be narrowed so execution failure does not require mocking checkpoint publication.

Fourth, introduce the checkpoint task and freeze the first bridge/explorer-facing record shape. person:Rajil1213 should validate bridge requirements before we call the API stable. `repo:checkpoint-explorer` should move to the new checkpoint endpoints as soon as they exist, even if some statuses are stubbed initially.

Fifth, remove the old monolithic loop path. This should only happen after CI has separate sync, execution, and checkpoint test jobs. person:krsnapaudel should help make those jobs reliable enough that rebasing the remaining open PRs does not keep hiding real behavior changes.

Finally, stage with metrics enabled and compare lag counters against current node behavior. We should not declare this complete when the code compiles; we should declare it complete when a restart during sync, execution, and checkpoint publication produces the expected durable state without manual repair.
