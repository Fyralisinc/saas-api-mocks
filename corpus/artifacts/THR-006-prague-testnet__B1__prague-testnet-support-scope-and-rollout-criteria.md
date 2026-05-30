# Prague Testnet Support Scope and Rollout Criteria

## Summary

This RFC defines the Prague testnet support scope for `product:strata` and `product:strata-bridge` across the December 2025 through early March 2026 window. The goal is to make Prague usable as a public-facing testnet target, not merely a protocol branch that can boot locally.

The work spans chain configuration, bridge smoke paths, faucet readiness, explorer indexing, dashboard visibility, and public developer documentation. The driver is person:prajwolrg, with expected coordination across protocol, bridge, infra, and devrel. The initial implementation window is constrained by holiday availability, so this RFC proposes explicit rollout criteria and ownership boundaries to prevent partial readiness from being mistaken for testnet support.

Prague support should be considered ready only when:

1. `repo:alpen`, `repo:strata-bridge`, and `repo:strata-p2p` agree on the same chain parameters and network identifiers.
2. A bridge deposit and withdrawal smoke path has run against Prague infra.
3. `repo:checkpoint-explorer` indexes Prague checkpoints and exposes expected finalized state.
4. `repo:alpen-faucet` can fund Prague users reliably with rate limits and operator visibility.
5. `repo:alpen-dashboards` exposes chain health, prover/checkpoint progress, bridge status, and faucet health.
6. person:john-light has enough stable endpoint, faucet, and bridge behavior to publish public testnet instructions without hand-waving.

## Motivation

The immediate motivation is to support the Prague testnet as a coordinated network target rather than a set of loosely related repo changes. Historically, testnet enablement has been easy to split across repositories but hard to declare complete. Chain parameters land in one place, bridge config follows later, infra catches up when boot nodes and RPC endpoints stabilize, and public docs/faucet work trail the core protocol work. That sequencing is understandable, but it creates a false readiness signal: protocol code can be “done” while users still cannot join, fund, bridge, observe, or debug the network.

For Prague, we should treat testnet support as an end-to-end product surface. The technical risk is not just consensus failure. The more likely failure mode is configuration drift: one repo uses the Prague chain ID while another uses stale network magic; bridge validation points at the wrong checkpoint cadence; explorer indexing assumes older block metadata; dashboards read a legacy Prometheus label; the faucet funds accounts on a chain that bridge tooling does not recognize. These failures waste protocol time because they appear as user bugs, bridge bugs, or infra instability rather than as release coordination problems.

The second motivation is bridge validation. person:Rajil1213 and person:ProofOfKeags cannot confidently validate bridge behavior until infra provides stable RPC, sequencer, prover, and Bitcoin-side test hooks. If bridge smoke tests depend on late infra stability, we need to make that dependency explicit and schedule it as a gating criterion, not as an afterthought.

The third motivation is public readiness. person:john-light should not need to reverse-engineer the current state of Prague from Slack threads and config diffs. Devrel output should be derived from stable artifacts: endpoints, faucet instructions, bridge caveats, known limits, and rollback expectations.

## Detailed Design

### Scope

Prague support covers six repositories:

- `repo:alpen`: Prague chain configuration, genesis/state assumptions, checkpoint cadence, prover/verifier compatibility, and release tags.
- `repo:strata-p2p`: network identifiers, bootnode configuration, peer discovery behavior, and Prague-specific connection policy.
- `repo:strata-bridge`: bridge configuration, Bitcoin-side parameters, deposit/withdrawal smoke tooling, and bridge validation scripts.
- `repo:checkpoint-explorer`: Prague indexing configuration and checkpoint visibility.
- `repo:alpen-dashboards`: operational dashboards for chain, bridge, prover, explorer, and faucet health.
- `repo:alpen-faucet`: Prague funding endpoint, rate limiting, balance monitoring, and operator controls.

The initial target is not a fully permissionless, production-grade network. It is a coherent public testnet that lets external users obtain funds, submit transactions, exercise bridge paths where enabled, and observe state transitions.

### Canonical Prague Configuration

We should create one canonical Prague configuration package or generated artifact consumed by the relevant repos. The exact mechanism can be a checked-in TOML/JSON file, a Rust crate/module, or a CI-generated config bundle, but it must establish one source of truth for:

- Prague chain ID.
- Network name and aliases.
- P2P network magic or equivalent discriminator.
- Genesis hash and initial state root.
- Checkpoint interval and finalization assumptions.
- RPC endpoint names.
- Bridge contract/script parameters.
- Bitcoin network or signet/regtest/testnet target used for bridge flows.
- Faucet funding denomination and dust thresholds.
- Explorer network slug.

person:prajwolrg should own the canonical config schema. person:MdTeach and person:bewakes should review protocol-sensitive fields. person:krsnapaudel should review infra-facing fields, especially endpoint naming, deploy environment labels, and dashboard dimensions. person:Rajil1213 and person:ProofOfKeags should review bridge fields before the first bridge smoke run.

Each repo may transform the canonical config into local runtime format, but transformations should be tested. The important property is that drift becomes visible in CI.

### Cross-Repo Drift Checks

Each participating repo should add a Prague config check that verifies local constants against the canonical artifact. At minimum:

