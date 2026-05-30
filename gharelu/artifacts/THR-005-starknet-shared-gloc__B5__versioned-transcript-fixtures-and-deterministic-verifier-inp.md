## Goal

Define the shared verifier package interface for `product:glock` so Starknet collaborators and `product:strata` integration can consume deterministic verifier inputs without depending on local research scripts, implicit transcript ordering, or benchmark harness state.

The concrete deliverable is a versioned transcript fixture format plus a deterministic input derivation path used by the shared verifier package. This replaces the earlier thin-adapter plan. The thin adapter assumed each downstream consumer could reconstruct verifier inputs from examples in `repo:verifiable-garbling`, `repo:garbled-circuits`, `repo:g16`, and `repo:zkaleido`. That is no longer a good boundary: the examples have drifted, external collaborators need a stable surface sooner than our benchmark cleanup, and protocol integration cannot depend on research notation changing in lockstep with production code.

The design target is boring and strict: for a given fixture version, circuit identifier, proof bytes, public parameters, and transcript domain, every supported verifier implementation must derive identical verifier inputs bit-for-bit.

## Non-goals

This does not specify a new Glock protocol, new garbling scheme, or new cryptographic security argument. The protocol semantics remain in the existing RFC and supporting notes in `repo:Technical-Whitepaper`.

This does not optimize verifier performance on Starknet. We should avoid obviously inefficient encoding choices, but interface stability is higher priority for this beat than benchmark polish.

This does not define the final `product:strata` bridge verification policy. `person:prajwolrg` and `person:storopoli` still need to map verifier acceptance into protocol state transitions, but this document only defines how the verifier receives deterministic inputs.

This does not make every historical example valid. Some old examples are misleading and should be deprecated rather than patched into compatibility.

## Background

The original estimate was a thin adapter around existing verifier code. That worked when the expected consumer was internal and the main uncertainty was API shape. It failed once external Starknet collaboration became the forcing function.

The problem is not just serialization. Glock verification depends on transcript construction: domain separation, statement binding, circuit metadata, public input ordering, proof element encoding, challenge derivation, and final predicate evaluation all have to agree. The research repos were useful because they made experiments easy, but they left too much ambient context outside the artifact being verified.

`person:Hakkush-07`, `person:cyphersnake`, and `person:ceyhunsen` surfaced the most important failure mode: two verifier implementations can both be “correct” relative to their local examples while disagreeing on the challenge stream. That is worse than a compile error because it creates integration ambiguity. `person:Zk2u` also flagged that infra cannot reliably cache or reproduce failing cases unless the verifier input is a content-addressable object with explicit versioning.

The external collaborator pressure changes the sequencing. We can tolerate a two-week slip on the initial milestone if the output is a stable shared package. We cannot ship a thin adapter that immediately needs incompatible changes when `product:strata` starts wiring it into bridge flows.

## Proposed design

We will publish a shared verifier package with three public concepts: fixture, transcript, and verifier input.

A fixture is a versioned JSON or CBOR document containing only portable data. Version `glock-transcript-fixture/v1` must include:

- `fixture_version`
- `protocol_id`
- `circuit_id`
- `circuit_digest`
- `parameter_digest`
- `transcript_domain`
- `public_inputs`
- `proof`
- `expected_challenges`
- `expected_result`
- `metadata`

`metadata` is explicitly non-consensus. It may include generator commit, repo name, creation time, author, or benchmark tags. The shared verifier package must ignore it for challenge derivation.

The transcript is a deterministic state machine. It starts from:

```text
H("alpen:glock:transcript:v1" || protocol_id || circuit_id || circuit_digest || parameter_digest || transcript_domain)
```

The exact hash or sponge backend must match the RFC amendment. If the Starknet implementation needs a field-native hash for cost reasons, that is a separate protocol version, not an alternate implementation of `v1`.

Every absorbed value is length-prefixed and type-tagged. Integers are unsigned big-endian canonical encodings unless the RFC amendment says the value is a field element, in which case it is encoded as the canonical field representation. No verifier implementation may accept non-canonical encodings. This matters because permissive parsing can create transcript malleability even when the proof relation is otherwise sound.

Verifier input derivation happens in one direction:

```text
fixture -> parsed fixture -> canonical transcript -> verifier input -> verifier result
```

The package should expose a high-level `verify_fixture(fixture)` for tests and a lower-level `derive_verifier_input(fixture)` for integrations. Downstream code should not manually replay transcript construction.

`expected_challenges` are included as fixtures, not as verifier inputs. They exist to catch transcript drift across implementations. A verifier must be able to run without trusting those expected values; test harnesses compare them after derivation. This keeps fixtures useful for conformance without making expected challenges part of the proof.

`expected_result` supports positive and negative fixtures. Negative fixtures are required because a shared verifier package that only proves agreement on happy paths can still hide malformed parsing behavior. For v1, we should include at least these negative cases:

- wrong domain
- reordered public inputs
- non-canonical integer encoding
- proof element outside field modulus
- circuit digest mismatch
- truncated proof

Ownership:

- `person:AaronFeickert` owns the RFC amendment and fixture semantics.
- `person:Hakkush-07` and `person:cyphersnake` own research fixture generation from current Glock artifacts.
- `person:ceyhunsen` owns cross-checking the implementation boundary against the Starknet collaborator interface.
- `person:prajwolrg` owns `product:strata` protocol expectations and confirms that the lower-level API is sufficient.
- `person:Zk2u` owns reproducibility and CI artifact handling.

The old thin-adapter examples should be marked deprecated in their respective repos with pointers to the shared package fixtures. We should not delete them immediately because they still help explain historical development, but they must stop being treated as integration references.

## Trade-offs

The main cost is schedule. The milestone slips by roughly two weeks because we are formalizing fixtures, canonical encodings, and conformance tests instead of wrapping the existing verifier. I think this is the right trade. The adapter would have looked faster while exporting unstable assumptions to every consumer.

The second cost is rigidity. Versioned fixtures make it harder to experiment casually because every transcript change now requires a version decision. That is acceptable for the shared package. Research code can keep moving, but anything crossing into collaboration or `product:strata` integration needs explicit compatibility.

There is also a format trade-off. JSON is easier for review and external collaboration, but CBOR is closer to deterministic binary parsing. The proposed approach allows either only if the canonical byte representation is defined before hashing. For v1, I prefer readable JSON fixtures plus a strict canonicalization rule in the package. If this becomes fragile, we can move v2 fixtures to CBOR.

Including `expected_challenges` may look redundant, but it gives us an early warning system. If two implementations accept the same proof but derive different intermediate challenges, we want that failure before protocol integration.

## Rollout plan

1. `person:AaronFeickert` drafts the RFC amendment defining fixture v1, transcript initialization, absorb order, canonical encodings, and negative fixture requirements.

2. `person:Hakkush-07`, `person:cyphersnake`, and `person:ceyhunsen` generate the first fixture set from current Glock examples, separating positive fixtures from intentionally malformed negative fixtures.

3. `person:Zk2u` adds CI checks that run fixture validation, challenge comparison, and verifier result comparison on every shared package change.

4. `person:prajwolrg` and `person:storopoli` review the lower-level verifier input API against `product:strata` integration needs, especially public input ordering and circuit identity binding.

5. Deprecate old examples as integration references in `repo:verifiable-garbling`, `repo:garbled-circuits`, `repo:g16`, and `repo:zkaleido`. Keep them available for research history, but point implementers to the shared package.

6. Share the v1 package and fixtures with the Starknet collaborator once the RFC amendment and CI checks land. Benchmarks can follow after interface freeze.
