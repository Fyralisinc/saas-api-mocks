**Goal**

Ship a standalone Glock verifier demo that can be published with the initial public release without requiring access to Strata, internal prover services, private test vectors, or unreleased crate topology. The demo should let an external reviewer clone `repo:g16`, run a deterministic verification example, inspect the verification key and public inputs, and reproduce the expected accept/reject behavior locally.

The immediate output for beat B2 is a release-ready demo plan: API boundary, example structure, fixture format, benchmark reproducibility requirements, and review ownership. This is scoped to the verifier side only. The demo should support the public claim that Glock has a usable verification interface for Bitcoin/ZK protocol integration work, while avoiding claims about end-to-end bridge security, production prover economics, or Strata integration readiness.

Primary driver remains person:AaronFeickert. I am writing this as person:Hakkush-07, with person:mukeshdroid owning the release RFC alignment and person:Zk2u covering reproducible environment details.

**Non-goals**

This demo will not expose the internal prover pipeline. We should not publish witness generation code, production proving parameters, private benchmark machines, or any Strata service adapters.

This demo will not present Glock as integrated into `repo:alpen`. Protocol integration notes are explicitly deferred. We can include comments showing where a verifier would sit in a bridge or rollup validation path, but not a full integration guide.

This demo will not make performance claims that depend on unreproducible hardware, unstable compiler flags, or unpublished benchmark harnesses from `repo:hash-benchmarks`. If we include timing output, it must be labeled as local measurement and not a release claim.

This demo will not stabilize all crate names. The public API must avoid forcing external users to import internal crate paths that person:mukeshdroid already flagged as likely to change. The example should depend on a narrow exported verifier facade.

**Background**

The Glock public release is trying to land three things at once: a credible cryptographic artifact, runnable verification examples, and enough documentation for external researchers to evaluate the construction. The current risk is that the release narrative is ahead of the review surface. We can reduce that risk by publishing a narrow verifier demo that is honest about what has been reviewed.

During B2, person:mukeshdroid drafted the release RFC around the Glock API boundary. The main design tension is that early examples directly reference unstable crate names and module paths. That is fine for internal iteration, but bad for a public release: external examples become accidental API commitments. In parallel, I sketched verification examples that can run without internal Strata services. Those examples currently need cleanup around fixture provenance, expected outputs, and failure-mode coverage.

person:AaronFeickert is reviewing the math appendix and public claims. That review is taking longer than launch planning assumed, so the demo should be built to stand independently from any stronger cryptographic claims. The right posture is: “this verifies this included proof under this included verification key and public input encoding,” not “this proves production bridge correctness.”

person:Zk2u should own the environment reproducibility path: pinned Rust toolchain, deterministic fixture generation record, and container or Nix instructions if needed. person:ceyhunsen can review the example ergonomics from a research-user perspective. person:john-light should only get a narrow description after the claims are locked, so devrel does not amplify unstable language. person:storopoli can sanity-check the public Rust API shape once the facade is in place.

**Proposed design**

We publish a `standalone-verifier` example under `repo:g16` with one command that verifies a known-good Glock proof and one command that intentionally fails against mutated public inputs.

The public-facing API should be a small facade, tentatively:

```rust
let vk = VerificationKey::from_bytes(VK_BYTES)?;
let proof = Proof::from_bytes(PROOF_BYTES)?;
let public_inputs = PublicInputs::from_bytes(INPUT_BYTES)?;

Verifier::new(vk).verify(&proof, &public_inputs)?;
```

The exact type names can change, but the boundary should remain this shape: parse verification key, parse proof, parse public inputs, verify. The example must not import low-level polynomial commitment internals, transcript details, curve-specific modules, or internal crate names. If we need feature flags for curve/backend selection, hide them behind example-level Cargo features rather than user-visible module paths.

Fixtures should live in a format that is inspectable and hard to confuse:

```text
fixtures/
  glock_demo_vk.bin
  glock_demo_proof.bin
  glock_demo_public_inputs.json
  glock_demo_manifest.json
```

The manifest should include the fixture version, producing commit, circuit identifier, hash of each binary file, transcript domain separator, curve/backend identifier, and whether the proof is synthetic or derived from a reduced demonstration circuit. Public inputs should be JSON for readability, but verification should use a canonical byte encoding derived from the JSON. The manifest must specify that canonicalization. We should avoid relying on map ordering, whitespace, or host-endian integer encodings.

