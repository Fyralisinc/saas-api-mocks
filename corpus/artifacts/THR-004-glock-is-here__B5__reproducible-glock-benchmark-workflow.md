**Goal**

Make Glock benchmark results reproducible enough that we can publish them with a narrow, defensible claim set and keep re-running the same workflow after release without arguing about machine drift.

For the public Glock release, the benchmark workflow in `repo:hash-benchmarks` has been too sensitive to runner variance, package versions, turbo behavior, and ad hoc local setup. This blocks `person:AaronFeickert` from finalizing release text because the numbers cannot be cleanly tied to a machine profile and commit set. The goal of this design is to replace the flaky workflow with pinned benchmark profiles owned by infra, with outputs that research and protocol can independently inspect.

Success criteria:

- A benchmark result records exact source revisions for `repo:g16`, `repo:hash-benchmarks`, and relevant Glock crate tags.
- A result records machine class, CPU model, microcode-visible metadata, kernel, compiler, Rust toolchain, feature flags, and benchmark parameters.
- Re-running the same profile on the same reserved machine class produces results within a documented tolerance band.
- Published numbers are generated from signed or otherwise authenticated artifacts, not screenshots or manually copied tables.
- `person:storopoli`, `person:Hakkush-07`, and `person:AaronFeickert` can consume the output without depending on my local environment.

**Non-goals**

This does not attempt to prove Glock is optimal against every hash construction or every proving system configuration. We are not expanding the benchmark matrix for release. In fact, the release claim should stay narrower until review catches up.

This does not solve protocol integration performance in `repo:alpen`. `person:storopoli` is patching integration notes so downstream protocol engineers can follow the release branch, but this workflow only produces benchmark evidence for Glock itself and the comparison cases already selected by research.

This does not create a fully trustless benchmarking system. A reserved machine plus pinned software stack is still an operational control, not a cryptographic guarantee. We can make manipulation harder and accidental drift visible, but we are not proving “the benchmark was honestly run” inside a ZK circuit.

This does not require all future benchmarks to use the same machine profile. It defines release-grade profiles for this launch and a schema we can extend later.

**Background**

Glock’s release depends on performance claims that are easy to overstate. The relevant public line is about practical prover/verifier costs for the hash construction in the context we care about, not a universal “fastest hash” claim. Review has already forced us to narrow wording, and that is good. The remaining problem is that even narrow claims need reproducible numbers.

The old benchmark workflow had three failure modes.

First, GitHub-hosted runners varied too much. CPU model, frequency behavior, cache topology, noisy neighbors, and kernel settings all leaked into the result. Some variance is expected for microbenchmarks, but we saw enough movement to make release tables unstable.

Second, dependency state was underspecified. Rust toolchain, feature flags, crate revisions, and comparison implementation commits were often implicit. That is tolerable during research iteration, but not for a public release branch.

Third, results were not packaged as reviewable artifacts. `person:mukeshdroid`, `person:Hakkush-07`, and `person:AaronFeickert` could inspect numbers, but reconstructing the exact run required asking the runner what happened. That slowed review and made benchmark reproducibility a late blocker.

During beat B5, I am replacing this with pinned machine profiles. In parallel, `person:storopoli` is updating `repo:alpen` integration notes, but those notes depend on unreleased crate tags. The design needs to tolerate that sequencing: benchmark artifacts can reference release candidate SHAs first, then final tags once cut.

**Proposed Design**

We define benchmark profiles as versioned TOML files in `repo:hash-benchmarks`, under `profiles/`. A profile is the unit we publish and rerun. Example fields:

- `profile_id`: stable name, e.g. `glock-public-release-cpu-a`.
- `machine_pool`: reserved infra pool name.
- `cpu_allowlist`: exact CPU model strings accepted for the run.
- `kernel`: pinned kernel family or image digest.
- `rust_toolchain`: exact channel and version.
- `compiler_flags`: explicit `RUSTFLAGS`, target CPU policy, and feature flags.
- `repos`: expected SHAs or tags for `repo:g16`, `repo:hash-benchmarks`, and release candidate Glock crates.
- `benchmarks`: list of benchmark cases, input sizes, iteration counts, warmup policy, and statistical summary mode.

