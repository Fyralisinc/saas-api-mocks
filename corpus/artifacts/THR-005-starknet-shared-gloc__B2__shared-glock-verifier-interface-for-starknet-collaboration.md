# Shared Glock Verifier Interface for Starknet Collaboration

## Summary

This RFC proposes a shared verifier interface for `product:glock` that can be used by both Alpen protocol integration work and the Starknet collaboration without maintaining separate verifier adapters. The current assumption that we can expose a thin Starknet-specific wrapper over the existing Glock verifier is no longer sufficient. The calldata layout, proof-shape assumptions, transcript binding, and field encoding rules need to be made explicit enough that external collaborators can implement against them without tracking internal verifier churn.

The proposal is to define a small, versioned verifier package with three stable layers:

1. A canonical proof object model that describes the Glock verifier inputs independent of runtime.
2. A Starknet calldata mapping that specifies how those objects are serialized over Cairo-friendly field elements.
3. A transcript/domain separation policy that binds proof bytes, public inputs, verifier parameters, and collaboration-specific context.

The implementation target is a shared package in the verifier stack, not a one-off adapter. `person:cyphersnake` has already been prototyping calldata and proof-shape mappings against current Glock verifier assumptions. This RFC tries to turn that prototype into an integration contract that `product:strata`, Starknet-side consumers, and our internal test harnesses can depend on.

## Motivation

The initial collaboration estimate treated Starknet support as a thin compatibility layer: take the existing Glock verifier, encode inputs into Starknet calldata, and expose a small shim. That was optimistic. The verifier boundary is not just a type conversion problem.

We have three concrete problems.

First, field encoding is ambiguous. Glock-side code currently reasons in terms of verifier-native field elements and byte encodings from the proving system implementation. Starknet contracts reason in felt-like values with Cairo-specific range and serialization constraints. A proof that is well-formed in our Rust verifier can still be malformed, non-canonical, or differently interpreted once represented as Starknet calldata. If we do not specify canonical encoding now, we risk accepting proofs in one environment and rejecting equivalent proofs in another, or worse, verifying different statements under apparently identical public inputs.

Second, transcript domain separation is underspecified. The existing verifier assumptions are safe in their immediate context, but the Starknet collaboration introduces a new consumer, new calldata conventions, and potentially long-lived verifier commitments. We need a domain string and transcript binding strategy that distinguishes Alpen internal test vectors, Glock standalone verification, and Starknet-facing verification. `person:AaronFeickert` has been pushing for clearer notation here, and I agree that this belongs in the interface rather than in downstream prose.

Third, interface stability now matters more than benchmark polish. The external collaboration pressure means we should stop optimizing around local convenience. We can still benchmark and reduce calldata later, but the first shared artifact must be stable enough that Starknet collaborators can build against it. If we change the order of commitments, public input packing, or transcript labels after integration begins, we push cost into everyone else’s implementation.

The goal of this RFC is to make the verifier boundary boring: explicit objects, explicit encodings, explicit transcript labels, explicit versioning.

## Detailed Design

### Package scope

We should create a shared verifier interface package under the Glock verifier workspace, consumed by both internal Rust verification code and any Starknet calldata generation tooling. The package should not contain Cairo verifier code itself. It should define the interface contract and provide reference serialization, deserialization, validation, and test vector generation.

The package should expose:

- `GlockVerifierInterfaceVersion`
- `GlockProof`
- `GlockPublicInputs`
- `GlockVerifierParameters`
- `GlockTranscriptContext`
- `StarknetCalldataProof`
- conversion routines between canonical Rust objects and Starknet calldata
- validation routines for canonical field encodings
- deterministic test vector generation

The package should live close enough to the verifier implementation that changes to proof shape fail tests immediately. `person:prajwolrg` and `person:storopoli` should review placement from the `product:strata` integration side so we do not create an interface that is technically clean but awkward to consume.

### Interface versioning

