# Public Testnet Readiness and Launch Criteria

## Summary

This RFC defines the readiness bar for the first public Alpen testnet across `product:strata` and `product:strata-bridge`, with launch targeted inside the 2025-08-04 to 2025-11-02 window. The immediate goal is not to prove that every bridge path is production-complete. The goal is to expose a coherent public network with stable enough consensus parameters, reproducible genesis, observable node and prover behavior, documented RPC access, and explicit rollback criteria.

The launch driver is `person:prajwolrg`. Review ownership is split across protocol, infra, and bridge: `person:MdTeach` and `person:storopoli` for consensus/readiness checks, `person:krsnapaudel` for infra and monitoring, and `person:Rajil1213` / `person:ProofOfKeags` for bridge-facing launch posture. `person:john-light` should treat this RFC as the source of truth for public docs timing, not as a promise that every endpoint is final.

This document intentionally accepts a launch posture where some recovery paths remain manual. That is acceptable only if the manual procedures are tested, assigned, and visible before public announcement.

## Motivation

We need a public testnet because private and semi-public deployments are no longer exercising the full integration surface. The critical path now crosses consensus params, genesis generation, peer discovery, checkpoint production, RPC exposure, bridge state transitions, faucet reliability, dashboards, and external developer expectations. Keeping the network private until every bridge flow is fully automated would reduce near-term risk but delay feedback on the parts most likely to fail under real users: RPC abuse patterns, wallet integration assumptions, faucet demand, observability gaps, and confusion around Bitcoin finality versus Alpen finality.

The main tension is scope. Bridge completeness and public availability compete for the same engineering bandwidth. The bridge team can support a public testnet with constrained functionality, but not if public docs imply production-like trust assumptions or smooth automated recovery. Similarly, protocol can provide a stable enough network, but not if genesis format and consensus parameters continue changing after infra has generated deployment artifacts.

The readiness criteria below are meant to force decisions early. If we cannot freeze network params, reproduce genesis, operate dashboards, and define rollback conditions, we should not launch publicly. If we can do those things, we should launch even if some bridge workflows are explicitly marked experimental.

## Detailed Design

### Launch Scope

The public testnet includes:

- `repo:alpen` nodes running the agreed `product:strata` protocol configuration.
- `repo:strata-p2p` networking configuration sufficient for public peer discovery and bootstrap.
- `repo:strata-bridge` deployed in a constrained bridge test mode.
- `repo:alpen-faucet` for test token distribution.
- `repo:alpen-dashboards` for infra, node, and bridge-facing observability.
- `repo:checkpoint-explorer` for public inspection of checkpoint and chain progress.

The launch does not require full bridge automation, mainnet-grade withdrawal assumptions, or complete removal of operator intervention. It does require that every operator intervention path has a named owner, a runbook, and a clear user-facing statement if it affects public behavior.

### Network Parameters

`person:prajwolrg`, with review from `person:MdTeach` and `person:storopoli`, should publish a frozen network parameter file before genesis generation. At minimum it must include:

- Network identifier and chain naming.
- Block timing assumptions.
- Sequencer / operator keys used at launch.
- Checkpoint interval and Bitcoin anchoring cadence.
- Fork activation heights, if any are enabled.
- Address format and RPC chain ID expectations.
- Bridge contract or bridge program references for the launched environment.
- Expected Bitcoin regtest, signet, testnet3, or testnet4 dependency.

Once frozen, these params cannot change without regenerating genesis or explicitly documenting an in-place migration. The default should be regeneration before launch, not mutation after launch.

Any protocol change merged after the freeze must be classified as one of:

- Launch-blocking consensus fix.
- Non-consensus operational fix.
- Post-launch follow-up.

Consensus changes after freeze require approval from `person:prajwolrg`, one protocol reviewer, and `person:krsnapaudel`, because infra artifacts may already depend on the previous parameter set.

### Genesis Generation

Genesis must be reproducible from committed inputs. The genesis artifact should not be treated as a hand-built blob. The required artifacts are:

- The exact commit SHA for `repo:alpen`.
- The exact network parameter file.
- The key material references, with secrets excluded.
- The command or script used to generate genesis.
- The generated genesis hash.
- A checksum for the distributed genesis artifact.

`person:prajwolrg` owns the genesis procedure. `person:krsnapaudel` owns verifying that the infra deployment consumes the same artifact. `person:MdTeach` or `person:storopoli` should independently regenerate and confirm the hash before we call the network ready.

If the genesis format continues moving, the launch date should move with it. We should not paper over genesis instability with infra-side compatibility glue. The public testnet should begin from a format we are willing to support for the full launch window.

### RPC Exposure

Public RPC should be exposed with rate limits, request logging, and a documented stability level. The initial public endpoint should support basic wallet and explorer use cases:

- Chain ID and network metadata.
- Latest block / header queries.
- Transaction submission.
- Transaction and receipt lookup.
- Balance and account state queries, if applicable.
- Health and sync status endpoints where safe.

`person:krsnapaudel` owns endpoint exposure and rate limiting. Protocol owns correctness of returned state. Devrel should not publish RPC examples until infra has confirmed final URLs and expected error modes.

We should explicitly avoid promising endpoint permanence. Public docs can say the endpoint is for testnet development and may be reset during the launch window. If we expect breaking resets, say so.