The example CLI should support:

```text
cargo run --example standalone-verifier -- verify fixtures/glock_demo_manifest.json
cargo run --example standalone-verifier -- verify --mutate-input fixtures/glock_demo_manifest.json
cargo run --example standalone-verifier -- print-public-inputs fixtures/glock_demo_manifest.json
```

The successful path exits `0` and prints only a concise verification success line plus optional elapsed time. The mutated path exits non-zero and confirms the verifier rejects altered public inputs. We need the reject path because otherwise the demo only proves that parsing and a happy path work.

For docs, `repo:Technical-Whitepaper` should reference the demo narrowly: it is a verifier API and fixture demonstration. It should not depend on unresolved appendix math. The README in `repo:g16` should include a short threat-model note: fixture verification is not a substitute for reviewing circuit constraints, setup assumptions, Fiat-Shamir transcript binding, or production integration.

Benchmark reproducibility should be minimal for this beat. We can include local timing output from the CLI, but any benchmark table belongs either in `repo:hash-benchmarks` after reproducibility review or behind a separate release note. If launch needs one number, person:Zk2u and person:mukeshdroid should agree on a pinned command, hardware description, commit hash, compiler version, and number of runs. Otherwise we omit numbers.

Review ownership:

- person:Hakkush-07 owns the standalone verification example and fixture manifest schema.
- person:mukeshdroid owns consistency with the release RFC and public API boundary.
- person:AaronFeickert owns final review of cryptographic wording and appendix dependencies.
- person:Zk2u owns reproducible environment instructions.
- person:ceyhunsen reviews whether an external researcher can run and inspect the demo without internal context.
- person:storopoli reviews Rust API stability and packaging ergonomics.
- person:john-light waits for claim-lock before writing public-facing copy.

**Trade-offs**

A standalone verifier demo is less impressive than an end-to-end Strata integration demo, but it is much easier to review honestly. It narrows the release to a checkable cryptographic artifact and avoids mixing Glock correctness with bridge orchestration, service availability, or unpublished protocol code.

Using static fixtures improves reproducibility but weakens the sense of liveness. That is acceptable for the first public release. A dynamic prover demo would require exposing more internals and would expand the review surface at exactly the wrong time.

JSON public inputs are more readable than binary-only fixtures, but they introduce canonicalization risk. We should keep JSON as the human-facing representation and make the byte encoding explicit in the manifest. The verifier should never verify “whatever serde happened to emit.”

Hiding internal crate names behind a facade adds a small maintenance burden. It is still the right move because public examples become de facto API documentation. If we publish examples with unstable paths, we either freeze bad names or break early users immediately.

Omitting benchmark claims may make the release feel less complete. The alternative is worse: publishing numbers that person:AaronFeickert, person:Zk2u, or person:mukeshdroid cannot reproduce under release pressure. Local timing is enough for the verifier demo; benchmark claims can follow after `repo:hash-benchmarks` is cleaned up.

**Rollout plan**

1. person:Hakkush-07 finalizes the example CLI and fixture layout in `repo:g16`, using the facade API only. Include one passing fixture and one deterministic mutation path.

2. person:mukeshdroid updates the release RFC to define the verifier boundary as the only public API commitment for this release. Remove direct references to unstable crate names from examples.

3. person:Zk2u adds pinned environment instructions: Rust version, expected target, dependency lockfile policy, and optional container/Nix path. The command sequence should work from a clean clone.

4. person:ceyhunsen runs the demo from scratch and files any ambiguity around fixture meaning, command output, or public input encoding.

5. person:storopoli reviews the Rust API surface for accidental exports and naming that we would regret stabilizing.

6. person:AaronFeickert reviews all public claims touching soundness, transcript binding, setup assumptions, and what the demo does or does not establish.

7. person:john-light receives the locked language only after the above reviews are done. Public copy should say the release includes a standalone verifier demo, not a production integration demo.

Exit criteria: clean clone works, passing and failing verification paths behave as expected, fixture hashes are documented, no unstable crate paths appear in public examples, and the README language matches the reviewed claims.
