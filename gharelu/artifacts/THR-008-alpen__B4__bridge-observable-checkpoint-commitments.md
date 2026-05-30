## Goal

Define a stable bridge-observable checkpoint commitment format for `product:strata` that can be emitted by `repo:alpen`, consumed by bridge services, and indexed by `repo:checkpoint-explorer` without exposing unstable internal checkpoint state.

The immediate goal is to unblock deposit-facing bridge work owned by person:Rajil1213 while preserving protocol flexibility for person:prajwolrg, person:MdTeach, and person:delbonis as the node internals continue to mature. The bridge needs a compact, versioned object that answers: “which L2 checkpoint does this Bitcoin-visible commitment bind to, and what deposit-relevant state is included?” It should not need to deserialize internal node structs or infer semantics from debug RPC output.

Concretely, `repo:alpen` should produce a `BridgeCheckpointCommitmentV1` value for each finalized checkpoint candidate. That value should have deterministic serialization, explicit domain separation, and fields sufficient for bridge deposit confirmation, explorer display, and later proof-circuit binding.

## Non-goals

This document does not define the full checkpoint protocol, fork-choice rule, proving schedule, or Bitcoin publication policy. It also does not decide whether checkpoint commitments are ultimately posted through an `OP_RETURN`, taproot leaf, covenant-like construction, or aggregated with other protocol metadata.

This is not a request to freeze all checkpoint internals. The protocol team should remain free to change the internal checkpoint accumulator, state transition metadata, proof receipts, and node storage layout. The design only freezes a bridge-facing commitment boundary.

This also does not define bridge withdrawal proofs, operator signing policy, peg-out batching, or emergency recovery paths. Those will consume checkpoint commitments later, but they are not specified here.

## Background

During the July 29 to August 23 implementation beat, bridge work started depending on checkpoint data before protocol internals were settled. The bridge needs to observe deposits against a checkpointed L2 state and decide whether a deposit has crossed a confirmation threshold that is meaningful for downstream bridge logic. The explorer similarly needs stable identifiers and commitment fields so it can index checkpoints without tracking every internal rename in `repo:alpen`.

Right now, the practical problem is type ownership. Some checkpoint structures live naturally in `repo:alpen` because they are node-internal. Some serialization helpers belong in `repo:strata-common`. Bitcoin client polling and publication plumbing touches `repo:bitcoind-async-client`. The explorer wants a clean JSON/RPC shape. Without a shared boundary, bridge code either imports too much protocol state or reconstructs commitments from fields that were not intended to be consensus-adjacent.

The tension is not just engineering cleanliness. If the bridge accidentally binds to the wrong checkpoint representation, we risk accepting a deposit against a value that later differs from what the prover or Bitcoin publication layer considers canonical. Conversely, if protocol waits for every internal detail to settle before exposing anything, bridge and explorer development stalls.

The right boundary is a small, versioned commitment object. Protocol owns how it is derived. Bridge owns how it interprets deposit-facing fields. Shared libraries own serialization and hash-domain rules.

## Proposed design

Add a bridge-observable checkpoint commitment type in `repo:strata-common`, with construction performed by `repo:alpen`.

Initial structure:

```text
BridgeCheckpointCommitmentV1 {
  version: u16,
  network: NetworkId,
  checkpoint_index: u64,
  l2_block_range: BlockRange,
  l2_state_root: Bytes32,
  deposit_tree_root: Bytes32,
  withdrawal_tree_root: Bytes32,
  chainwork_anchor: BitcoinWorkAnchor,
  previous_bridge_commitment: Bytes32,
  protocol_checkpoint_hash: Bytes32,
}
```

The serialized commitment hash is:

```text
bridge_checkpoint_commitment_hash =
  tagged_hash("strata.bridge_checkpoint.v1", canonical_encode(BridgeCheckpointCommitmentV1))
```

The `protocol_checkpoint_hash` field is intentionally opaque to the bridge. It binds the bridge-facing object back to the protocol checkpoint representation selected by person:prajwolrg, person:MdTeach, person:delbonis, and person:storopoli. The bridge should not parse it. It only verifies that the commitment published or returned by the node is internally consistent.

