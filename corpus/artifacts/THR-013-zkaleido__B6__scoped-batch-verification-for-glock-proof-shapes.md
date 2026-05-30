# RFC: Scoped Batch Verification for Glock Proof Shapes

## Summary

This RFC proposes a deliberately scoped batch verification interface in `zkaleido` for the two Glock proof shapes currently required by Strata integration work. The goal is not to design a universal aggregation layer. The goal is to harden the proving backend around the proof objects we actually need to verify repeatedly during Glock milestones, while keeping the API narrow enough that performance, soundness assumptions, and cross-repo release behavior remain reviewable.

The proposal adds a `BatchVerifier` abstraction parameterized over an explicit `ProofShape`, with initial support for:

1. Single-statement Glock Groth16 proofs used by current bridge verification paths.
2. Homogeneous multi-statement Glock proof batches where all items share circuit identity, verifying key digest, public input layout, and transcript domain.

The interface rejects heterogeneous aggregation, mixed proving systems, and dynamic public input schemas. These can be revisited later, but they are out of scope for this RFC.

person:Hakkush-07 will own the design note and first implementation sketch. person:AaronFeickert should review the cryptographic assumptions and failure modes. person:prajwolrg should review the Strata-facing integration boundary. person:mukeshdroid should review benchmark methodology before we publish any performance claims.

## Motivation

The original batching discussion tried to cover universal aggregation across proof systems, circuit families, and recursive verification targets. That direction is attractive from a research API perspective, but it has repeatedly collided with Glock and Strata delivery needs. We need something smaller, auditable, and immediately useful.

There are three concrete problems.

First, Glock verification paths are becoming hot enough that single-proof verification is an awkward baseline. We expect batches of similarly shaped proofs during bridge-related flows, especially when Strata integration tests exercise repeated state transitions or inclusion checks. Even modest batching wins matter if they reduce verifier CPU load without complicating consensus-adjacent logic.

Second, current benchmark instability makes broad performance claims hard to review. The `hash-benchmarks`, `g16`, `zkaleido`, and `alpen` repos have enough cross-dependency movement that a universal batching API would likely produce numbers that are expensive to reproduce and easy to misinterpret. A narrower API lets us pin the exact proof shape, verifying key digest, public input encoding, and machine profile used for benchmark runs.

Third, cross-repo release work tends to bunch near integration windows. If batching requires simultaneous broad changes in `zkaleido`, `g16`, `strata`, and downstream harnesses, we will create another integration risk. A scoped batch verifier can land behind feature flags and be exercised first in `zkaleido` fixtures, then Glock integration tests, then Strata-facing code.

The design bias here is intentionally practical: we trade generality for reviewability.

## Detailed design

### Terminology

A `ProofShape` is the static verification shape for a batch. It contains enough information to determine whether proofs may be safely verified together.

For the first version, a proof shape includes:

- Proving system identifier, initially `groth16`.
- Circuit family identifier, initially Glock-specific.
- Circuit version.
- Verifying key digest.
- Public input schema identifier.
- Transcript or Fiat-Shamir domain separator, if applicable.
- Curve or pairing backend identifier.
- Optional build metadata used only for diagnostics, not for verification decisions.

A `BatchItem` is one proof plus its public inputs and caller-visible identifier.

A `BatchResult` is either success for the full batch or a structured failure that can optionally identify a failing item after fallback isolation.

### API shape

`zkaleido` should expose a narrow interface along these lines:

```rust
pub enum ProofShape {
    GlockGroth16V1 {
        circuit_id: CircuitId,
        circuit_version: CircuitVersion,
        vk_digest: VkDigest,
        public_input_schema: PublicInputSchemaId,
        domain: DomainSeparator,
    },
}

pub struct BatchItem<P, I> {
    pub id: BatchItemId,
    pub proof: P,
    pub public_inputs: I,
}

pub trait BatchVerifier {
    type Proof;
    type PublicInputs;
    type Error;

    fn shape(&self) -> ProofShape;

    fn verify_batch(
        &self,
        shape: &ProofShape,
        items: &[BatchItem<Self::Proof, Self::PublicInputs>],
    ) -> Result<BatchVerificationReport, Self::Error>;
}
```

This is intentionally less generic than the research API previously discussed. We should not accept trait objects that hide the proving system, verifying key, or public input encoding. The verifier must know what it is batching before it sees the items.

### Supported batch modes

Version one supports two modes.

The first mode is a batch of size one. This sounds trivial, but it gives downstream callers a stable API while letting us compare old and new paths exactly. It also allows Strata code to adopt the interface before performance-sensitive batching is enabled.

The second mode is a homogeneous batch of Glock Groth16 proofs with identical `ProofShape`. All items must use the same verifying key digest and public input schema. If any item differs, verification must fail before cryptographic work begins.

No mixed circuit batches. No recursive aggregation. No proof-carrying-data abstraction. No universal verifier registry.

### Verification behavior

The verifier should perform checks in this order:

1. Reject empty batches unless a caller explicitly asks for a no-op mode. The default should be rejection, because empty success can hide plumbing bugs.
2. Validate the supplied `ProofShape` against the verifier instance.
3. Validate every item’s public input length and schema.
4. Validate proof encoding and subgroup requirements before pairing accumulation.
5. Run the batch verification algorithm.
6. If batch verification fails and diagnostic fallback is enabled, rerun individual verification to identify failing items.

