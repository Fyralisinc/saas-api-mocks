## Goal

Build a shared fixture generator and CI matrix for the Starknet adapter around `product:glock`, with enough stability that `repo:garbled-circuits` and `repo:zkaleido` can consume the same verifier package without each repo inventing its own vector format, Cairo layout assumptions, or proof metadata shims.

The immediate B6 goal is narrower than “finish the Starknet verifier.” We need a repeatable path from Glock verifier inputs to Starknet-facing fixtures, plus CI coverage that proves the package still compiles and validates canonical vectors across both integration surfaces. The design should let person:cyphersnake keep the shared package compiling, let person:Zk2u and I iterate on generator coverage, and give person:Hakkush-07 a clean place to review protocol-level assumptions without chasing downstream repo differences.

## Non-goals

We are not trying to optimize verifier proving or verification benchmarks in this beat. Benchmark polish is lower priority than interface stability because the external Starknet collaboration depends on knowing what the adapter consumes and emits.

We are not freezing the full research notation or whitepaper model. `repo:Technical-Whitepaper` can continue evolving under person:AaronFeickert and person:Hakkush-07. This design only freezes the serialization boundary used by implementation fixtures.

We are not introducing a Bitcoin consensus commitment format for `product:strata` here. The fixture generator can include Strata-shaped metadata fields where useful, but it must not imply a final bridge protocol encoding.

We are not storing large generated fixture blobs in every downstream repo. Generated data should be reproducible, content-addressable where practical, and only checked in when the fixture is intentionally canonical.

## Background

The original estimate assumed a thin Starknet adapter: map Glock verifier inputs into Cairo structs, call the verifier, and add a few integration tests. That assumption stopped holding once we tried to make the same verifier package usable from both `repo:garbled-circuits` and `repo:zkaleido`.

The hard part is not just syntax. Glock verification crosses several domains with incompatible defaults: Rust field element representations, Cairo felt layout, Groth16-style proof conventions in `repo:g16`, garbled circuit wire labels, and protocol notation that was still being reconciled by research. Small differences in endianness, limb packing, public input ordering, or domain separation tags can produce fixtures that look plausible but test the wrong statement.

During B6, holiday availability made reviews bursty. That argues for a generator and CI layout where reviewers can inspect a small manifest diff and regenerate locally, instead of manually checking opaque vector blobs. It also matters that full-vector CI roughly doubles runtime. We need a split between fast compatibility checks and slower canonical-vector checks.

The main artifact should be a shared package contract, not a repo-local test trick. `repo:verifiable-garbling`, `repo:garbled-circuits`, `repo:g16`, and `repo:zkaleido` should all agree on enough fixture semantics that failures point to real interface drift rather than adapter-specific glue.

## Proposed design

The fixture generator should live with the shared verifier package, with thin repo-local wrappers in `repo:garbled-circuits` and `repo:zkaleido`. The generator emits a deterministic fixture directory containing:

- `manifest.json`
- `proof.json`
- `public_inputs.json`
- `adapter_inputs.json`
- optional `witness_digest.json`
- optional compressed binary payloads for large vectors

`manifest.json` is the stable entry point. It records generator version, Glock package commit, source circuit identifier, proof system backend, curve, field modulus, public input ordering version, Cairo adapter ABI version, and a content digest for each payload file. Downstream repos should depend on the manifest schema and adapter ABI version, not on incidental generator internals.

`proof.json` should use a normalized verifier-facing representation: affine coordinates as decimal strings or canonical hex strings, fixed ordering for pairing inputs, and explicit curve identifiers. We should avoid relying on Rust debug output, Cairo serialization output, or test harness-specific encodings. If `repo:g16` changes internal proof types, the fixture boundary should remain stable unless the proof statement changes.

`public_inputs.json` should be ordered, typed, and named. Names are for review; order is consensus for verification. Each item should include `name`, `type`, `field`, `encoding`, and `value`. For example, a Bitcoin header hash should declare whether the value is raw 32-byte little-endian digest, display-order hex, or field-reduced integer. This is where we prevent accidental Bitcoin/ZK bugs: the adapter must not silently reinterpret transaction IDs, block hashes, or Merkle paths as native field elements without an explicit encoding rule.

