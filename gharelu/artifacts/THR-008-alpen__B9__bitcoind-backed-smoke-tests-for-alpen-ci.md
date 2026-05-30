## Goal

Introduce a bitcoind-backed smoke test lane for `repo:alpen` CI that exercises the Strata node against a real Bitcoin regtest backend, with enough determinism to catch integration breakage before it reaches bridge, explorer, or release branches.

The immediate goal for B9 is not exhaustive protocol validation. It is to make the basic local-node path repeatable in CI:

1. Start `bitcoind` in regtest with known wallet and mining state.
2. Start the Strata node with CI-owned configuration.
3. Drive the minimum deposits / block production / checkpoint observation flow needed to prove that Bitcoin RPC, node state transitions, and external outputs still line up.
4. Fail with logs that person:prajwolrg, person:storopoli, person:delbonis, and infra can act on without reproducing the whole environment manually.

The owner for this document is person:krsnapaudel. Protocol review should come from person:prajwolrg and person:storopoli, with person:delbonis validating the common type and serialization boundaries that have repeatedly caused cross-repo churn.

## Non-goals

This is not a full Strata protocol conformance suite. We will not prove every transition, fork-choice edge case, bridge withdrawal path, or checkpoint validity condition in this lane.

This is not a replacement for unit tests in `repo:alpen`, `repo:strata-common`, `repo:bitcoind-async-client`, or `repo:checkpoint-explorer`. The smoke test should sit above those packages and catch wiring failures: config drift, RPC assumptions, serialization incompatibility, database migration breakage, and observable output regressions.

This is not a long-running performance benchmark. CI runners have variable disk and CPU behavior, and the B9 tension is exactly that our runners do not provide predictable disk performance. We should collect timing metadata, but not make pass/fail decisions on tight latency thresholds.

This is not the final release testnet harness. It should use real `bitcoind`, but stay small enough to run on pull requests once stabilized.

## Background

During the Strata node maturation arc, local node work exposed a recurring gap: protocol internals moved faster than the consumers depending on stable shapes. Bridge work from person:Rajil1213 and related review from person:AaronFeickert needed predictable deposit and proof-facing outputs. Explorer work needed checkpoint and block views that did not change accidentally. Meanwhile, implementation details in `repo:alpen`, `repo:strata-common`, and `repo:bitcoind-async-client` were still settling.

The result was that integration failures often appeared as local reproduction tasks rather than CI failures. A developer would run a node locally with a specific `bitcoind`, wallet state, config file, and database directory, then discover a mismatch that CI never saw. The failures were sometimes real protocol integration bugs, sometimes stale type ownership between repos, and sometimes infra-level issues like regtest startup ordering.

By January 2025, local node runs had become reliable enough to promote into CI, but the existing CI shape was not prepared for them. `bitcoind` startup is noisy: RPC may be unavailable while the daemon is still initializing, wallet creation is stateful, block generation can fail if the wallet or address path is wrong, and logs mix expected startup warnings with actual failures. CI disk variability also matters because node databases and Bitcoin datadir writes can make test duration unstable.

The design below makes the test lane explicit: isolate datadirs, use bounded readiness checks, mine deterministic regtest blocks, keep assertions about externally meaningful state, and upload enough artifacts for debugging.

## Proposed design

Add a CI job named `smoke-bitcoind-regtest` under the main `repo:alpen` pull request workflow. Initially this job should be required only on a protected integration branch or manual workflow dispatch. After two weeks of signal collection, we can move it into the default PR matrix.

The job has five phases.

First, provision dependencies. The runner installs or uses a pinned `bitcoind` version. We should avoid `latest` package manager behavior because Bitcoin Core RPC and wallet defaults can shift between versions. The job records `bitcoind --version`, Strata commit SHA, Rust toolchain, and relevant crate versions into a small metadata file.

Second, start `bitcoind` in an isolated regtest datadir. The job creates a temporary directory, writes a minimal `bitcoin.conf`, and starts `bitcoind` with:

- `regtest=1`
- `server=1`
- fixed RPC user/password or cookie path
- `fallbackfee` set for wallet-created transactions if needed
- debug categories limited enough to keep logs useful