The fallback mode should be disabled in production consensus-sensitive paths unless explicitly configured. It is useful in test harnesses and CI, but it can turn one failed batch into `n` verifier calls. Production callers should receive a batch-level failure unless they have a strong reason to pay for isolation.

### Randomness and soundness

Batch verification must use domain-separated challenge generation. The batch challenge transcript should include:

- Batch verifier version.
- `ProofShape`.
- Batch length.
- Ordered item identifiers or stable item commitments.
- Public input commitments.
- Encoded proof commitments.

We should not rely on caller-provided randomness. If randomness is derived internally, it must be derived from a transcript that binds the entire batch. If the underlying Groth16 batching approach needs external randomness, we should expose only a deterministic transcript-derived interface at the `zkaleido` boundary.

person:AaronFeickert should confirm the exact transcript contents and whether ordered item identifiers are necessary or whether proof/input commitments are sufficient. My preference is to bind item order explicitly, because accidental reordering bugs are otherwise painful to inspect.

### Error handling

Errors should distinguish:

- Shape mismatch.
- Invalid public input schema.
- Invalid proof encoding.
- Cryptographic verification failure.
- Diagnostic fallback failure.
- Unsupported batch mode.

The public API should not leak backend-specific pairing internals unless the caller opts into debug diagnostics. For Strata-facing code, a compact failure enum is preferable. For `zkaleido` tests and benchmarks, richer diagnostics are acceptable.

### Benchmarking

We should add benchmark cases for batch sizes:

- 1
- 2
- 4
- 8
- 16
- 32

Each benchmark must record:

- `zkaleido` commit.
- `g16` commit.
- Curve backend version.
- CPU model.
- Compiler version and flags.
- Circuit identifier and verifying key digest.
- Whether fallback isolation is enabled.

No benchmark result should be quoted in Glock or Strata planning docs unless person:mukeshdroid or another research reviewer has checked reproducibility. This is specifically to avoid repeating the earlier problem where benchmark instability made performance claims hard to review.

### Integration plan

Phase one lands the type definitions, shape validation, and single-item path in `zkaleido`.

Phase two adds homogeneous Glock Groth16 batch verification behind a feature flag. The implementation should live close to existing `g16` verification code rather than introducing a generic aggregation crate.

Phase three wires benchmark fixtures and CI checks. person:Zk2u and person:cyphersnake can help ensure the CI environment records enough machine and dependency metadata to make benchmark drift visible.

Phase four exposes the interface to Glock integration tests. person:prajwolrg should review this boundary before it is consumed by Strata paths.

Phase five considers enabling the batch path in Strata-facing flows, but only after we have stable failure behavior and benchmark confidence.

## Drawbacks

The main drawback is that this does not solve universal aggregation. If we later need mixed proof systems or heterogeneous circuit batching, this API may feel restrictive.

The second drawback is duplication. Some validation logic may overlap with existing single-proof verification paths. I think this is acceptable if the duplication is temporary and localized. The batch verifier is consensus-adjacent enough that implicit generic plumbing would be riskier than a small amount of explicit validation.

The third drawback is that diagnostic fallback can hide performance costs in testing. We need clear defaults and benchmark labels so nobody compares fallback-enabled numbers against production verification numbers.

The fourth drawback is that a narrow Glock-specific shape may create pressure to add one-off variants for every future milestone. We should resist that unless the variant has a clear integration owner and benchmark plan.

## Alternatives considered

One alternative is a universal `ProofBatch` trait over arbitrary proof systems, circuits, and public input encodings. This was the original research direction. I do not think it is appropriate for the current Glock milestone because it expands the review surface and delays the narrow path we need.

Another alternative is to keep batching entirely inside `g16` and expose no `zkaleido`-level abstraction. That is simpler locally, but it pushes shape validation and integration policy into callers. I want `zkaleido` to own the boundary because it is already the proving backend coordination point.

A third alternative is to wait until Strata integration shows batching is necessary. This avoids premature work, but it means any needed batching would arrive during an integration window, when cross-repo changes are most expensive.

A fourth alternative is recursive aggregation instead of batch verification. That may be useful later, but it changes prover cost, verifier assumptions, and artifact management. It is not a substitute for hardening near-term verification throughput.

## Open questions

1. What exact transcript fields are required for the batch challenge, and can we standardize them now without constraining future recursive work?

2. Should empty batches be a hard error everywhere, or do any Glock/Strata call sites naturally model empty work as successful verification?

3. What is the maximum batch size we are comfortable supporting in production before requiring chunking?

4. Should diagnostic fallback be compiled out for production builds, or is a runtime configuration enough?

5. Do we need a stable serialized `ProofShape` for cross-repo fixtures, or is an internal Rust type sufficient for the first milestone?

6. Who owns release coordination when `zkaleido`, `g16`, and Strata integration tests need synchronized updates near a Glock milestone? My proposal is person:Hakkush-07 for `zkaleido`, person:prajwolrg for Strata integration review, and person:AaronFeickert for cryptographic review signoff.
