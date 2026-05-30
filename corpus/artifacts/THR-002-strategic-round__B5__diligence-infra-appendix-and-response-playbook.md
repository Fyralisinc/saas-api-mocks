## Goal

Create a canonical infra appendix and response playbook for strategic-round technical diligence. The immediate audience is investor technical reviewers, but the durable audience is us: protocol, bridge, infra, and devrel should be able to point at one document for how `product:strata`, `product:strata-bridge`, `product:glock`, and `product:bitcoin-dollar` are deployed, monitored, and operated during testnet and early mainnet phases.

This appendix should replace the current pattern where person:pramodkandel and person:john-light assemble bespoke answers from Slack threads, dashboards, release notes, and reviewer memory. It should give enough concrete detail to support diligence without exposing operational secrets or making claims ahead of protocol confidence.

The document should cover:

- Reproducible deployment notes for the current Strata stack.
- Monitoring and alerting boundaries, including known dashboard gaps.
- A short incident-response playbook for sequencer, prover, bridge, Bitcoin RPC, and data-availability failure modes.
- Clear ownership paths across person:krsnapaudel, person:MdTeach, person:Rajil1213, and the relevant protocol reviewers.

## Non-goals

This is not a marketing security paper. It should not claim production maturity where we only have internal process or testnet evidence.

This is not a replacement for the `repo:Technical-Whitepaper`, protocol specs, or bridge design docs. It should link to those as canonical sources for consensus, settlement, proof construction, withdrawal semantics, and bridge trust assumptions.

This is not a full SOC 2 control matrix, a public postmortem template, or a legal disclosure process. We should write it so those can be derived later, but the current scope is technical diligence.

This is not a dashboard redesign project. The appendix should identify gaps and show how we are closing them, but we should not block the doc on making every panel investor-safe.

## Background

During the strategic diligence thread, the investor narrative started moving faster than our confidence in several operational details. The protocol story around validity proofs, Bitcoin settlement, forced exits, and bridge security is strong, but the diligence surface area is broader: reviewers ask how the system is deployed, how we detect failure, who gets paged, what happens when Bitcoin reorgs, and how we distinguish degraded service from funds-at-risk incidents.

Right now, answers exist but are unevenly distributed. person:MdTeach and person:prajwolrg can explain core Strata node behavior. person:Rajil1213 and person:ProofOfKeags can explain bridge operator paths and withdrawal handling. person:AaronFeickert can pressure-test cryptographic assumptions. person:krsnapaudel is ramping into infra ownership midstream and has started collecting reproducible deployment notes, dashboard screenshots, and incident-response fragments. The gap is not absence of work; it is that the work is not yet packaged as a stable appendix.

The infra appendix must make a clean distinction between three layers:

1. Protocol safety: the ZK state transition function, proof verification, Bitcoin checkpointing, withdrawal rules, and bridge custody assumptions.
2. Operational reliability: whether sequencers, provers, indexers, relayers, RPC endpoints, and dashboards are live and observable.
3. Response process: how we triage, escalate, pause, degrade, or recover components without improvising in Slack.

For diligence, reviewers mainly need to know that we understand these layers and do not collapse them into vague “monitoring is set up” claims.

## Proposed Design

The appendix should live as a canonical internal markdown document initially, then be promoted into the diligence packet once reviewed. I propose the following structure.

### 1. Deployment Inventory

Document the currently supported environments: local devnet, shared testnet, staging, and any investor-demo environment. For each environment, list:

- Git refs or release tags for `repo:alpen`, `repo:strata-bridge`, `repo:zkaleido`, and `repo:bitcoin-bosd`.
- Required external dependencies: Bitcoin Core version, RPC mode, mempool assumptions, object storage, container runtime, secrets provider, and metrics backend.
- Boot order: Bitcoin backend, Strata node, prover service, bridge relayer or operator service, indexer, API/RPC edge, dashboards.
- State reset policy: which environments are ephemeral, which preserve chain history, and who can reset them.

The important diligence detail is reproducibility. We should show that a reviewer can understand how a Strata deployment is assembled without needing person:krsnapaudel to narrate a private shell history. We should not include credentials, private endpoint URLs, seed material, signing keys, or privileged runbooks.

### 2. Component Health Model

Define health states for each major component:

- `green`: component is live, synced, and meeting expected latency.
- `yellow`: degraded but no known safety impact.
- `red`: service unavailable or correctness risk requires immediate escalation.
- `black`: insufficient telemetry to classify.