The interface version must be included in every serialized proof payload and in the transcript. I propose:

```text
GLOCK_VERIFIER_INTERFACE_V1 = 1
TRANSCRIPT_DOMAIN = "alpen.glock.starknet.verifier.v1"
```

The version is not just metadata. It is a verifier input. Any change to proof element ordering, field encoding, transcript labels, public input packing, or verifier parameter commitments requires a version bump. Minor implementation refactors that preserve byte-for-byte serialization and transcript behavior do not require a bump.

The version should be represented as a single canonical integer in calldata and as a fixed label in transcript initialization. The verifier must reject unknown versions.

### Canonical proof object model

The canonical proof object should be defined before any Starknet serialization. The current prototype from `person:cyphersnake` maps calldata directly against the current verifier assumptions; this RFC keeps that work but inserts an object model in the middle.

At a high level:

```text
GlockProof {
  interface_version
  commitments
  openings
  evaluations
  challenge_dependent_values
  proof_auxiliary_data
}
```

The actual field names should match the verifier notation once `person:AaronFeickert`, `person:Hakkush-07`, and `person:ceyhunsen` finish the notation pass. The important point is that every field has:

- a stable name
- a stable order
- a type domain
- an encoding rule
- a transcript binding rule

No anonymous vectors should cross the interface unless their length and semantic meaning are fixed by the verifier parameters. If we need variable-length vectors, the length must be included in the transcript and constrained by verifier parameters.

### Field encoding

All field elements crossing into Starknet calldata must use canonical little-endian byte encoding at the object layer and canonical felt-array representation at the Starknet layer.

For each verifier-native field element:

1. Serialize the element to its canonical byte representation.
2. Split into fixed-width limbs suitable for Starknet calldata.
3. Require the top limb to be range-checked against the modulus-derived bound.
4. Reject non-canonical representations on decode.

We should not rely on “fits in felt” assumptions unless the field modulus makes that mechanically true. The interface package should expose `encode_field_element_for_starknet` and `decode_field_element_from_starknet` and tests should include edge cases: zero, one, modulus minus one, modulus, and overlong encodings.

This directly addresses the field encoding mismatch from the current design beat. It also gives Starknet collaborators an unambiguous target without forcing them to reverse-engineer our Rust serialization.

### Calldata layout

The Starknet calldata layout should be deterministic and self-delimiting where needed:

```text
[
  interface_version,
  verifier_parameter_commitment_len,
  verifier_parameter_commitment...,
  public_input_len,
  public_inputs...,
  proof_len,
  proof_elements...
]
```

Within `proof_elements`, the order follows the canonical `GlockProof` object order, not the order that happens to be convenient in the current verifier implementation.

For fixed-size sections, lengths may be omitted if the verifier parameters uniquely determine them. For the first version, I prefer including top-level lengths even if redundant. The calldata overhead is small relative to the cost of integration ambiguity, and we can remove redundancy in a later version if it becomes material.

`person:Zk2u` should review the calldata shape from the infra/research side because this package will likely become the source of generated fixtures used in CI and external handoff.

### Transcript binding

Transcript initialization must bind:

- interface version
- transcript domain string
- verifier parameter commitment
- public inputs
- proof commitments in canonical order
- collaboration context, if applicable

The collaboration context should not be free-form user data. For V1, I propose a fixed context label:

```text
"starknet-shared-glock-verifier"
```

If later we need chain ID, contract address, or verifier deployment ID binding, that should be a V2 change unless we know the exact Starknet deployment semantics now. Prematurely binding unstable deployment metadata would make test vectors fragile.

Every transcript absorb operation should have a named label. For example:

```text
absorb("interface_version", version)
absorb("verifier_parameters", parameter_commitment)
absorb("public_inputs", public_inputs_digest)
absorb("proof_commitments", commitments_digest)
```

This is slightly more verbose than positional absorbs, but it makes review tractable. The notation review queue is already growing around these details; explicit labels reduce ambiguity between the research document, Rust code, and Cairo implementation.