- `repo:alpen` checks chain ID, genesis hash, checkpoint interval, and verifier/prover compatibility flags.
- `repo:strata-p2p` checks network discriminator, bootnode list shape, and advertised network name.
- `repo:strata-bridge` checks chain ID, RPC target, Bitcoin network, bridge script parameters, and checkpoint assumptions.
- `repo:checkpoint-explorer` checks explorer slug, genesis hash, and checkpoint cadence.
- `repo:alpen-faucet` checks chain ID and RPC target.
- `repo:alpen-dashboards` checks environment labels and expected metric names.

These checks do not need to be elaborate. A small test that fails loudly on mismatched Prague values is better than a manual rollout checklist.

### Rollout Phases

Phase 0 is config freeze. We agree on the Prague canonical config and land validation checks. No public claims should be made before this phase is complete.

Phase 1 is protocol boot. `repo:alpen` and `repo:strata-p2p` can start a Prague network with stable bootnodes and predictable RPC behavior. person:prajwolrg and person:MdTeach should verify node startup, peer discovery, block production, and checkpoint emission. person:krsnapaudel should confirm infra naming and deployment reproducibility.

Phase 2 is observability. `repo:checkpoint-explorer` and `repo:alpen-dashboards` must show chain height, checkpoint height, prover/checkpoint status, peer health where available, RPC status, and error rates. This phase gives us the ability to debug the bridge and faucet without relying on logs from individual machines.

Phase 3 is faucet readiness. `repo:alpen-faucet` should expose a Prague faucet with rate limiting, balance alerting, and operator controls. The faucet owner needs to be explicit. Since ownership is unclear at kickoff, the default proposal is that person:krsnapaudel owns deployment and operational health, while person:prajwolrg owns protocol correctness of the funding transaction path. If this split is wrong, we should resolve it before Phase 1 closes.

Phase 4 is bridge smoke validation. person:Rajil1213 and person:ProofOfKeags should run at least one deposit path and one withdrawal or withdrawal-adjacent path supported by the current Prague bridge design. If withdrawals are intentionally limited, the limitation must be documented with exact reason and expected follow-up. Bridge validation should include chain confirmation assumptions, checkpoint dependency, proof generation or verification behavior, and failure observability.

Phase 5 is public testnet instructions. person:john-light can publish docs only after phases 0 through 4 have passed or after we explicitly mark unsupported pieces. Docs should include endpoints, faucet flow, bridge caveats, explorer link, dashboard link if public, known limitations, and where to report issues.

### Readiness Criteria

Prague is “internal ready” when protocol nodes are stable for 48 hours, checkpoints are visible, dashboards are live, and faucet funding works from a clean wallet.

Prague is “bridge ready” when bridge smoke tests pass against the deployed network and the bridge team has signed off on current limitations.

Prague is “public ready” when internal ready and bridge ready are both complete, public docs are reviewed, and ops has a rollback or pause procedure for faucet and bridge-facing endpoints.

If a criterion is waived, the waiver must name the owner, the risk, the expected user-visible behavior, and the follow-up issue.

## Drawbacks

This approach adds coordination overhead at the exact moment we want to move quickly. A canonical config artifact and cross-repo checks require upfront work that may feel slower than copying constants into each repo.

The phased rollout may also expose that some work is not staffed. Faucet ownership is already unclear, and holiday availability means the critical path may wait on person:krsnapaudel or bridge reviewers. Making the dependency explicit does not remove it.

There is also a risk that “public ready” becomes too strict for a testnet. We should avoid production-grade requirements where they do not matter. The criteria above are intended to prevent avoidable confusion, not to block useful external testing until every edge case is solved.

## Alternatives Considered

One alternative is to let each repo manage Prague configuration independently and rely on manual review. This is the lowest-effort path but directly preserves the current drift problem. It also makes regressions hard to attribute because config mistakes show up as runtime failures.

Another alternative is to delay bridge support until after core Prague protocol support is public. This would simplify the first launch, but it weakens the point of the testnet for `product:strata-bridge` and leaves person:Rajil1213 and person:ProofOfKeags validating against a moving target later.

A third alternative is to keep Prague internal until all docs, faucet, bridge, explorer, and dashboards are polished. That reduces public embarrassment but delays feedback. The phased readiness model is a better fit: we can declare internal readiness, bridge readiness, and public readiness separately without pretending they are the same milestone.

## Open Questions

Who is the durable owner for `repo:alpen-faucet` Prague readiness: infra, protocol, or a named rotating owner?

Which Bitcoin network target should Prague bridge smoke tests use for the first public window: signet, testnet4, or a controlled regtest-like environment?

Do we want the canonical Prague config to live in `repo:alpen`, or should it be generated into a standalone artifact consumed by downstream repos?

What bridge limitations are acceptable in the first public Prague window, and how should person:john-light describe them without implying stronger guarantees than we have?

What is the minimum dashboard surface that ops needs before public docs go live: internal-only Grafana, public read-only dashboards, or screenshots plus explorer links?

Who signs off on final public readiness besides person:prajwolrg: person:MdTeach for protocol, person:Rajil1213 for bridge, person:krsnapaudel for infra, and person:john-light for docs?