For Strata nodes, health includes Bitcoin tip lag, L2 head progress, checkpoint submission status, reorg handling, and peer connectivity if applicable. For provers, health includes job queue depth, proving latency, failure rate by circuit/version, and verifier compatibility. For bridge services, health includes deposit detection lag, withdrawal queue status, operator signing path status, and Bitcoin transaction propagation. For `product:glock`, the appendix should summarize proof aggregation or verification pipeline health in the terms used by the current implementation, with person:MdTeach and person:delbonis reviewing exact wording.

The `black` state matters. Some dashboards are currently useful for engineers but not investor-safe because panel names, units, or missing legends require tribal context. We should state those gaps explicitly and track them as remediation items rather than hide them.

### 3. Monitoring Appendix

Add screenshots only after they have been scrubbed. For each dashboard, include a one-paragraph interpretation guide:

- What the dashboard proves.
- What it does not prove.
- Which alert fires from the underlying metric.
- Who owns first response.

For example, a prover dashboard can show that proving latency is within target for a testnet workload. It does not prove mainnet capacity under adversarial load. A bridge dashboard can show that deposit indexing and withdrawal processing are moving. It does not independently prove the security of bridge custody assumptions.

person:krsnapaudel should own dashboard packaging. person:Rajil1213 should approve bridge panels. person:MdTeach should approve Strata node and prover panels. person:pramodkandel should decide which screenshots are safe for the diligence packet.

### 4. Incident Response Playbook

Use severity levels tied to user impact and funds risk.

- `SEV0`: credible funds-at-risk condition, invalid state accepted, bridge signing compromise, or verifier/proof mismatch affecting finalized state.
- `SEV1`: chain halt, sustained inability to prove or checkpoint, bridge withdrawal halt, or Bitcoin backend failure affecting settlement.
- `SEV2`: degraded performance, delayed deposits, delayed proving, partial RPC outage, stale dashboards.
- `SEV3`: non-user-impacting alert, documentation drift, noisy metric, isolated non-production failure.

Initial response should be simple:

1. Declare severity and incident commander in the incident channel.
2. Freeze speculative external commentary.
3. Capture current heads: Bitcoin tip, Strata L2 head, last proven block, last checkpoint, bridge deposit height, withdrawal queue.
4. Decide whether to pause relayers, disable public endpoints, stop new bridge operations, or continue in degraded mode.
5. Assign one owner for technical mitigation and one owner for stakeholder updates.

For Bitcoin reorgs, the playbook should specify the confirmation depth assumed by each service and the expected behavior when a deposit or checkpoint is displaced. For prover failures, it should distinguish job retry from circuit/version incompatibility. For bridge incidents, it should separate liveness failures from custody or signing-risk failures. person:Rajil1213 should be the required reviewer for bridge response language; person:AaronFeickert should review any wording that implies cryptographic guarantees.

## Trade-offs

The main trade-off is precision versus disclosure. Diligence reviewers need concrete operational evidence, but too much deployment detail creates avoidable security exposure. The appendix should describe architecture, observability, and process, not secrets or exact privileged procedures.

Another trade-off is canonical quality versus speed. We need this document during the active diligence window, so the first version should be accurate and useful even if some panels remain marked `black`. Waiting for perfect dashboards would preserve polish but continue the current problem: person:pramodkandel and person:john-light would still need to synthesize answers manually.

There is also a narrative trade-off. Investors want a crisp story, but our internal doc should preserve uncertainty where it exists. Saying “monitoring screenshots are not yet investor-safe” is better than implying mature operational coverage before person:krsnapaudel has finished the infra review.

## Rollout Plan

Week 1: person:krsnapaudel drafts the appendix with deployment inventory, dashboard list, and first incident-response table. person:MdTeach reviews Strata/prover accuracy. person:Rajil1213 reviews bridge operations. person:pramodkandel marks which sections are diligence-facing.

Week 2: Convert Slack-only response knowledge into the playbook. Add concrete examples for Bitcoin RPC outage, prover backlog, bridge relayer halt, and dashboard staleness. person:delbonis and person:prajwolrg review protocol boundary language.

Week 3: Produce the diligence-safe version. Remove sensitive endpoints, scrub screenshots, link canonical docs in `repo:Technical-Whitepaper`, `repo:alpen`, and `repo:strata-bridge`, and create an internal issue list for every remaining `black` health state.

After the strategic round, keep the appendix as a release-blocking operational artifact. Every major testnet or mainnet-readiness release should update deployment refs, dashboard coverage, and incident contacts before we reuse the document externally.