### Test vectors and compatibility checks

The shared package should generate golden test vectors containing:

- canonical JSON representation of proof objects
- canonical byte representation
- Starknet calldata representation
- expected transcript challenge outputs
- expected verifier result

The test vectors should include at least:

- one minimal valid proof
- one proof with maximum observed public input count for current `product:strata` integration
- one malformed non-canonical field encoding
- one wrong interface version
- one changed transcript domain
- one reordered commitment vector

`person:john-light` does not need to review verifier internals, but having stable test vectors will help devrel avoid publishing examples that are accidentally tied to an internal branch state.

### Integration sequence

The implementation should proceed in this order:

1. Finalize object names and notation with `person:AaronFeickert`, `person:Hakkush-07`, and `person:ceyhunsen`.
2. Land the Rust interface package with serialization, validation, and fixture generation.
3. Update `person:cyphersnake`’s calldata prototype to use the package instead of hand-mapped verifier assumptions.
4. Wire internal Glock verifier tests to consume the same canonical objects.
5. Hand off V1 calldata fixtures to Starknet collaborators.
6. Add `product:strata` integration tests once `person:prajwolrg` and `person:storopoli` confirm the public input shape.

## Drawbacks

This adds an interface package before we have fully optimized the verifier boundary. We will likely encode some redundancy into V1, especially around lengths and parameter commitments. That is intentional but still a cost.

It also slows the immediate prototype. `person:cyphersnake` could continue mapping calldata directly against the current verifier faster than we can formalize the object model. The risk is that this speed produces an adapter nobody can safely depend on.

A versioned interface creates maintenance obligations. Once V1 is shared externally, we either support it or clearly deprecate it. That is more process than a research prototype usually wants.

Finally, transcript labels and canonical encoding rules may force small changes to existing verifier code. Those changes are not conceptually hard, but they touch security-sensitive logic and need careful review.

## Alternatives Considered

### Thin Starknet adapter

The original plan was to expose a narrow adapter over the existing verifier. This is still the fastest path to a demo, but it does not solve encoding, transcript, or versioning ambiguity. I do not think it is acceptable for the collaboration handoff.

### Starknet-first verifier format

We could define the proof format directly as Starknet calldata and treat Rust objects as a convenience layer. This would optimize for the external consumer but make the internal verifier harder to reason about and test. Glock should remain verifier-native first, with Starknet as one serialization target.

### Wait for final research notation

We could block implementation until the research notation is fully settled. This would reduce naming churn, but it would also stall the collaboration. The better compromise is to freeze semantic object boundaries now and allow final field names to settle before V1 is cut.

### Benchmark-optimized calldata V1

We could minimize calldata immediately by removing redundant lengths, compressing commitments where possible, and using context-specific packing. This is premature. V1 should privilege auditability and deterministic compatibility. Optimization can happen in V2 with test vectors proving equivalence.

## Open Questions

1. Do we need to bind Starknet chain ID or verifier contract address in V1, or should deployment-specific binding be deferred to V2?

2. Are verifier parameters fixed enough that we can commit to their canonical digest format now? `person:AaronFeickert` and `person:ceyhunsen` should confirm.

3. Should public inputs be represented as verifier-native field elements only, or do we need typed public input domains for `product:strata` state commitments, bridge claims, and Bitcoin header data?

4. What is the exact owner path for the shared package: inside `repo:glock`, adjacent to `repo:g16`, or as a small compatibility crate consumed by both?

5. How many malformed encoding fixtures are enough for external confidence? My default is to include more than we think we need, especially around modulus boundaries.

6. Who signs off on V1 stability before handoff? I propose `person:Hakkush-07` for interface authorship, `person:AaronFeickert` for transcript and notation, `person:cyphersnake` for calldata compatibility, and one protocol integration reviewer from `person:prajwolrg` or `person:storopoli`.
