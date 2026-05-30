## Goal

Ship the public testnet bridge deposit path with enough correctness, observability, and recovery surface that users can move test BTC into `product:strata` without us pretending the bridge is fully autonomous.

For B4, the target is narrower than “complete bridge.” We need a reliable BTC deposit detection path in `repo:strata-bridge`, event propagation into `product:strata`, and public-facing status/error behavior that `person:john-light` can document without caveats changing daily. The design should support deposits from ordinary Bitcoin testnet wallets, map each deposit to the intended Strata recipient, wait for configured confirmations, and produce an auditable internal state transition for bridge operators.

The launch posture is: public availability first, with explicit operational controls. Deposits should be hard to lose, easy to inspect, and recoverable manually if an indexer, RPC endpoint, or relayer component stalls.

## Non-goals

This does not deliver a trust-minimized bridge end state. We are not proving Bitcoin inclusion inside the rollup circuit for public testnet launch, nor are we enforcing bridge correctness entirely through ZK verification.

This does not finalize withdrawal symmetry. Withdrawal proving, Bitcoin transaction construction, signing policy, and withdrawal challenge semantics are separate design tracks owned with `person:ProofOfKeags`, `person:prajwolrg`, and the protocol team.

This does not optimize for minimum deposit latency. We will keep conservative Bitcoin confirmation requirements even if that creates friction for devrel demos. The bridge is public infrastructure, and bad early accounting is more expensive than slower deposits.

This does not remove manual recovery. For launch, manual intervention remains part of the accepted operating model, provided the system records enough evidence for `person:krsnapaudel`, `person:Rajil1213`, and ops to reconcile state safely.

## Background

The public testnet launch combines `repo:alpen`, `repo:strata-bridge`, `repo:strata-p2p`, `repo:alpen-faucet`, `repo:alpen-dashboards`, and `repo:checkpoint-explorer`. During B4, bridge and faucet work are moving in parallel. I am finishing deposit event plumbing while `person:ProofOfKeags` keeps bridge sequencing/recovery aligned, `person:krsnapaudel` waits on final RPC hostnames, and `person:john-light` needs stable public behavior for docs.

The deposit path currently has three practical constraints.

First, Bitcoin testnet is unreliable enough that our bridge cannot treat mempool observation as meaningful finality. RPC nodes can disagree, reorgs happen, and public users will paste transactions from wallets we do not control. We need indexed block evidence, not just transaction broadcast evidence.

Second, the public faucet and bridge UX are coupled in users’ minds even if they are separate systems. If the faucet rate-limits too aggressively or returns opaque failures, users will blame the bridge. If bridge confirmations are higher than expected, users will assume deposits are stuck. We need status messages that distinguish “seen,” “confirming,” “credited,” “rejected,” and “operator review.”

Third, the launch scope trades bridge completeness for public availability. That means the design has to be honest about centralized components: bridge watcher, deposit state store, relayer, and manual operator controls. We should avoid language or code paths that imply more autonomy than exists.

## Proposed design

The deposit path is a state machine backed by durable event records. Each deposit has a canonical key:

`bitcoin_txid:vout -> deposit_address -> strata_recipient -> amount_sats`

The bridge watcher in `repo:strata-bridge` monitors Bitcoin testnet blocks using the configured RPC endpoint supplied by infra. For public testnet we should prefer block polling over wallet notifications. Polling is slower but easier to make deterministic, replayable, and portable across RPC providers. `person:krsnapaudel` owns final RPC hostnames and endpoint health checks; the bridge service should treat hostnames as configuration and expose endpoint identity in metrics.

Deposit addresses are generated or registered with embedded recipient metadata according to the current bridge format. The watcher scans each new block for outputs paying to known deposit scripts. On match, it writes a `DepositObserved` record with block hash, block height, txid, vout, amount, scriptPubKey, recipient, and first-seen timestamp. This record is immutable except for attaching derived status.

Confirmations are computed against the active best chain. A deposit moves:

`observed -> confirming -> confirmed -> relayed -> credited`

If the block containing the deposit is disconnected, the deposit moves to `reorged` and can later return to `confirming` if the transaction appears in a new canonical block. We should not delete the original observation. Keeping both the old block hash and the replacement block hash gives us the audit trail needed for incident review.

