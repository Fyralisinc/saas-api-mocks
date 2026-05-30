**Goal**

Define the Prague testnet chain parameters and default node profile for `product:strata` so protocol, bridge, infra, explorer, faucet, and public support work all target the same network shape during the 2025-12-18 to 2025-12-31 design beat.

The immediate goal is to remove configuration drift across `repo:alpen`, `repo:strata-p2p`, `repo:strata-bridge`, `repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet`. Prague should be a stable enough public-facing testnet that bridge validation can proceed without late rewrites, while still allowing us to reset the chain if we find a consensus or proof-system issue before the March support window closes.

This document proposes the canonical Prague chain-params bundle, the node profiles we should ship, and the ownership boundaries for rolling these values into dependent repos. person:prajwolrg is the driver for the thread; I will own the initial chain-params draft from protocol, with review from person:bewakes and person:MdTeach, infra wiring from person:krsnapaudel, and bridge validation from person:Rajil1213 and person:ProofOfKeags.

**Non-goals**

This is not a mainnet parameter decision. Any value here can prioritize observability, test liquidity, faster debugging, or lower operational cost over mainnet conservatism.

This is not a redesign of the bridge protocol. The Prague node profile must expose stable interfaces for `product:strata-bridge`, but bridge deposit validation, withdrawal policy, and challenge semantics remain owned by the bridge team.

This is not a final public documentation artifact. person:john-light can derive docs from the frozen parameter bundle, but this document is for internal implementation alignment.

This is not a proof-system performance target. Prague must run the currently selected proving path and publish checkpoints, but we should not treat its proving latency as a production SLO until infra, batching, and prover topology are separately specified.

**Background**

Prague exists because earlier testnet work let each repo encode its own view of the network: chain ID, genesis hash, sequencer URL, Bitcoin network, checkpoint cadence, faucet funding address, bridge confirmation depth, and indexer assumptions were all updated locally. That was workable while only protocol nodes were live. It became brittle once bridge watchers, dashboards, faucet, and public docs needed to agree.

The most sensitive dependency is genesis. `repo:strata-bridge` needs the Prague rollup genesis output root, L1 deployment metadata, deposit contract or script parameters, and final sequencer endpoint shape before it can validate deposits end to end. person:Rajil1213 and person:ProofOfKeags have already flagged that the bridge deposit watcher should not ship with a reorg policy inferred from defaults. The watcher needs explicit Bitcoin confirmation depth, rollup finality interpretation, and behavior when the sequencer reorgs or republishes a batch.

The second dependency is node role clarity. For Prague we have at least four practical node modes: local developer node, public RPC node, bridge-observer node, and infra validator/checkpointer node. These should be profiles over one chain-params file, not separate parameter sets. If a developer can accidentally point a bridge watcher at a local profile with different genesis or Bitcoin network, we will burn time debugging false failures.

The third dependency is public readiness. Faucet and docs trailed core protocol work in prior testnet phases because they were waiting for infra stability, while infra was still waiting on final protocol constants. The solution is not to make every value immutable early; it is to version the parameter bundle and make “provisional” visible.

**Proposed Design**

We should introduce a canonical Prague chain-params bundle named `prague-0` and store the source of truth in `repo:alpen`. Other repos should consume generated artifacts or pinned copies that include the bundle version and genesis hash. The bundle should be treated as immutable once published; if we reset the chain, we publish `prague-1` rather than mutating `prague-0`.

The bundle should contain:

- `network_name`: `prague`
- `params_version`: initially `prague-0`
- `rollup_chain_id`: a Prague-specific integer that cannot collide with local dev or prior public testnets
- `genesis_timestamp`: UTC timestamp selected at final genesis build time
- `genesis_state_root`, `genesis_block_hash`, and `genesis_output_root`
- `sequencer_pubkey` or sequencer identity commitment, depending on the current consensus implementation
- `sequencer_rpc_url`, `sequencer_p2p_multiaddr`, and fallback bootnodes
- Bitcoin network binding, expected to be Signet unless infra explicitly requires a private regtest-backed deployment
- bridge deposit script or contract parameters, including deposit address derivation domain separator
- checkpoint cadence in rollup blocks and expected Bitcoin anchoring cadence
- proof verification key digest or circuit set digest for the active Prague proving path
- minimum supported node software version
- feature flags enabled at genesis