The workflow runs only on self-hosted runners that match a profile. `person:krsnapaudel` and `person:arminsabouri` do not need to manage this directly for the release, but the infra convention should match the rest of our reserved-runner setup: machines are named by pool, not by person, and the workflow refuses to run if hardware metadata does not match the profile.

Before running benchmarks, the harness emits a preflight manifest:

- Git SHAs and dirty state for every checkout.
- Rust and Cargo versions.
- Kernel version and selected CPU metadata from `/proc/cpuinfo` and `lscpu`.
- Governor and turbo state.
- Memory size and NUMA topology.
- Benchmark profile hash.

The preflight manifest is included in the final artifact and hashed. If a required field is missing, the run fails closed.

The benchmark harness then executes each case with a fixed warmup and sample policy. For release numbers, we should report median and a conservative dispersion measure, not a single best run. The artifact includes raw samples, summary JSON, and generated markdown tables. Release prose should cite the generated table artifact, while the whitepaper or technical note can point to the raw sample archive.

For variance control, each release profile has a tolerance rule. The first accepted run on a clean profile becomes the baseline candidate. Two subsequent runs must fall within the tolerance band for the benchmark group before we mark the profile “release-stable.” If they do not, the workflow preserves all artifacts but marks the result as unstable. `person:Hakkush-07` and `person:AaronFeickert` can then decide whether the claim text should narrow further or whether we need to investigate infra.

For authentication, the workflow should attach GitHub artifact attestations where available and also produce a simple signed checksum file from the runner identity. This is not meant to be a trust anchor for users; it is meant to prevent internal copy/paste drift between artifacts, release notes, and `repo:Technical-Whitepaper`.

The output shape should be boring:

- `manifest.json`
- `samples.jsonl`
- `summary.json`
- `summary.md`
- `checksums.txt`
- `profile.toml`

`person:john-light` gets `summary.md` for public-facing coordination, but the release branch should retain the JSON artifacts so claims remain auditable. `person:ceyhunsen` can review harness changes where research-owned comparison cases are touched.

**Trade-offs**

Pinned machines reduce variance but increase scheduling friction. This already slipped the infra change by four days, so we should not pretend the cost is zero. The benefit is that once a profile is stable, we stop burning review time on irreproducible differences.

Using exact CPU allowlists makes the release less portable. That is intentional for public numbers. Later we can add more profiles, but the first release needs fewer degrees of freedom, not more.

Failing closed on missing metadata will make the workflow annoying at first. I prefer that over silently accepting a result that cannot be defended later. If a metadata field is genuinely unavailable on a runner, we should explicitly mark it optional in the profile schema.

Publishing medians and dispersion may make the headline less clean than best-case numbers. That is the right outcome. Glock’s release should survive technical scrutiny from people who will rerun the code.

Deferring `repo:alpen` protocol integration notes keeps this artifact focused. The downside is that protocol engineers, including `person:storopoli`, `person:bewakes`, and `person:alexhui01`, will still need final crate tags before the integration branch is fully clean. We should not mix that dependency into benchmark reproducibility.

**Rollout Plan**

1. Land profile schema and preflight manifest support in `repo:hash-benchmarks`.
2. Configure one reserved runner pool for the public release profile and disable GitHub-hosted runners for release-grade benchmark jobs.
3. Run three candidate benchmark passes using release candidate SHAs for `repo:g16` and Glock crates.
4. Have `person:Hakkush-07` review benchmark case coverage and `person:AaronFeickert` review whether generated tables support only the narrowed public claims.
5. Replace release candidate SHAs with final crate tags once available, then rerun the same three-pass stability check.
6. Attach `summary.md`, raw samples, manifests, and checksums to the release branch artifacts.
7. Update `repo:Technical-Whitepaper` and public release notes to cite artifact IDs rather than manually copied local results.
8. Keep the profile in place for post-release reruns, but require a new profile ID for any hardware, compiler, or benchmark-matrix change.

Owner for the workflow is `person:Zk2u`. Research signoff is `person:AaronFeickert` with input from `person:mukeshdroid` and `person:Hakkush-07`. Protocol integration notes remain with `person:storopoli`, and should reference the benchmark artifact only after final tags are cut.
