# zkaleido Backend Trait Boundaries

## Summary

This RFC proposes a narrowed backend trait boundary for `repo:zkaleido` that separates three concerns which were conflated in the first sketch:

1. Circuit description: the shape of the constraint system or arithmetization-facing program.
2. Witness generation: deterministic construction of private inputs and auxiliary values from public inputs and runtime data.
3. Proof-system binding: backend-specific proving, verification, key material, transcript behavior, and serialization.

The immediate goal is not to make zkaleido a universal proof-system framework. The goal is to make Glock and Strata integration work less brittle while preserving enough abstraction to support the research backends we are actively evaluating in `repo:g16` and `repo:hash-benchmarks`.

The core proposal is a small set of traits:

- `CircuitSpec`: static circuit identity, public input schema, and backend-independent metadata.
- `WitnessBuilder`: witness construction for a given `CircuitSpec`.
- `ProvingBackend`: proof-system binding over an associated circuit, witness, key, proof, and verification key types.
- `BenchmarkFixture`: reproducible fixture construction, separated from backend implementation.

This draws the boundary at the point where zkaleido can describe what should be proven without pretending all proving systems expose the same internals. Backend implementations remain free to use Groth16, Plonkish systems, STARK-style traces, or specialized constructions, but must present stable proof and verification behavior to Glock and Strata callers.

## Motivation

The first backend-trait sketch was too coupled to `repo:g16` internals. It exposed assumptions that made sense for Groth16-style proving, especially around constraint-system synthesis, proving keys, and serialization, but those assumptions leaked into places where zkaleido should only care about application-level circuit identity and public input compatibility.

That coupling creates two concrete problems.

First, Glock and Strata need stable integration surfaces. They do not want to depend on backend-specific proving concepts when they are only asking for a proof of a known circuit over known public inputs. If Glock has to understand whether a backend compiles constraints eagerly, loads an SRS, indexes a trace, or binds a transcript in a particular order, then the abstraction has failed.

Second, research needs room to evaluate backends without forcing delivery code to churn. person:Zk2u, person:mukeshdroid, person:Hakkush-07, and person:ceyhunsen are looking at backend behavior that may not map cleanly onto the current g16 shape. If the common trait encodes g16’s lifecycle too directly, every non-g16 experiment becomes either an awkward wrapper or a fork.

There is also a benchmark review problem. In `repo:hash-benchmarks`, fixtures have lagged API proposals. That made performance claims hard to review because it was unclear whether differences came from backend implementation, witness construction, fixture selection, serialization, or machine-local setup. person:storopoli and I have both hit cases where numbers looked meaningful until we inspected fixture construction and found accidental differences.

The desired outcome is boring: stable enough APIs that protocol engineers can wire proving calls once, research engineers can plug in backends without rewriting Glock or Strata integration code, and benchmark output can be reviewed against named, reproducible fixtures.

## Detailed design

### Non-goals

This RFC does not define a universal circuit IR. It does not require every backend to support every circuit shape. It does not specify a canonical proof format across all systems. It does not require online key generation. It does not attempt to hide all backend-specific configuration.

Instead, it defines where backend-specific behavior is allowed to live.

### Circuit identity and metadata

`CircuitSpec` is the backend-independent description of what is being proven.

Conceptually:

```rust
pub trait CircuitSpec {
    type PublicInput;
    type WitnessInput;

    const CIRCUIT_ID: &'static str;
    const VERSION: u32;

    fn public_input_schema() -> PublicInputSchema;
    fn constraints_profile() -> ConstraintsProfile;
}
```

`CIRCUIT_ID` and `VERSION` are part of compatibility. If the public input schema changes, the version changes. If the witness algorithm changes but the public statement does not, we should usually still bump the version unless we can prove byte-for-byte public input compatibility and identical constraints.

`PublicInputSchema` should remain lightweight. It should describe field ordering, domain separation tags, expected encodings, and any Bitcoin-specific digest conventions. For example, a Strata bridge proof that commits to a Bitcoin block header, deposit outpoint, and bridge state root must specify the exact encoding and ordering of those values. It should not specify proving-key layout.

