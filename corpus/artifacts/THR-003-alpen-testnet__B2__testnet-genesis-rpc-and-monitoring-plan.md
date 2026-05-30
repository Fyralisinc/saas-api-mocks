## Goal

Define the launch-ready plan for public testnet genesis generation, RPC exposure, and monitoring for `product:strata` and the supporting public-facing services in `repo:alpen`, `repo:strata-p2p`, `repo:alpen-dashboards`, `repo:checkpoint-explorer`, and `repo:alpen-faucet`.

The immediate goal for B2 is not “perfect launch infra.” It is a reproducible readiness path that lets person:prajwolrg make the launch/no-launch call with enough protocol, bridge, and infra signal to avoid ambiguity. By the end of this beat we should have:

- A deterministic genesis artifact and manifest that protocol reviewers can reproduce.
- Explicit network parameters for public testnet, including chain identifiers, sequencer/bootstrap peers, DA/checkpoint assumptions, bridge addresses, faucet limits, and RPC policy.
- Public RPC endpoints that are stable enough for docs and devrel, without pretending they are production-grade.
- Dashboards and alerts that cover consensus progress, sequencer health, proof/checkpoint pipeline health, bridge liveness, RPC saturation, and faucet abuse.
- Rollback criteria that are operationally actionable by infra without needing a protocol debate during an incident.

Primary owners:
- person:krsnapaudel: infra plan, endpoint exposure, dashboard wiring.
- person:prajwolrg: launch readiness driver and protocol sign-off coordination.
- person:MdTeach, person:storopoli, person:bewakes: consensus/genesis review.
- person:Rajil1213, person:ProofOfKeags: bridge readiness and recovery paths.
- person:john-light: docs timing and public endpoint communication.

## Non-goals

This plan does not try to finish every bridge feature before public testnet. The current launch posture intentionally trades bridge completeness for public availability. Bridge flows must be safe, observable, and recoverable, but they do not need to be feature-complete.

This plan does not define mainnet parameters, final withdrawal security, production fee policy, or long-term decentralization of sequencing/proving. Any parameter that could be mistaken for mainnet policy must be labeled testnet-only in the manifest and docs.

This plan does not remove manual recovery. Manual recovery remains part of the launch posture. The goal is to make the manual path explicit, rehearsed, and gated by clear operator roles rather than hidden in Slack context.

This plan does not require dashboards to be beautiful or complete. We need the minimum useful observability surface for launch decisions and incident response.

## Background

The public testnet launch depends on several surfaces stabilizing at the same time: protocol consensus changes, genesis generation, P2P bootstrap, RPC exposure, faucet funding, bridge flows, explorer/checkpoint visibility, and public documentation. The main risk in B2 is that each subsystem can appear “almost ready” independently while the integrated network remains difficult to reproduce or debug.

Genesis format is still moving because protocol changes in `repo:alpen` affect how rollup configuration, initial validator/sequencer metadata, chain IDs, and bridge-related commitments are encoded. That creates downstream churn for infra scripts and dashboards. If we let genesis be an implicit byproduct of whoever last ran the tool, we will lose reproducibility exactly when review queues are backed up.

RPC exposure has a separate timing problem. Devrel and public docs need stable URLs before launch week, while endpoint behavior may still change under load. We should separate “stable DNS and routing” from “stable service semantics.” person:john-light can publish endpoints once DNS, basic health checks, and rate-limit policy are fixed, even if we continue tuning backend pools.

Monitoring currently lags implementation. We have metrics in different places, but launch readiness needs a single view that answers: is the chain producing blocks, are checkpoints advancing, are proofs being generated or accepted where expected, are bridge queues moving, are RPC nodes falling behind, and can faucet usage be distinguished from abuse?

## Proposed design

### Genesis artifact and manifest

We create a launch genesis bundle with three files:

1. `genesis.json`: canonical genesis consumed by nodes.
2. `network.toml`: human-readable public testnet parameters.
3. `manifest.json`: hash manifest for reproducibility and review.

The manifest should include:
- Git commit SHAs for `repo:alpen`, `repo:strata-p2p`, and `repo:strata-bridge` used to generate artifacts.
- Tool command and arguments used for generation.
- SHA256 hashes of `genesis.json` and `network.toml`.
- Chain ID, network name, genesis timestamp, slot/block timing, initial sequencer identity, bootstrap peers, initial bridge config, checkpoint config, and faucet allocation.
- Reviewer acknowledgements from person:MdTeach, person:storopoli, and person:bewakes before the bundle is promoted from candidate to launch.

The genesis generation flow should be deterministic from checked-in config plus explicit secrets supplied out of band. No operator-local defaults. If a value is generated, it must be written into the manifest. If a value is secret, the manifest must include a public commitment or derived identifier, not the secret.

Genesis candidates are versioned as `testnet-genesis-rc.N`. We only update the launch candidate by producing a new RC, never by editing an existing artifact in place.

### Network parameters

The public testnet config should fix:

- Network name: public testnet, with an explicit non-mainnet chain ID.
- Sequencer/bootstrap set: initially Alpen-operated, with bootstrap peer IDs published.
- RPC policy: public read-only endpoints, rate-limited by IP and method class.
- Faucet policy: capped allocations per address and per time window, with allowlist override for devrel demos.
- Bridge posture: bridge enabled only for supported flows; unsupported flows must fail closed or be hidden from public UI.
- Checkpoint policy: checkpoint cadence, explorer indexing expectations, and alert threshold if checkpoints stall.

person:prajwolrg owns the final protocol parameter table. person:krsnapaudel owns translating those values into infra config and dashboards. person:Rajil1213 and person:ProofOfKeags own the bridge-specific entries.

### RPC exposure

We expose RPC through a small gateway layer rather than direct node access. The gateway should provide:

- Stable public DNS names.
- TLS termination.
- Per-method rate limits.
- Backend health checks that remove lagging nodes.
- Request and error metrics by method, status code, backend, and latency bucket.
- A separate internal endpoint for operators and dashboards.

Initial public methods should be conservative: chain state reads, block/header queries, transaction submission, and basic health methods. Expensive debug and tracing methods stay disabled unless person:prajwolrg explicitly approves them for testnet.

RPC readiness criteria:
- At least two backend nodes synced from genesis.
- Gateway failover verified by taking one backend out of rotation.
- Rate limits verified against faucet and explorer usage.
- Public endpoint returns consistent chain ID and latest block across backends.
- Docs use DNS names, not raw hostnames.

### Monitoring and dashboards

The launch dashboard in `repo:alpen-dashboards` should have five panels:

1. Chain progress: latest block/slot, finality/checkpoint height, block time distribution.
2. Node health: peer count, sync lag, restart count, disk, CPU, memory.
3. RPC health: request rate, p95/p99 latency, 4xx/5xx, backend eviction count.
4. Bridge health: deposit detection lag, withdrawal queue depth, relayer status, manual recovery queue.
5. Faucet/explorer health: faucet request rate, failed sends, balance, explorer indexing lag.

Alerts should page only on launch-critical signals:
- Chain stops advancing for more than the agreed threshold.
- RPC public endpoint unavailable from external probe.
- All bridge relayers down or queue age exceeds threshold.
- Checkpoint generation or indexing stalls beyond threshold.
- Faucet hot wallet falls below minimum balance.
- Any node diverges on chain ID or canonical tip.

Everything else can be Slack-only during launch week. person:sapinb should have the ops-facing alert matrix before the public launch window.

### Rollback and recovery

Rollback means we stop public promotion, not necessarily delete the network. Criteria:

- Genesis mismatch discovered after publication.
- Chain halt that cannot be recovered within the agreed window.
- Bridge flow produces ambiguous or unrecoverable state.
- RPC endpoint serves inconsistent chain data across backends.
- Faucet drains unexpectedly or becomes an attack vector.

Recovery runbooks must cover:
- Restarting sequencer/bootstrap nodes.
- Removing a bad RPC backend.
- Pausing faucet.
- Pausing or hiding bridge UI flows.
- Replaying or reconciling bridge relayer state.
- Publishing a replacement genesis RC if we abort before public launch.

Manual recovery actions require an explicit operator and reviewer pairing: infra operator from person:krsnapaudel or delegate, protocol reviewer from person:prajwolrg/person:MdTeach/person:storopoli, bridge reviewer from person:Rajil1213/person:ProofOfKeags when bridge state is involved.

## Trade-offs

The main trade-off is launch velocity versus completeness. Public testnet is useful only if external users can hit stable endpoints, get funds, submit transactions, and inspect progress. Waiting for full bridge completeness would delay the feedback loop. The cost is that we must be honest in docs and monitoring: bridge is testnet, limited, and recoverable by manual paths.

A second trade-off is deterministic process versus iteration speed. Versioned genesis RCs add ceremony, but they prevent unreviewable changes in the most sensitive launch artifact. Given the review queue pressure behind consensus changes, this is worth it.

A third trade-off is gateway complexity. Direct node RPC would be simpler, but public exposure without rate limits and backend isolation will make incidents harder to contain. A thin gateway is the right minimum.

## Rollout plan

Week of 2025-08-18:
- person:prajwolrg freezes the initial public testnet parameter table.
- person:krsnapaudel creates the first genesis bundle candidate and manifest.
- person:MdTeach, person:storopoli, and person:bewakes review genesis fields against consensus expectations.
- person:Rajil1213 and person:ProofOfKeags review bridge entries and recovery assumptions.

Week of 2025-08-25:
- Promote a reviewed `testnet-genesis-rc.1`.
- Bring up two or more RPC backends from genesis.
- Put gateway DNS, TLS, health checks, and rate limits in place.
- Wire launch dashboard panels and external probes.
- Run failure drills: backend removal, faucet pause, bridge relayer restart, chain halt triage.

Exit criteria for B2:
- Reproducible genesis bundle exists with reviewer sign-off.
- Public RPC DNS is stable and tested against multiple backends.
- Launch dashboard answers the five launch questions without manual log-diving.
- Rollback criteria and recovery runbooks are written and assigned.
- person:john-light has endpoint names and caveats for public docs, with no raw infra details exposed.