The node binary should accept `--network prague` and load the embedded Prague params by default. It should also accept `--chain-params <path>` for controlled overrides, but if the loaded bundle declares `network_name = prague`, the node must log the params version, genesis hash, Bitcoin network, and bootnode set on startup. Public RPC nodes should expose these values through a lightweight status endpoint so dashboards and bridge watchers can assert they are connected to the expected network.

Profiles should be layered on top of the same bundle:

`dev` profile: single-node or small local cluster, relaxed peer count, local data path, optional mock prover, no public RPC assumptions. It may override endpoints but must not call itself Prague unless it uses the Prague genesis.

`public-rpc` profile: read-heavy RPC limits, stable CORS policy, no wallet or signing endpoints, metrics enabled, peer discovery enabled, pruning policy documented.

`bridge-observer` profile: RPC plus event/indexing settings required by `repo:strata-bridge`; must pin Bitcoin confirmation depth, rollup block lag before acting on deposits, and reorg handling. For Prague, I propose bridge watchers consider Bitcoin deposits candidate after 6 Signet confirmations, eligible for rollup inclusion after indexer observation, and invalidated if the Bitcoin backing transaction is reorged before inclusion. Rollup-side bridge events should be considered unstable until the checkpoint containing the event has either been proven or explicitly marked as soft-final by the sequencer policy. person:Rajil1213 and person:ProofOfKeags should confirm whether that is too conservative for testnet UX.

`infra-validator` profile: checkpointer/prover-facing settings, stricter peer requirements, metrics labels, persistent storage, alert thresholds, and no faucet dependencies.

For cross-repo consumption, `repo:alpen` should generate a JSON artifact and a compact TOML artifact from the same typed params. `repo:strata-p2p` should use the bootnode and network magic values from the artifact. `repo:strata-bridge` should vendor the artifact with a startup assertion against the connected RPC status endpoint. `repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet` should consume the same params version and show the active version in internal metrics.

Ownership should be explicit. person:MdTeach drafts the protocol params. person:prajwolrg approves protocol readiness. person:krsnapaudel owns deployed endpoint shape, DNS, bootnodes, and metrics. person:Rajil1213 and person:ProofOfKeags own bridge watcher compatibility. person:bewakes reviews consensus-facing defaults. person:john-light waits for the frozen bundle before publishing public docs.

**Trade-offs**

Versioning params instead of mutating them adds ceremony, but it makes resets legible. If a faucet, bridge watcher, or explorer is on `prague-0` while infra has moved to `prague-1`, the mismatch is immediately visible.

Embedding Prague params in binaries improves operator UX but risks stale binaries connecting to dead infrastructure. The startup status checks and minimum software version field reduce that risk. For bridge components, startup should fail closed on params mismatch.

Using 6 Bitcoin confirmations is conservative for Signet and may slow deposit demos. The alternative is 1-2 confirmations for better UX. I prefer the conservative default because the current tension is bridge validation depending on infra stability; a loose reorg policy will create ambiguous failures. We can expose a non-default demo override if devrel needs it.

Keeping node profiles as config overlays avoids separate networks by accident. The cost is stricter validation logic in startup config parsing. That is worth it because most prior drift came from “almost Prague” configs copied between repos.

**Rollout Plan**

1. By 2025-12-20, person:MdTeach publishes the first `prague-0-rc1` chain-params artifact in `repo:alpen`, with provisional genesis fields clearly marked. person:bewakes and person:prajwolrg review consensus fields.

2. By 2025-12-23, person:krsnapaudel confirms endpoint shape: sequencer RPC, P2P bootnodes, metrics labels, public RPC URL, and DNS names. The artifact is regenerated as `prague-0-rc2`.

3. By 2025-12-26, person:Rajil1213 and person:ProofOfKeags wire `repo:strata-bridge` startup validation against the RPC status endpoint and confirm deposit watcher assumptions: Bitcoin network, confirmation depth, rollup lag, and reorg behavior.

4. By 2025-12-28, dependent repos pin the same params version: `repo:strata-p2p`, `repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet`. Any repo still carrying hand-written Prague constants should be treated as blocking.

5. By 2025-12-30, freeze `prague-0` if no consensus or bridge-blocking issue remains. After freeze, changes require either a documented override that does not affect genesis or a new `prague-1` reset.

6. By 2025-12-31, person:john-light and person:pramodkandel can draft public support material from the frozen params, while ops confirms faucet funding and dashboard visibility.