Readiness should be determined by RPC, not process existence. The harness should call `getblockchaininfo` until it succeeds or a timeout is reached. Startup logs are still captured, but log text should not drive readiness.

Third, initialize regtest chain state. The harness creates or loads a named wallet, derives an address, mines enough blocks to mature coinbase outputs, and stores the resulting tip hash and height. This step must be idempotent inside a fresh datadir and should never depend on a runner-global Bitcoin directory.

Fourth, start the Strata node with a CI-specific config. The config should point to the isolated Bitcoin RPC endpoint and use temporary database paths. We should keep this config checked into `repo:alpen` as a test fixture rather than generating every field in shell. Generated values should be limited to ports, datadirs, and credentials. This makes review easier when protocol settings change.

The node readiness condition should be an application-level health or RPC response, not a fixed sleep. Once ready, the harness drives a minimal scenario:

1. Mine Bitcoin blocks and confirm Strata observes the regtest tip.
2. Submit or simulate the smallest deposit-related input that current node code supports without depending on external services.
3. Advance enough L1 blocks for the node to process the event.
4. Query the node’s externally consumed output: block status, checkpoint status, or deposit-visible state, depending on what is stable at implementation time.
5. Assert only the invariants bridge and explorer consumers care about: monotonic heights, parseable IDs, stable serialization, and no missing required fields.

Fifth, collect artifacts on both success and failure. At minimum: `bitcoind` logs, Strata node logs, CI config, metadata, final `getblockchaininfo`, final wallet balance, and any node RPC responses used in assertions. On failure, the harness should print a short failure summary with the phase name, last successful milestone, and paths to artifacts.

Implementation should live primarily in a Rust or repository-native test harness if practical. Shell is acceptable for process orchestration, but the assertions should avoid fragile `grep`/`jq` chains once the flow becomes protocol meaningful. person:delbonis should review whether the harness imports shared types from `repo:strata-common` or treats node responses as external JSON. My preference is to treat them as external JSON for this smoke lane: it catches accidental API shape changes that a shared-type client might compile through.

## Trade-offs

Using real `bitcoind` makes the test slower and noisier than mocks, but that is the point. The bugs we are targeting live in RPC behavior, wallet assumptions, chain initialization, and node synchronization. A mocked Bitcoin client would be useful for unit coverage, but it would not catch the integration failures we keep seeing.

Keeping assertions narrow means this lane will miss deeper protocol bugs. That is acceptable. A smoke test should identify broken wiring quickly and provide artifacts. Protocol-specific validation belongs in focused tests owned by person:prajwolrg, person:storopoli, person:MdTeach, and other protocol reviewers.

Running on hosted CI means timing will remain imperfect. We should use generous timeouts with phase-level reporting instead of tight sleeps. If disk performance keeps causing false failures, infra can evaluate larger runners or persistent cache strategy, but the first version should assume ordinary runners.

There is also a type ownership trade-off. If the smoke harness imports internal Rust types, it becomes easier to write and harder to distinguish internal compatibility from external compatibility. If it treats responses as serialized outputs, it is slightly more verbose but better matches the needs of bridge and explorer consumers. For this lane, stable output verification is more valuable.

## Rollout plan

Phase 1: person:krsnapaudel adds the manual `smoke-bitcoind-regtest` CI job, pinned `bitcoind`, isolated datadirs, readiness checks, and artifact upload. The first assertion can be only “node starts, connects to Bitcoin RPC, and observes regtest height.”

Phase 2: person:prajwolrg and person:storopoli review the protocol scenario and define the smallest meaningful event flow. person:delbonis reviews serialized output expectations against `repo:strata-common`.

Phase 3: run the job on the integration branch for at least two weeks. Track false failures separately from real integration failures. The main metrics are failure phase, runtime distribution, and whether logs were sufficient to debug without local reproduction.

Phase 4: make the job required for PRs touching node startup, Bitcoin RPC code, common serialization, checkpoint output, or bridge-visible state. Once stable, expand to all `repo:alpen` PRs.

Phase 5: add downstream smoke consumers. Bridge-facing checks can be coordinated with person:Rajil1213. Explorer-facing checkpoint checks should be aligned with `repo:checkpoint-explorer`. The CI lane should remain small; broader scenarios can become nightly jobs once this one is boring.
