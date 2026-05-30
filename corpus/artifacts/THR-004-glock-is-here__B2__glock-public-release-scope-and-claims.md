## Summary

This RFC defines the public release scope for `product:glock` across `repo:g16`, `repo:hash-benchmarks`, `repo:Technical-Whitepaper`, and limited integration references in `repo:alpen`. The release should present Glock as a research-grade, independently runnable implementation and benchmark suite for our current G16-oriented work, not as a production-ready protocol component or a finalized Strata integration path.

The release boundary is:

1. A public `repo:g16` library/API that exposes the core proof construction and verification interfaces needed to reproduce the examples.
2. A small set of verification examples that run without internal Strata services.
3. A reproducible benchmark harness in `repo:hash-benchmarks`, with pinned inputs, dependency versions, hardware notes, and scripts.
4. A whitepaper snapshot in `repo:Technical-Whitepaper` that states the construction, assumptions, and current limitations.
5. A narrow note in `repo:alpen` describing intended future integration surfaces without committing to protocol-level parameters or deployment timelines.

The primary goal is to make the Glock work inspectable and runnable by external reviewers while avoiding claims that outpace the review state. AaronFeickert is driving the release, with me owning the initial RFC and API boundary, Hakkush-07 owning standalone verification examples, Zk2u supporting reproducibility and infra hygiene, and ceyhunsen/storopoli available for implementation review where protocol-facing interfaces are touched.

## Motivation

We need a public artifact for Glock that external researchers can evaluate without depending on internal services, private configuration, or unpublished Strata assumptions. The current internal state is useful but too entangled with naming churn, benchmark-local assumptions, and protocol integration sketches. If we publish it as-is, we risk creating confusion about what is stable, what is experimentally validated, and what is only a planned integration direction.

The thread has already exposed three tensions.

First, public claims have had to narrow as review caught up with launch planning. We should assume the public release will be read as a commitment unless we are explicit. Any sentence that sounds like “Glock is ready for Bitcoin bridge production use” should be treated as wrong for this release. The defensible claim is narrower: Glock is an implementation and evaluation artifact for a specific proving/verification direction relevant to Alpen’s Bitcoin/ZK protocol work.

Second, benchmark reproducibility became a blocker later than expected. Benchmarks that only run on one engineer’s machine or depend on loosely described CPU configuration will undermine the release. We do not need perfect cross-platform reproducibility, but we do need enough metadata and scripting that an external reviewer can reproduce the shape of the results and understand deviations.

Third, protocol integration notes were deferred to keep the release focused. That is the right call. The public release should not try to settle bridge-level integration, Bitcoin covenant assumptions, verifier placement, operator incentives, or production parameter selection. Those belong in later protocol RFCs with person:Rajil1213, person:ProofOfKeags, person:prajwolrg, and person:storopoli more directly involved.

## Detailed design

### Release artifacts

`repo:g16` should be the main implementation artifact. The public API should expose stable conceptual names even if internal crate names remain in flux. The API examples in the release must not require users to import unstable or temporary crate paths. If crate renaming cannot be completed before release, we should add a thin public prelude or compatibility module and mark deeper modules as internal.

The minimum API surface should include:

- circuit parameter loading from checked-in example parameters;
- proof generation for the public examples;
- proof verification from serialized proof/public input pairs;
- deterministic test-vector verification;
- explicit error types for malformed proof, invalid public input, unsupported parameter version, and verification failure.

We should not expose internal builder APIs unless they are needed for the examples. The release should prefer boring, auditable interfaces over flexible construction hooks. The first public users are likely to be reviewers trying to reproduce claims, not application developers trying to extend the system.

`repo:hash-benchmarks` should contain the benchmark harness and raw result format. Each benchmark result should include:

- commit SHA for `repo:g16`;
- commit SHA for `repo:hash-benchmarks`;
- CPU model, core count, RAM, OS, kernel, compiler, and relevant feature flags;
- exact command invocation;
- input size or circuit profile;
- median, p95, and run count;
- whether turbo/frequency scaling was controlled.

Zk2u should review the scripts for reproducibility. If we cannot make the scripts portable across Linux/macOS before release, we should say Linux is the supported benchmark environment and include a container or Nix/devshell path for that environment. It is better to be narrow and reproducible than broad and vague.

`repo:Technical-Whitepaper` should include a release-tagged whitepaper snapshot. The snapshot should define the construction, notation, security assumptions, and what the implementation covers. AaronFeickert should own final math review. Hakkush-07 should make sure the verification examples match the paper’s notation closely enough that a reviewer can map code to text without guessing. The appendix math review is already taking longer than expected, so the release should explicitly allow the appendix to lag behind only if the main claims do not rely on unfinished appendix material.

`repo:alpen` should only receive a short integration note or tracking document. It should say Glock is being evaluated for future protocol integration and list the open integration questions. It should not introduce consensus-critical constants, bridge operator requirements, or verifier deployment assumptions.