`adapter_inputs.json` should represent what the Starknet adapter actually passes into Cairo: felts, arrays, and any split-limb structures. This file is generated from the normalized proof and public inputs, not authored independently. The adapter ABI version controls this shape. A breaking Cairo layout change increments the ABI version and updates the CI consumers together.

The generator should support three fixture classes:

1. `smoke`: tiny fixtures that compile and execute quickly, used on every PR.
2. `canonical`: stable fixtures for cross-repo compatibility, checked in by digest and regenerated during scheduled CI.
3. `stress`: large or edge-case vectors, generated on demand or in nightly CI.

For B6, I would make `smoke` mandatory in both downstream repos and keep `canonical` mandatory only in the shared package until runtime is under control. `stress` should not block normal PRs.

The CI matrix should have four axes, but not all combinations need to run on every push:

- package target: shared verifier, `repo:garbled-circuits`, `repo:zkaleido`
- fixture class: smoke, canonical, stress
- adapter ABI version: current, previous if supported
- mode: compile-only, verify, regenerate-and-diff

The default PR matrix should run compile-only and smoke verification for the shared verifier plus both downstream consumers. The scheduled matrix should regenerate canonical fixtures, verify digests, and run full verification. Stress fixtures can run nightly or manually.

Generated fixture size should be controlled with a fixture lockfile. The lockfile contains manifest digests and expected compressed sizes. CI fails if a canonical fixture grows beyond an allowed threshold without an explicit lockfile update. This addresses repo-size concerns while still allowing intentional vector growth.

Ownership should be explicit. I own the generator manifest and adapter fixture shape for this beat. person:Zk2u owns CI matrix mechanics and artifact caching. person:cyphersnake owns keeping the shared verifier package compiling across downstream repos. person:Hakkush-07 reviews notation-sensitive fields, especially public input ordering and encoding. person:prajwolrg and person:storopoli should be pulled in only where the fixture semantics touch `product:strata` protocol assumptions.

## Trade-offs

The shared package design costs more upfront than a thin adapter. We pay for schema design, generator determinism, versioning, and CI plumbing. The benefit is that downstream repos stop drifting independently. Given the external Starknet pressure, this is the right trade: a slightly slower implementation is better than two incompatible “working” adapters.

Storing normalized proof inputs and adapter inputs duplicates data. That duplication is intentional. It lets us distinguish “the proof statement changed” from “the Cairo packing changed.” Without both layers, adapter bugs collapse into verifier failures and become harder to review.

Keeping large fixtures mostly out of regular PR CI reduces coverage on each change. The mitigation is to make smoke vectors representative and make scheduled canonical regeneration visible. Full vectors should protect compatibility, not punish every small edit.

Versioning the adapter ABI may feel heavy for a young package, but it gives external collaborators a concrete contract. If we do not name the ABI, every Cairo struct edit becomes an ambiguous breaking change.

## Rollout plan

First, define `manifest.json` and `public_inputs.json` schemas and land one smoke fixture generated from the current Glock verifier path. person:Hakkush-07 reviews the naming, ordering, and encoding fields before downstream integration.

Second, add `adapter_inputs.json` generation for the current Starknet ABI and wire it into the shared verifier package tests. person:cyphersnake keeps this compiling against both `repo:garbled-circuits` and `repo:zkaleido`.

Third, person:Zk2u adds the PR CI matrix: shared package compile, downstream compile, and smoke verification. Artifact caching should be enabled before canonical vectors are mandatory.

Fourth, add canonical fixture generation with digest locking. CI should support `regenerate-and-diff` so reviewers see manifest and digest changes clearly. Large payloads should be compressed and excluded from downstream repos unless explicitly promoted.

Fifth, after one week of green scheduled runs, declare adapter ABI version `v0` stable for external collaboration. At that point, breaking changes require a manifest version bump, an ABI version bump where applicable, and a migration note in the shared package changelog.