The `deposit_tree_root` is the key bridge-facing field. It must commit to all deposits recognized up to `l2_block_range.end`, using a canonical leaf format owned by bridge and protocol jointly. For V1, deposit leaves should include Bitcoin outpoint, deposit amount, destination script or account encoding, deposit transaction inclusion metadata, and a replay-protection domain. person:Rajil1213 and person:MdTeach should co-own this leaf schema because mistakes here directly affect bridge accounting.

The `chainwork_anchor` should contain the Bitcoin block hash, height, and accumulated work value used by the node when deriving deposit inclusion. We should avoid exposing “confirmations” as a committed field because it is time-dependent. Consumers can compute confirmations relative to their own Bitcoin view. What matters for the commitment is the exact Bitcoin chain point the node used.

The `previous_bridge_commitment` field gives bridge and explorer consumers a simple commitment chain independent of the full protocol checkpoint graph. This lets `repo:checkpoint-explorer` show continuity and detect gaps without needing protocol fork-choice details. It also gives bridge services a cheap sanity check when following the finalized checkpoint stream.

`repo:alpen` should expose this value in two places:

1. Node RPC: `strata_getBridgeCheckpointCommitment(checkpoint_index | latest_finalized)`.
2. Event stream or indexed storage path used by bridge services, initially behind an unstable RPC namespace if needed.

The JSON form should use hex strings for fixed bytes, decimal strings for large integer work values, and explicit network names. The binary canonical encoding should be the only hash input. JSON is for transport and debugging only.

Ownership split:

- person:Rajil1213 owns bridge consumer requirements and deposit leaf review.
- person:prajwolrg owns integration into `repo:alpen` checkpoint production.
- person:MdTeach and person:delbonis review protocol binding and internal/external separation.
- person:krsnapaudel reviews CI coverage and cross-repo dependency shape.
- person:AaronFeickert reviews domain separation and commitment ambiguity.
- person:bewakes and person:storopoli review serialization ergonomics in `repo:strata-common`.

## Trade-offs

Freezing a V1 bridge-facing type now creates migration burden if protocol internals change. That is acceptable because the bridge needs stable semantics earlier than protocol needs fully final internals. The version byte gives us a clean V2 path, and the opaque `protocol_checkpoint_hash` prevents over-coupling.

Including both `deposit_tree_root` and `protocol_checkpoint_hash` is somewhat redundant. The redundancy is useful. The bridge can reason about deposit state directly, while protocol can still bind the object to its canonical checkpoint. This reduces the chance that explorer or bridge code starts treating internal checkpoint structs as API.

The `previous_bridge_commitment` chain may diverge from future protocol checkpoint graph concepts. That is intentional. It is a consumer-facing continuity chain, not a replacement for protocol fork choice.

The largest risk is pretending serialization is stable before it is tested across repos. We should treat canonical encoding tests as part of the type definition, not as optional coverage.

## Rollout plan

First, add `BridgeCheckpointCommitmentV1`, canonical encoding, tagged hash helpers, and test vectors to `repo:strata-common`. Test vectors should include at least one mainnet-like and one signet/regtest object. person:AaronFeickert should review the domain tag and ambiguity surface before merge.

Second, wire `repo:alpen` to construct the object from existing checkpoint state without changing checkpoint internals. If an internal field is missing, add an explicit adapter layer rather than exporting internal structs. person:prajwolrg and person:MdTeach should keep this adapter narrow.

Third, expose the RPC in `repo:alpen` and add integration tests that compare the RPC hash against the `repo:strata-common` test vector logic. person:krsnapaudel should make this part of CI before bridge consumers depend on it.

Fourth, update bridge code to consume only `BridgeCheckpointCommitmentV1` and deposit leaf proofs derived from its `deposit_tree_root`. person:Rajil1213 should remove any bridge-side dependency on internal checkpoint structs.

Fifth, update `repo:checkpoint-explorer` to index `checkpoint_index`, `bridge_checkpoint_commitment_hash`, `chainwork_anchor`, and `previous_bridge_commitment`. The explorer should display `protocol_checkpoint_hash` as an opaque binding, not as a parsed protocol object.

Finally, mark V1 as stable for bridge-facing use once `repo:alpen`, bridge consumer code, and explorer indexing all pass against the same test vectors for one full CI cycle. Any later semantic change requires `BridgeCheckpointCommitmentV2`, not silent field reinterpretation.