### Public claims

The release announcement and README language should be constrained to the following claims:

- Glock provides a public implementation of the current G16-oriented proving and verification work.
- The included examples can be verified without internal Alpen services.
- The benchmark suite is intended to reproduce the reported performance envelope under documented conditions.
- The work is relevant to Alpen’s Bitcoin/ZK protocol research but is not a production bridge release.

Claims we should avoid:

- “production-ready”;
- “fully integrated with Alpen”;
- “final protocol design”;
- “trust-minimized bridge implementation” unless carefully scoped to future intent;
- “fastest” or “best” without a benchmark methodology that supports comparison.

john-light and pramodkandel can help phrase external-facing material later, but the source READMEs should remain technical and conservative. We should not optimize the engineering artifacts around launch copy.

### Examples

Hakkush-07’s examples should be standalone and runnable from a clean checkout. They should not require internal Strata services, private RPC endpoints, unpublished parameter files, or environment variables that only exist in our infra.

The target examples are:

1. Verify a checked-in proof against checked-in public inputs.
2. Generate and verify a proof for a small deterministic example.
3. Run a benchmark command over a documented hash/circuit profile.

The examples should fail loudly when parameters are missing or version-mismatched. Silent fallback to generated local parameters would make benchmark and verification results harder to interpret.

### Versioning and release process

We should create release candidates rather than publish directly from `main`.

Proposed sequence:

1. Tag `repo:g16` release candidate `glock-rc.1`.
2. Pin `repo:hash-benchmarks` to that SHA and generate benchmark output.
3. Update `repo:Technical-Whitepaper` with the matching implementation commit.
4. Run clean-checkout verification on a machine not used during development.
5. Freeze public claims and README wording.
6. Tag final releases across repositories.

AaronFeickert should approve the final technical claim set. I will own the API boundary checklist. Zk2u should approve the reproducibility checklist. Hakkush-07 should approve the example checklist. If protocol-facing text expands beyond the narrow integration note, we should request review from storopoli or ceyhunsen before publishing.

### Review checklist

Before public release, we need all of the following:

- `repo:g16` examples run from clean checkout;
- no public example imports unstable crate names unless intentionally documented;
- benchmark scripts run from clean checkout;
- benchmark results include machine and commit metadata;
- whitepaper notation matches example naming where practical;
- README states limitations clearly;
- no README or announcement text implies production bridge readiness;
- no internal service names, private URLs, or unpublished deployment assumptions leak into examples.

## Drawbacks

The main drawback is that this scope may feel underwhelming compared with the broader protocol story. Glock is important to Alpen’s long-term Bitcoin/ZK roadmap, and a narrow implementation-plus-benchmarks release will not answer all natural questions about bridge integration, verifier economics, or mainnet deployment constraints.

The second drawback is extra release overhead. Pinning commits across four repositories, producing reproducible benchmarks, and aligning examples with whitepaper notation will take time from implementation work. However, without this overhead, the public artifact will be harder to trust and harder to review.

The third drawback is API conservatism. By hiding internal builder interfaces, we may frustrate early external users who want to experiment more deeply. I think this is acceptable for the first public release. We can expose more once names and invariants settle.

## Alternatives considered

One alternative is to delay the entire public release until protocol integration notes are complete. I do not recommend this. It couples review of Glock’s implementation and benchmarks to larger bridge design questions that will take longer and involve more teams. It also prevents external reviewers from evaluating the core artifact now.

Another alternative is to release only the whitepaper and defer code. That would reduce engineering cleanup but would not meet the reproducibility bar we want. For this kind of Bitcoin/ZK work, code and benchmarks are part of the argument.

A third alternative is to publish the current repositories with broad caveats. This is the fastest path, but it pushes too much interpretive work onto reviewers. It also increases the chance that unstable crate names, local benchmark assumptions, or internal integration sketches become perceived as public commitments.

A fourth alternative is to make the release explicitly “developer preview” and expose more internal APIs. I do not think we should do that yet. The more surface we expose, the more future changes will look like breakage rather than normal research iteration.

## Open questions

1. What exact crate/module names are stable enough for public examples? I propose we freeze only a top-level prelude and keep deeper modules explicitly internal for now.

2. What benchmark environments do we support at release? My preference is one documented Linux path with strong metadata rather than weaker support across many environments.

3. How much appendix math must land before the release? AaronFeickert should decide whether unfinished appendix material blocks release or can be marked as forthcoming.

4. Should `repo:alpen` contain only a tracking issue, or should it include a short checked-in design note? I prefer a checked-in note if it is clearly non-normative and reviewed by storopoli or ceyhunsen.

5. Who signs off on final public wording? I propose AaronFeickert for technical claims, john-light for external readability only after technical language is frozen, and me for consistency with this RFC’s scope.

6. Do we need independent clean-room reproduction before final tag? Ideally yes. Zk2u or another infra-side reviewer should run the examples and benchmarks without using development-local state.
