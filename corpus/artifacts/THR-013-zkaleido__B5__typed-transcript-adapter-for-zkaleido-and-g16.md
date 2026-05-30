## Goal

Define and ship a typed transcript adapter for `repo:zkaleido` and `repo:g16` that removes ad hoc transcript plumbing from prover and verifier paths, while preserving the delivery constraints coming from `product:glock` and `product:strata`.

The immediate target for B5 is practical: make transcript behavior explicit enough that `person:prajwolrg` can continue the draft `repo:alpen` integration without discovering missing Fiat-Shamir hooks late in the cycle. The adapter should support single proof verification now and expose the shape needed for batch verification, even if not every backend has an optimized batch path in the first merge.

Ownership for this document is `person:mukeshdroid`, with implementation review from `person:AaronFeickert`, `person:storopoli`, and `person:Zk2u`.

## Non-goals

This is not a redesign of the proving system API across all research code. We are deliberately narrowing the surface to what `product:glock` and `product:strata` need.

This is not a new transcript primitive. We are not changing the hash function, sponge construction, domain separation policy, or Fiat-Shamir security argument unless the existing plumbing is shown to be unsound.

This is not a benchmark claims cleanup. The typed adapter should make benchmarks easier to interpret by reducing accidental variance from transcript misuse, but `repo:hash-benchmarks` instability is tracked separately.

This is not a full batch-verification implementation for every curve and proof shape. The goal is to avoid painting ourselves into a corner and to provide the hooks needed by `repo:alpen`.

## Background

`repo:zkaleido` currently exposes proving backend flexibility that was useful for research iteration but awkward for integration. Transcript state is threaded through call sites in several styles: raw byte append calls, backend-specific challenge derivation, and helper functions that implicitly choose labels or serialization. That made it easy to prototype, but hard to review.

For `repo:g16`, this is especially sensitive because Fiat-Shamir challenges bind the public inputs, commitments, evaluation claims, and proof elements. If the prover and verifier paths serialize the same conceptual value differently, verification can fail silently in integration or, worse, accept a proof under an unintended statement encoding. In the Bitcoin/ZK setting, this matters because `product:glock` and `product:strata` both rely on stable statement commitments across independently versioned repositories.

During B5, `person:AaronFeickert` and `person:mukeshdroid` replaced the most obvious ad hoc transcript plumbing with a typed adapter. At roughly the same time, `person:prajwolrg` opened the draft `repo:alpen` integration PR. That PR found two gaps immediately:

1. Batch verification needs to absorb multiple proof instances under a single domain-separated transcript schedule.
2. Existing examples encoded transcript inputs too loosely, so integration changes broke examples that were relying on local helper behavior.

The tension is familiar: research API generality keeps wanting a broad abstraction over transcript backends and proof systems, while Glock and Strata need a narrow, stable path that can be released without cross-repo bunching.

## Proposed design

Introduce a typed transcript adapter with three layers:

1. `TranscriptSink`: a minimal trait over the underlying transcript implementation.
2. `TranscriptEncoding`: typed serialization rules for field elements, group elements, scalars, public inputs, commitments, and proof messages.
3. `ProofTranscript`: proof-system-specific sequencing for `repo:g16` and the relevant `repo:zkaleido` backend paths.

The important constraint is that raw transcript mutation should not be available from normal prover or verifier code. Call sites should not append arbitrary bytes with local labels. Instead, they call typed methods such as:

- `absorb_statement(statement_id, public_inputs)`
- `absorb_commitment(round, commitment)`
- `challenge_scalar(label)`
- `absorb_evaluation_claim(point, value)`
- `absorb_proof_instance(instance_index, proof_header)`

The names above are illustrative, but the structure should be enforced in code. The adapter owns label construction and domain separation. Call sites own only the typed values.

For `repo:g16`, define a canonical transcript schedule for single proof verification:

