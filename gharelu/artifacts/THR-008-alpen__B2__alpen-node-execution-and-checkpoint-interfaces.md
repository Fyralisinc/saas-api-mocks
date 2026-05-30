# RFC: alpen Node Execution and Checkpoint Interfaces

## Summary

This RFC proposes the first stable interface boundary for the `alpen` node execution path, covering block import, L1 checkpoint ingestion, and state transition execution. The goal is not to freeze all protocol internals. The goal is to define the narrow set of types and traits that downstream consumers can rely on while protocol work continues.

The proposed boundary has three layers:

1. `BlockImport`: validates ordering, availability, and metadata for Strata blocks before execution.
2. `StateTransition`: applies an execution input against a prior state commitment and returns a deterministic transition output.
3. `CheckpointProvider`: normalizes L1 checkpoint data into a canonical internal representation consumed by node execution, bridge clients, and explorer indexing.

This should live primarily in `alpen`, with shared primitive types moved into `strata-common` only when they are truly cross-repo contracts. The immediate consumers are the Strata node, bridge-side monitoring, and `checkpoint-explorer`. `bitcoind-async-client` remains an adapter for Bitcoin RPC data, not a source of protocol vocabulary.

The intended first reviewers are person:delbonis, person:bewakes, person:MdTeach, and person:storopoli. I expect person:Rajil1213 to review bridge-facing checkpoint semantics once the initial trait shape is agreed.

## Motivation

The current node work is exposing the same problem in several places: protocol implementation details are leaking into repo-level integration boundaries before we have named the ownership of those types. In practice, this means bridge and explorer code are forced to depend on unstable structs, or each repo invents its own version of “checkpoint,” “block reference,” or “transition output.”

This is already showing up in three ways.

First, checkpoint naming differs across repos. In one place a checkpoint means an L1-published commitment. In another, it means a locally observed block height plus metadata. In another, it means the state root after applying a batch. These are related but not equivalent, and the ambiguity makes review harder than the code warrants.

Second, the review queue around consensus traits is growing because we are mixing two questions: what does the node do internally, and what is the stable protocol-facing interface? person:MdTeach has been reviewing invariants around state transitions, but several review comments are really about interface boundaries rather than transition correctness.

Third, bridge and explorer consumers need stable outputs before protocol internals are settled. person:Rajil1213 and the bridge team should not need to track every execution-engine refactor just to parse checkpoint state. Likewise, `checkpoint-explorer` should index canonical checkpoint events without knowing how the node currently models execution state.

This RFC aims to unblock the B2 milestone by agreeing on interface names, type ownership, and determinism requirements. The milestone has already slipped one week; pushing all of this into implementation PRs will make the slip worse.

## Detailed Design

### Ownership Rules

The default rule is: keep concrete node orchestration types in `alpen`; move only durable, cross-repo protocol primitives into `strata-common`.

`strata-common` should own:

- hashes, block IDs, and commitment wrappers with explicit domains;
- checkpoint IDs and checkpoint payloads that are emitted across repo boundaries;
- serialization formats that bridge, explorer, and node components must agree on;
- error enums only when the exact error is part of a public machine-readable contract.

`alpen` should own:

- node-local import queues;
- execution scheduling;
- storage handles;
- retry state;
- internal validation context;
- adapter traits for Bitcoin RPC and local databases.

`bitcoind-async-client` should not grow Strata protocol semantics. It should expose Bitcoin block headers, transactions, chain tips, and RPC status in Bitcoin-native terms. `alpen` should translate those into checkpoint-relevant inputs.

### Core Types

We should introduce the following canonical types.

```rust
pub struct StrataBlockRef {
    pub block_id: StrataBlockId,
    pub parent_id: StrataBlockId,
    pub height: u64,
}

pub struct L1CheckpointRef {
    pub checkpoint_id: CheckpointId,
    pub l1_block_hash: BitcoinBlockHash,
    pub l1_height: u32,
    pub txid: BitcoinTxid,
    pub output_index: u32,
}

pub struct CheckpointPayload {
    pub strata_block: StrataBlockRef,
    pub state_root: StateRoot,
    pub batch_root: BatchRoot,
    pub proof_commitment: ProofCommitment,
}
```

The names matter. `L1CheckpointRef` is the Bitcoin anchoring location. `CheckpointPayload` is the committed Strata data. `CheckpointId` is derived from the canonical serialized payload plus anchoring location if we decide L1 location must be identity-forming. That is an open question below.

We should avoid names like `Checkpoint`, `BlockCheckpoint`, and `StateCheckpoint` without qualifiers. They are too easy to misuse.

### Block Import Interface

`BlockImport` is responsible for admitting Strata blocks into the node’s execution path. It does not execute state transitions and does not decide finality.

```rust
pub trait BlockImport {
    type Error;

    fn import_block(&mut self, block: RawStrataBlock) -> Result<BlockImportOutcome, Self::Error>;
}
```

`BlockImportOutcome` should distinguish:

- already known block;
- accepted pending parent;
- accepted ready for execution;
- rejected malformed;
- rejected consensus-invalid.

The important detail is that “accepted” does not imply executed. This lets us ingest blocks in the presence of minor ordering issues while keeping execution deterministic.

Block import must validate:

- block ID matches canonical encoding;
- parent reference is present;
- declared height is parent height plus one when parent is known;
- block timestamp and metadata are within consensus-defined bounds once those bounds are available;
- payload commitments are structurally valid.