`ConstraintsProfile` is advisory metadata for review and benchmarking: approximate constraint count, hash gadget families, curve assumptions, recursion posture, and whether the circuit is expected to be aggregation-friendly. It is not used for proof verification.

person:prajwolrg and person:storopoli should review the first set of Strata-facing circuit IDs because most downstream mistakes here will look like integration bugs rather than type errors.

### Witness construction

Witness generation gets its own trait because it is often where protocol logic, chain data, and backend assumptions become entangled.

```rust
pub trait WitnessBuilder<C: CircuitSpec> {
    type Witness;
    type Error;

    fn build(
        &self,
        public: &C::PublicInput,
        input: C::WitnessInput,
    ) -> Result<Self::Witness, Self::Error>;
}
```

The witness builder must be deterministic for a fixed `public` and `input`, excluding explicitly configured randomness. If randomness is required, it must be supplied through a named dependency in the builder configuration, not read implicitly from thread-local RNG state.

For Bitcoin/ZK circuits, this matters because witness generation may depend on:

- Bitcoin header chains and confirmation depth.
- Merkle branches for transaction inclusion.
- Bridge deposit script data.
- Strata state commitments.
- Hash preimage layouts.
- Range-check decomposition and endianness conventions.

These are not proving-backend responsibilities. They belong to protocol code and must be reviewable without reading Groth16 or Plonk internals.

Witness builders may be shared across backends only when the witness representation is genuinely backend-independent. If g16 needs one witness shape and a trace-based backend needs another, we should have two builders over the same `CircuitSpec`, not a leaky universal witness object.

### Proof-system binding

`ProvingBackend` owns backend-specific lifecycle and types.

```rust
pub trait ProvingBackend<C: CircuitSpec> {
    type Config;
    type ProvingKey;
    type VerificationKey;
    type Witness;
    type Proof;
    type Error;

    fn load(config: Self::Config) -> Result<Self, Self::Error>
    where
        Self: Sized;

    fn prove(
        &self,
        pk: &Self::ProvingKey,
        public: &C::PublicInput,
        witness: Self::Witness,
    ) -> Result<Self::Proof, Self::Error>;

    fn verify(
        &self,
        vk: &Self::VerificationKey,
        public: &C::PublicInput,
        proof: &Self::Proof,
    ) -> Result<(), Self::Error>;
}
```

The backend may expose additional methods behind backend-specific extension traits. The common trait should avoid methods like `synthesize`, `num_constraints`, `to_r1cs`, or `transcript_challenge` unless they are required by Glock or Strata call sites. Those belong either in backend-specific diagnostics or benchmark tooling.

Proof and key serialization should not be implicit. We should define explicit codec traits separately:

```rust
pub trait ArtifactCodec<T> {
    type Error;

    fn encode(value: &T) -> Result<Vec<u8>, Self::Error>;
    fn decode(bytes: &[u8]) -> Result<T, Self::Error>;
}
```

This lets a backend support multiple encodings if needed, while Glock and Strata can pin the encoding they accept at integration boundaries.

person:cyphersnake and person:Zk2u should sanity-check that this boundary does not block the research backends currently under evaluation. person:storopoli should check that the g16 implementation does not have to allocate or clone large artifacts unnecessarily just to satisfy the trait.

### Benchmark fixtures

Benchmarks must consume fixtures, not construct ad hoc public inputs in benchmark bodies.

```rust
pub trait BenchmarkFixture<C: CircuitSpec> {
    fn name(&self) -> &'static str;
    fn public_input(&self) -> C::PublicInput;
    fn witness_input(&self) -> C::WitnessInput;
}
```

Fixture names should be stable and descriptive, for example:

- `bridge_deposit_mainnet_like_1in2out`
- `strata_checkpoint_1024_headers`
- `sha256_pairwise_1k`
- `glock_recursive_leaf_minimal`