1. Initialize transcript with protocol domain, backend identifier, curve identifier, and transcript version.
2. Absorb statement metadata: constraint system digest, public input digest, and any verifying key commitment used by the integration.
3. Absorb proof commitments in round order.
4. Derive challenges only through typed challenge methods.
5. Absorb evaluation claims and opening data.
6. Derive final verifier randomness, if needed for aggregation or deferred checks.

For batch verification, extend the schedule without duplicating logic. The batch verifier should initialize a batch domain, then absorb each instance with an explicit `instance_index` and per-instance statement digest before deriving batch weights. The adapter should make it impossible to accidentally reuse a single-proof transcript schedule for a batch context. This is the hook that `person:prajwolrg` found missing in the `repo:alpen` integration.

Serialization must be deterministic and versioned. Field elements use canonical fixed-width encoding modulo the field. Group elements use the existing compressed canonical encoding, with subgroup and infinity handling kept outside the transcript adapter unless the existing backend already validates during decoding. The adapter should not silently normalize invalid proof material.

Errors should distinguish encoding failure, invalid transcript state, and unsupported batch mode. `product:strata` integration code should be able to surface these as deterministic verification failures rather than backend panics.

Implementation split:

- `person:mukeshdroid` and `person:AaronFeickert`: adapter trait, transcript schedule, and test vectors.
- `person:storopoli`: review backend compatibility in `repo:zkaleido`.
- `person:Zk2u`: check infra implications for reproducible benchmark runs.
- `person:prajwolrg`: validate the `repo:alpen` draft integration against the new hooks.
- `person:Hakkush-07`, `person:ceyhunsen`, and `person:cyphersnake`: review typed encoding choices where they touch research assumptions.

## Trade-offs

The main trade-off is reduced API generality. A fully generic transcript interface would let each backend define arbitrary absorb/challenge flows. That is attractive for research, but it is exactly what made the current integration path fragile. The typed adapter narrows what call sites can express, which is acceptable because the immediate users are Glock and Strata.

A second trade-off is versioning overhead. Adding protocol domain, backend identifier, curve identifier, and transcript version to the initial state creates more ceremony. The benefit is that cross-repo integration failures become explicit. If `repo:g16` and `repo:alpen` disagree about a transcript version, verification should fail clearly instead of drifting through helper behavior.

Batch support also adds design surface before all optimized batch paths exist. The alternative is to keep single verification clean and add batch later. We already tried that implicitly, and the `repo:alpen` draft PR found the missing hooks at the integration boundary. Adding the hook now is cheaper than reshaping verifier APIs during release work.

The adapter does not solve benchmark instability by itself. It may reduce one source of accidental variation, but performance claims still need pinned inputs, fixed backend versions, and reproducible runner configuration in `repo:hash-benchmarks`.

## Rollout plan

Phase 1 is adapter landing in `repo:zkaleido` and `repo:g16`. Replace direct transcript mutation in the primary prover and verifier paths. Keep compatibility shims only where examples or tests need staged migration, and mark them for removal.

Phase 2 is deterministic test vectors. Add tests that assert prover and verifier transcript schedules match exactly for representative Glock and Strata statements. Include negative tests for label mismatch, public input order mismatch, and single-versus-batch domain mismatch.

Phase 3 is `repo:alpen` integration. `person:prajwolrg` should rebase the draft PR onto the adapter branch and wire the batch-verification hooks even if the first implementation internally verifies instances one at a time. The public API should not need another shape change when optimized batching lands.

Phase 4 is examples and docs cleanup. Broken examples should be updated to use typed transcript construction rather than local byte helpers. Any example that cannot be updated cleanly is evidence that the adapter surface is missing a real typed operation.

Phase 5 is release coordination. `person:storopoli`, `person:Zk2u`, and `person:krsnapaudel` should confirm that dependency versions across `repo:zkaleido`, `repo:g16`, and `repo:alpen` can be pinned before the next integration window. The success condition is boring: no transcript-specific integration surprises during the next Glock or Strata release candidate.