It must not validate:

- full state transition correctness;
- proof validity beyond structural commitment checks;
- L1 checkpoint inclusion.

Those checks belong downstream.

### State Transition Interface

`StateTransition` is the deterministic execution boundary. Given the same previous commitment, block payload, and execution context, all honest nodes must return the same output or the same consensus error.

```rust
pub trait StateTransition {
    type Error;

    fn apply(
        &self,
        prior: StateCommitment,
        input: TransitionInput,
    ) -> Result<TransitionOutput, Self::Error>;
}
```

`TransitionInput` should contain the Strata block payload, relevant protocol parameters, and any L1-derived data already normalized by checkpoint ingestion. It should not contain open database handles, wall-clock time, network clients, or mutable global config.

`TransitionOutput` should include:

- new `StateRoot`;
- emitted withdrawal or bridge-relevant commitments;
- consumed L1 checkpoint references, if any;
- execution receipts needed by explorer indexing;
- deterministic logs only if we commit to their format.

person:MdTeach should review the exact split between consensus errors and operational errors. My current view is that malformed input, invalid witness data, and invalid transition semantics are consensus errors; storage read failure and missing local data are operational errors. We should not collapse these into a single `anyhow::Error` at the trait boundary.

### L1 Checkpoint Interface

`CheckpointProvider` normalizes Bitcoin-observed data into canonical checkpoint candidates.

```rust
pub trait CheckpointProvider {
    type Error;

    fn checkpoint_at(
        &self,
        l1_ref: L1CheckpointRef,
    ) -> Result<Option<CheckpointPayload>, Self::Error>;
}
```

This interface intentionally takes an `L1CheckpointRef` rather than a bare height. Bitcoin heights are not stable identifiers under reorgs. Consumers that want “latest known checkpoint” can use a higher-level scanner, but execution should consume explicit anchoring references.

The provider may be backed by `bitcoind-async-client`, a local index, or test fixtures. The trait should not expose where the data came from.

For `checkpoint-explorer`, the stable emitted record should be:

```rust
pub struct IndexedCheckpoint {
    pub l1_ref: L1CheckpointRef,
    pub payload: CheckpointPayload,
    pub observed_at_l1_height: u32,
    pub confirmation_depth: u32,
}
```

Explorer code can add presentation fields, but these four fields should be enough for bridge monitoring and node reconciliation.

### Serialization and Versioning

All cross-repo checkpoint payloads must use canonical serialization. We should add an explicit version byte or version field to `CheckpointPayload` before it becomes externally consumed. If we defer versioning until after explorer and bridge integrations exist, migration will be more painful.

The version should describe the payload schema, not the node software version. Protocol parameter changes should be represented either inside the payload or by reference to a known parameter set.

### Testing Requirements

The initial implementation should include fixture-based tests shared across `alpen` and `strata-common`.

Minimum fixtures:

- valid checkpoint payload with canonical ID;
- same payload at different L1 refs, if identity excludes L1 location;
- malformed payload encoding;
- block import with missing parent;
- block import with known parent;
- deterministic transition output for a small synthetic block.

person:bewakes and person:delbonis should own the node-side fixture shape. person:krsnapaudel should review CI placement once the fixtures are used across repos, because CI reliability has lagged behind feature work and we should avoid adding brittle cross-repo test dependencies.

## Drawbacks

This introduces interface work before all protocol internals are settled. Some trait methods may change as execution matures, and there is a risk of overfitting to today’s node architecture.

It also creates more named types. That is intentional, but it adds overhead. A poorly maintained `strata-common` can become a dumping ground for “things two repos happen to import.” We should enforce the ownership rule above during review.

Finally, canonical serialization and fixture sharing will slow the first implementation PR. The tradeoff is worth it because the alternative is silent divergence between bridge, explorer, and node assumptions.

## Alternatives Considered

One alternative is to keep all types local to `alpen` until the node is more mature. This maximizes implementation speed but pushes instability onto bridge and explorer consumers. It also makes later extraction harder because the internal names will already encode local assumptions.

Another option is to move all shared-looking types into `strata-common` immediately. This is worse. It creates a fake sense of stability and makes protocol cleanup harder. Only protocol primitives and cross-repo payloads should move.

A third option is to define checkpoint interfaces around Bitcoin height rather than explicit L1 references. This is simpler for scanning but wrong for execution semantics under reorgs. Height-based APIs can exist for indexing convenience, but the execution boundary should use explicit block hash and transaction location.

## Open Questions

Should `CheckpointId` include the L1 anchoring location, or should it identify only the Strata payload? I lean toward payload-only identity plus separate `L1CheckpointRef`, but bridge monitoring may prefer location-bound identity.

Do we need separate `PendingCheckpointPayload` and `FinalizedCheckpointPayload` types, or is confirmation depth sufficient metadata?

Which transition outputs must be committed protocol artifacts versus node-local receipts? person:AaronFeickert and person:storopoli should weigh in before we expose proof-related commitments across repos.

Where should the first canonical fixtures live: `strata-common` test data, `alpen` integration fixtures, or a small shared fixture crate?

How strict should `BlockImport` be before state execution exists? If we reject too early, we risk duplicating transition validation. If we accept too much, downstream errors become harder to interpret.

What is the minimum checkpoint schema needed by person:Rajil1213 and bridge consumers during this milestone, and what can wait until the bridge integration path is more concrete?