### Monitoring and Dashboards

`repo:alpen-dashboards` must cover the minimum operational questions before launch:

- Are public nodes producing and receiving blocks?
- Are bootstrap peers reachable?
- Are checkpoints being produced at the expected cadence?
- Is the bridge indexer or bridge service lagging?
- Are RPC error rates or latencies increasing?
- Is faucet demand normal, abusive, or failing?
- Are explorer views aligned with node state?

`person:krsnapaudel` owns dashboard readiness. `person:prajwolrg` and `person:MdTeach` should define protocol metrics that indicate consensus or checkpoint failure. `person:Rajil1213` and `person:ProofOfKeags` should define bridge-specific health checks, even if the bridge remains constrained.

The known risk is that dashboard metrics lag implementation. To manage this, each launch-blocking metric should have either an automated dashboard panel or a manual command in the launch runbook. Manual checks are acceptable for testnet launch, but only if they are fast and assigned.

### Bridge Posture

The bridge should launch as experimental and constrained. We should not imply production-like Bitcoin custody or withdrawal guarantees. The public launch should document:

- Supported deposit path.
- Supported withdrawal path, if enabled.
- Expected confirmation depth.
- Manual operator steps, if any.
- Failure states users may encounter.
- Reset policy for bridge state during the testnet window.

`person:Rajil1213` and `person:ProofOfKeags` own bridge launch criteria. If bridge completeness slips, the fallback is not to block all public availability. The fallback is to launch protocol RPC, faucet, explorer, and dashboards while labeling bridge features as limited or staged.

### Rollback and Recovery Criteria

We should define rollback before announcement. Rollback means either pausing public promotion, taking endpoints offline, or regenerating the testnet.

Rollback is required if any of the following occur before launch:

- Genesis cannot be reproduced by an independent reviewer.
- Nodes disagree on chain state under the frozen params.
- Public RPC cannot submit or query transactions reliably.
- Checkpoint production fails under normal operation.
- Bootstrap peers are not reachable from outside our infra.
- Faucet cannot issue test funds under expected load.
- Dashboards cannot answer basic liveness questions.
- Bridge state can become misleading or unsafe without public warning.

Post-launch, rollback or reset should be considered if:

- Consensus failure requires state surgery.
- Public users are building against incorrect RPC behavior.
- Bridge accounting diverges from expected state.
- Checkpoint data is wrong or unrecoverable.
- Operator key compromise is suspected.

`person:prajwolrg` has final call on protocol rollback. `person:krsnapaudel` has final call on taking infra endpoints down quickly. Bridge-specific pause decisions sit with `person:Rajil1213` and `person:ProofOfKeags`, with protocol consulted if bridge state affects chain assumptions.

### Launch Checklist

Before public announcement, the launch issue should show:

- Frozen network params reviewed.
- Genesis generated and independently reproduced.
- Infra deployed from pinned artifacts.
- Public RPC smoke tests passing.
- Faucet smoke tests passing.
- Explorer synced and showing expected chain data.
- Dashboard panels or manual checks available.
- Bridge scope documented.
- Recovery runbook reviewed.
- Public docs reviewed against actual endpoint behavior.
- Named on-call coverage for the first 72 hours.

The first 72 hours should be treated as launch stabilization, not normal operations. Infra and protocol review queues will already be congested by then, so we should avoid landing non-critical changes during that period.

## Drawbacks

This launch posture accepts visible rough edges. Public users may encounter resets, constrained bridge flows, and endpoint instability. That is uncomfortable, but less risky than pretending the network is more complete than it is.

Freezing params early also slows protocol iteration. Some changes that would be easy in a private devnet become expensive once public genesis exists. The tradeoff is deliberate: public testnet value depends on a stable shared target.

Manual recovery paths are another drawback. They increase operator load and make public behavior harder to explain. The mitigation is not to hide them, but to document and rehearse them.

## Alternatives Considered

One option is to delay public launch until bridge completeness is much higher. This reduces user confusion but blocks feedback on the broader system and keeps infra assumptions untested.

Another option is to launch protocol-only and exclude `product:strata-bridge` entirely. This is cleaner technically, but it avoids one of the main integration risks we need to learn from. A constrained bridge launch gives us better information as long as public docs are precise.

A third option is to run an invite-only testnet first. We have effectively done versions of this already. The remaining unknowns are public-facing: docs, RPC behavior, faucet load, external wallets, dashboards, and explorer expectations.

We also considered allowing genesis and params to keep moving until the final week. That compresses all review queues into the highest-risk period. Given the current backlog across protocol, bridge, and infra, that is not acceptable.

## Open Questions

- Which Bitcoin test network dependency are we committing to for the full public window?
- What exact bridge operations are enabled on day one?
- Do we publish one public RPC endpoint or separate read/write endpoints?
- What rate limits are acceptable before developer experience becomes unusable?
- Who is the named backup for `person:krsnapaudel` during the first 72 hours?
- What is the maximum acceptable checkpoint delay before public status changes to degraded?
- Do we expose reset history in `repo:checkpoint-explorer` or only in docs?
- What issue label or project view tracks launch-blocking review items across `repo:alpen`, `repo:strata-bridge`, and infra repos?
- Does `person:john-light` need a hard docs freeze date relative to endpoint freeze?
