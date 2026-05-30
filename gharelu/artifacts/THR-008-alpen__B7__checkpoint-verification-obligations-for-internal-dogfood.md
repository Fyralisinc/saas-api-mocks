# RFC: Checkpoint Verification Obligations for Internal Dogfood

## Summary

This RFC defines the minimum proof obligations and implementation expectations for calling the Strata checkpoint verification path ready for internal dogfood in `repo:alpen`. The goal is not to freeze the full consensus design or declare the node externally hardened. The goal is narrower: make checkpoint verification sufficiently explicit, testable, and stable that bridge, explorer, and protocol engineers can build against it without reverse-engineering intent from code.

The current checkpoint path works in the common case, but the obligations it enforces are not written down at the same precision as the implementation. This has already caused confusion across `repo:alpen`, `repo:strata-common`, `repo:bitcoind-async-client`, and `repo:checkpoint-explorer`. In particular, consumers need to know which Bitcoin headers, Strata state commitments, transition proofs, and ancestry relations are actually checked by the node, and which assumptions remain outside the verifier.

This RFC proposes that before wider internal use, we treat checkpoint verification as a boundary with a small, documented contract:

1. A checkpoint must bind to an identified Bitcoin L1 context.
2. A checkpoint must bind to an ordered Strata state transition.
3. The verifier must reject equivocation at the same checkpoint height unless explicitly operating in a diagnostic mode.
4. The verifier must expose stable, typed outputs for bridge and explorer consumers.
5. The proof obligation notes must use terminology aligned with the whitepaper and research notes.

The primary audience is `person:prajwolrg`, `person:MdTeach`, `person:delbonis`, `person:bewakes`, `person:Rajil1213`, and downstream consumers in bridge and explorer work.

## Motivation

The node maturation arc has repeatedly exposed a mismatch between implementation velocity and contract clarity. The protocol implementation has advanced quickly, but the surrounding repos have had to infer ownership and semantics for shared types. This is manageable while everything is local, but it becomes brittle once the bridge and checkpoint explorer depend on node outputs.

The immediate issue surfaced during review of the checkpoint verification path. The code contains enough structure to validate a checkpoint, but the proof obligations are scattered between implementation details, tests, and informal discussion. That makes it hard to answer basic questions:

- Does verification mean the checkpoint is valid relative to Bitcoin finality, or only well-formed relative to a provided header chain?
- Is the state root checked as a transition output, an input commitment, or both?
- Which object owns the canonical encoding for checkpoint IDs?
- If two checkpoints at the same Strata height are observed, does the node reject one, record both, or defer fork choice?
- What exactly should `repo:checkpoint-explorer` display as verified versus merely observed?

This ambiguity matters for Bitcoin/ZK protocol work because the checkpoint is the handoff between several trust domains. It touches Bitcoin header availability, proof verification, rollup state transition semantics, bridge safety, and public observability. If the node exposes a boolean named `verified` without a sharper contract, every consumer will project a different meaning onto it.

This RFC is also motivated by two reopened consensus edge cases from the audit beat:

1. Header ancestry can be locally consistent but anchored to a Bitcoin tip that is not acceptable under the node’s configured finality policy.
2. A checkpoint can carry internally consistent transition data while failing to bind cleanly to the previous accepted checkpoint under the expected Strata chain relation.

Neither issue requires redesigning the node. Both require making the verifier’s obligations explicit enough that implementation, tests, and downstream APIs agree.

## Detailed Design

### Verification Contract

A checkpoint verification result should represent the following statement:

Given the node’s configured Bitcoin view, Strata chain state, verification parameters, and accepted checkpoint history, this checkpoint is a valid next checkpoint candidate for internal consumption.

This is intentionally stronger than “the proof verifies” and weaker than “the checkpoint is globally canonical forever.”

The verifier must check four categories of obligations.

### 1. Bitcoin Context Binding

Each checkpoint must bind to a Bitcoin L1 context. At minimum this includes:

- Bitcoin block hash used as the L1 anchor.
- Bitcoin block height, if available from the header source.
- Header ancestry sufficient to connect the anchor to the node’s configured Bitcoin view.
- Confirmation or finality depth policy used by the node at verification time.

`repo:bitcoind-async-client` should remain responsible for fetching Bitcoin data, but not for interpreting Strata validity. The node verifier should receive typed Bitcoin header context and apply the policy relevant to checkpoint acceptance.

The output must distinguish:

- `observed_l1_anchor`: the anchor claimed by the checkpoint.
- `validated_l1_context`: whether the node accepted the anchor under its local Bitcoin view.
- `l1_policy`: the configured confirmation/finality rule used for the decision.

This distinction matters for `repo:checkpoint-explorer`, where we may want to show an observed checkpoint before the local node is willing to treat it as internally verified.

### 2. Strata Transition Binding

Each checkpoint must bind to a Strata state transition. The checkpoint must identify:

- Previous checkpoint ID or genesis transition marker.
- Current checkpoint ID.
- Strata height or epoch index.
- Pre-state commitment.
- Post-state commitment.
- Batch or block commitment covered by the transition.
- Proof artifact or proof reference.
- Verification key or verification key commitment.

The verifier must check that the transition proof verifies against the expected public inputs. The public inputs must include both the pre-state and post-state commitments, not just a post-state claim. If the proof system abstraction currently hides this detail, `person:MdTeach` and `person:delbonis` should make the public input mapping explicit in the verification module or adjacent notes.

For internal dogfood, it is acceptable for the proof artifact to be mocked or generated by a development prover in certain environments, but the verifier output must say so. A development proof path must not produce the same status enum as a production proof path. Proposed statuses:

- `proof_verified`
- `proof_verified_dev_mode`
- `proof_missing`
- `proof_invalid`
- `proof_unsupported`

This avoids bridge consumers accidentally treating dev-mode verification as production assurance.

### 3. Chain Relation and Equivocation Handling

The verifier must check the relation between the candidate checkpoint and the previously accepted checkpoint.

For a non-genesis checkpoint, the candidate’s `previous_checkpoint_id` must match the node’s accepted predecessor for the relevant Strata height or epoch. If it does not, the verifier must return a structured fork or equivocation result rather than a generic failure.

At minimum:

- Same height, different checkpoint ID: `equivocation_detected`.
- Unknown predecessor: `missing_predecessor`.
- Known predecessor but invalid transition relation: `invalid_transition_link`.
- Candidate extends accepted tip: `extends_tip`.
- Candidate extends known non-tip branch: `extends_noncanonical_branch`.

For internal dogfood, we should default to rejecting equivocation for accepted-node state while still allowing diagnostic storage behind a separate path. The bridge side, especially work owned by `person:Rajil1213`, should never have to inspect raw node internals to know whether a checkpoint is accepted, rejected, or merely observed.

### 4. Stable Typed Outputs

The verifier should return a typed result that can be shared through `repo:strata-common` rather than rebuilt separately by each repo. This is the main cross-repo ownership gap we need to close.

I propose a common result shape with these fields:

- `checkpoint_id`
- `strata_height`
- `previous_checkpoint_id`
- `l1_anchor`
- `l1_context_status`
- `transition_status`
- `chain_relation_status`
- `proof_status`
- `acceptance_status`
- `diagnostics`
- `verification_timestamp`
- `verifier_version`

The important field is `acceptance_status`, which should be derived from the other statuses and should have a small enum:

- `accepted`
- `rejected`
- `observed_only`
- `deferred`

The explorer should display `acceptance_status`, not invent a separate notion of validity. Bridge consumers should use `accepted` only, unless explicitly operating in a test or diagnostic configuration.

### Documentation Requirements

Before dogfood, we need a short proof obligation note checked into the repo near the verifier. It should define:

- What the checkpoint proof proves.
- What the node checks outside the proof.
- What the node assumes from Bitcoin RPC.
- What is covered by local policy rather than consensus.
- What is intentionally not checked yet.

Terminology should be aligned with the whitepaper. In particular, we should settle names for “checkpoint,” “state commitment,” “transition proof,” “L1 anchor,” and “accepted checkpoint.” I can work with `person:prajwolrg`, `person:MdTeach`, and `person:delbonis` on this note. If we keep code names different from paper names, the note should include a mapping table.

### Tests

Internal dogfood should require tests for:

- Valid checkpoint extending accepted tip.
- Invalid proof.
- Valid proof with unacceptable Bitcoin anchor.
- Unknown predecessor.
- Same-height equivocation.
- Header ancestry mismatch.
- Dev-mode proof result not producing production `accepted` unless explicitly configured.

CI has lagged behind feature work, so these tests should be cheap and deterministic. `person:krsnapaudel` should not have to debug prover instability as a prerequisite for ordinary node CI. Where full proof generation is expensive, we should use fixed fixtures or verifier mocks with clear type separation.

## Drawbacks

This adds ceremony to a path that is still evolving. Some fields may change as the protocol hardens, and shared types in `repo:strata-common` can become a coordination tax if we move too early.

It also risks over-specifying behavior before all consensus questions are closed. The two reopened edge cases show that we still have unsettled semantics around L1 policy and checkpoint ancestry. However, leaving those semantics implicit is worse for dogfood. A typed `deferred` or `observed_only` result is preferable to a vague success/failure result.

Another drawback is that bridge and explorer consumers may treat the first stable output as permanent API. We should mark this as internal dogfood API, version it, and reserve the right to change fields before external release.

## Alternatives Considered

One option is to continue with implementation-first maturation and document the verifier after the node path stabilizes. I do not recommend this. The current ambiguity is already slowing bridge and explorer work, and late documentation tends to describe accidental behavior rather than intended obligations.

Another option is to expose only raw checkpoint data and let each consumer decide what validity means. This is inappropriate for a protocol boundary. The bridge, explorer, and node should not each implement their own checkpoint acceptance semantics.

A third option is to block dogfood until production proof generation, final Bitcoin policy, and all consensus edge cases are settled. That is too strict. We can support internal use earlier if the verifier is explicit about dev-mode proofs, deferred decisions, and policy-dependent acceptance.

A fourth option is to put all shared types in `repo:alpen` and have downstream repos import from there. This would make the node repo the de facto owner of cross-repo protocol data. I prefer placing stable result types in `repo:strata-common`, with `repo:alpen` owning verification behavior.

## Open Questions

1. Should `accepted` require the Bitcoin anchor to satisfy a fixed confirmation depth, or should the finality policy remain fully configurable for internal dogfood?

2. Do we need separate statuses for “proof verified but verification key deprecated” and “proof verified under current key”? This may matter once key rotation is part of the protocol lifecycle.

3. Should equivocation evidence be persisted by default, or only when diagnostic storage is enabled?

4. Where should canonical checkpoint ID encoding live: `repo:strata-common` or the protocol crate inside `repo:alpen`?

5. How much of the verifier result should `repo:checkpoint-explorer` expose directly? My preference is to expose status, anchor, height, and diagnostics, but not low-level proof internals unless a debug mode is enabled.

6. Who owns the final terminology pass against the whitepaper? I can do the first pass, but `person:prajwolrg` and `person:storopoli` should confirm that implementation names and proof terminology are not drifting apart.