`repo:hash-benchmarks` should report fixture name, circuit ID, circuit version, backend ID, backend version, machine profile, proving time, verification time, peak memory if available, and artifact sizes. Without fixture identity, performance claims are not reviewable.

### Error boundaries

Errors should remain typed inside backend implementations but be normalized at product integration boundaries.

For example, Glock probably does not need to distinguish an FFT domain error from malformed proving-key bytes. It does need to distinguish:

- unsupported circuit version
- invalid public input encoding
- missing key material
- proving failure
- verification failure
- backend unavailable

The trait should not force every backend to share one internal error enum. Instead, Glock and Strata adapters should map backend errors into product-level error types.

### Versioning and compatibility

Each backend implementation must expose a backend ID and version through config or metadata. We should treat the tuple `(circuit_id, circuit_version, backend_id, backend_version, artifact_codec)` as the compatibility boundary for generated proofs and keys.

This is deliberately strict. It prevents us from silently accepting proofs generated under a backend revision whose transcript, serialization, or constraint synthesis behavior changed.

## Drawbacks

This design introduces more types and more adapter code than a single universal backend trait. In particular, witness construction becomes explicit, which means each circuit/backend pairing may need glue.

It also does not give research code a maximally general API. Some experiments may need backend-specific extension traits immediately. That is acceptable if the common trait remains stable for Glock and Strata.

There is a risk that `CircuitSpec` becomes a dumping ground for metadata. We should keep it limited to compatibility, public input structure, and review-facing profile information. Anything needed only by one backend belongs in that backend’s config.

Finally, this does not solve cross-repo release pressure by itself. `repo:zkaleido`, `repo:g16`, `repo:hash-benchmarks`, and `repo:alpen` can still bunch up near integration windows. The benefit is that versioned boundaries make the bunching visible earlier.

## Alternatives considered

One alternative was to keep the g16-shaped trait and add optional hooks for other backends. This is the smallest immediate change, but it keeps g16 concepts in the center of the API and makes non-g16 work feel second-class.

Another option was a full intermediate representation for circuits, with every backend compiling from that IR. That may be attractive later, but it is too broad for the Glock and Strata delivery timeline. We do not yet have consensus on the right IR, and forcing one now would turn backend hardening into a compiler project.

We also considered making witness generation a method on `ProvingBackend`. That is simpler at call sites, but it hides protocol logic inside proving code. For Bitcoin bridge circuits, that is the wrong review boundary. person:AaronFeickert, person:Zk2u, and person:storopoli were aligned that witness construction should be separately testable.

A final alternative was to define only product-specific APIs for Glock and Strata, with no shared zkaleido backend trait. That would minimize abstraction debate, but it would duplicate backend lifecycle, artifact handling, benchmark integration, and error mapping. It would also make research backend evaluation slower because every backend would need product-specific wiring.

## Open questions

Should `CircuitSpec::VERSION` be manually maintained, or should we derive a circuit digest from constraint synthesis where possible? Manual versions are easier to reason about but easier to forget.

How much public input schema detail should live in zkaleido versus product repos? My preference is that zkaleido owns enough schema to prevent encoding ambiguity, while product repos own domain-specific validation.

Do we need a first-class `VerifierBackend` split from `ProvingBackend` for light-client or constrained verifier environments? Glock may eventually care about verifier-only builds.

What is the minimum benchmark machine profile required for claims in `repo:hash-benchmarks`? CPU model and memory are mandatory; kernel, compiler, and feature flags may also need to be pinned.

Should recursive proof aggregation be modeled as a normal circuit/backend pair, or does it need a separate trait because proof objects become witness inputs? I think it should start as normal circuit composition unless person:mukeshdroid or person:Hakkush-07 finds a concrete blocker.

Who owns compatibility test vectors across repos? I propose person:storopoli owns the initial g16 vectors, person:Zk2u owns backend-comparison fixtures, and person:prajwolrg reviews Strata-facing public input schemas before they are treated as stable.