The confirmation threshold should be high enough to survive normal testnet instability. I propose defaulting to 6 confirmations for public testnet launch, with an environment override but no public promise that the value is fixed. `person:john-light` can document “deposits are credited after several Bitcoin confirmations” and the UI/API can expose the exact current remaining count. This handles the tension that devrel expected lower confirmations while preserving bridge safety.

Once confirmed, the bridge relayer submits a deposit event into `product:strata`. The event payload should include the canonical deposit key, Bitcoin inclusion metadata, recipient, and amount. The Strata side must reject duplicate canonical keys. Idempotency is more important than relayer simplicity: if the relayer restarts after submitting but before persisting local success, replaying the same event should be safe.

`person:prajwolrg`, `person:MdTeach`, `person:storopoli`, and `person:bewakes` should review the Strata-side validation boundary: event schema, duplicate rejection, amount accounting, and failure modes if the bridge submits malformed metadata. The public testnet path can rely on bridge authority, but the protocol code should still keep explicit invariants and logs.

For user-visible status, `repo:strata-bridge` exposes a read API consumed by bridge UI/docs and dashboards:

- `not_found`: no known matching deposit
- `observed`: transaction seen in a block but below minimum confirmations
- `confirming`: known deposit with `n / required` confirmations
- `credited`: relayed and accepted by Strata
- `reorged`: previously observed but no longer canonical
- `review_required`: amount/script/recipient mismatch or relayer failure requiring operator action

Errors must be stable and plain enough for public docs. `person:john-light` should not have to explain internal component names. Internally we still emit detailed labels for RPC failure, chain reorg, duplicate key, relayer rejection, malformed deposit, below-minimum amount, and unknown address.

For observability, `repo:alpen-dashboards` should track: Bitcoin watcher tip height, Strata tip height, RPC lag, observed deposits, confirmed deposits, relayed deposits, relayer failures, reorg count, average confirmation wait, and deposits in `review_required`. `repo:checkpoint-explorer` does not need to become a bridge explorer, but it should link cleanly to credited deposit events when useful.

Manual recovery is an explicit operator path. We need a CLI/admin command that can replay a confirmed deposit by canonical key, mark a malformed deposit as rejected with reason, and export all evidence for a deposit. `person:sapinb` should have a runbook that says when to escalate to bridge engineering versus infra. No manual command should mutate amount, recipient, txid, or vout after observation; recovery can replay or annotate, not rewrite facts.

## Trade-offs

The main trade-off is latency versus correctness. Six confirmations will feel slow in demos, especially when faucet usage is also rate-limited. The alternative is accepting shallow Bitcoin history and spending launch week explaining reorg-induced accounting bugs. I prefer slow and explainable.

Polling Bitcoin RPC is less elegant than event subscriptions and may add small delays. It is also easier to replay from height N, easier to compare across providers, and easier for infra to debug. Given that final RPC hostnames are still settling, this is the right launch choice.

The design keeps bridge authority centralized. That is acceptable for public testnet if we are explicit. It also gives protocol reviewers a smaller boundary: Strata accepts authorized deposit events and enforces local invariants, while the bridge service owns Bitcoin observation and operational recovery.

Manual recovery creates operator risk. The mitigation is append-only evidence, idempotent replay, and narrow commands. We should optimize for recovering from stuck deposits, not for giving operators broad database-edit powers.

## Rollout plan

1. `person:Rajil1213` finishes deposit event plumbing in `repo:strata-bridge`: canonical key construction, observed/confirmed state transitions, relayer submission, and idempotent replay behavior.

2. `person:krsnapaudel` provides final public testnet Bitcoin RPC and Strata RPC hostnames, plus health-check expectations. Bridge config should be environment-driven and visible in metrics without leaking credentials.

3. `person:prajwolrg`, `person:MdTeach`, `person:storopoli`, and `person:bewakes` review the Strata-side deposit event schema and duplicate rejection path in `repo:alpen`.

4. `person:ProofOfKeags` and `person:Rajil1213` run recovery drills: relayer restart after submit, Bitcoin RPC outage, reorg simulation, duplicate replay, and malformed deposit review.

5. `person:john-light` documents public behavior using stable states and conservative language around confirmations. Docs should avoid promising exact credit time.

6. `person:krsnapaudel` wires `repo:alpen-dashboards` panels for watcher lag, relayer failures, and deposits requiring review.

7. Before launch window close, we freeze public error codes and run an end-to-end deposit from faucet-funded testnet BTC through credited Strata balance, with logs and dashboard screenshots attached to the launch checklist.
