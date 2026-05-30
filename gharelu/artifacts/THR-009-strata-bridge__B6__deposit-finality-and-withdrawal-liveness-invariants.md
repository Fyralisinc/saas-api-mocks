## Goal

Define the bridge state-machine invariants we require for deposit finality and withdrawal liveness in `product:strata-bridge`, and separate those invariants from adjacent assumptions about operator key rotation. This document is scoped to the audit finding from B6: our design doc claimed stronger safety properties than the implementation could currently enforce, mainly because deposit finality, withdrawal liveness, and key rotation were described as one coupled bridge-safety argument.

The goal is to make the bridge safety envelope reviewable by person:Rajil1213, person:ProofOfKeags, person:prajwolrg, person:MdTeach, person:krsnapaudel, and research reviewers without relying on informal knowledge of the implementation. In particular, we should be able to answer:

1. When is a Bitcoin deposit final enough to cause a Strata-side credit?
2. Under what assumptions can a valid withdrawal request eventually become a Bitcoin transaction?
3. Which safety and liveness claims break when bridge operator keys rotate, stall, or disagree?

This document does not claim the current devnet bridge fully satisfies every invariant below. It defines the target invariant set and the rollout path for making the implementation, tests, and audit evidence match.

## Non-goals

This is not a full bridge protocol specification. It does not define the complete custody script, federation policy, watchtower behavior, relayer economics, or fraud response process.

This is not a redesign of `product:strata` event contracts. We assume the Strata execution layer emits bridge events with stable identifiers, ordered inclusion, and enough metadata for the bridge state machine to bind Bitcoin-side observations to Strata-side transitions. The early delivery plan assumed these contracts were stable too soon; this document treats them as an explicit dependency.

This is not a key-rotation implementation plan. Key rotation gets its own invariant family because rotation can preserve deposit safety while temporarily reducing withdrawal liveness, or preserve withdrawal liveness while complicating replay protection. We only define the boundary conditions that the bridge state machine must expose to a future rotation design.

## Background

The bridge state machine spans Bitcoin confirmation tracking, Strata event consumption, operator signing, and withdrawal broadcast. Ownership has crossed protocol, bridge, infra, and research boundaries, which made it easy for safety language to drift away from executable behavior.

During B6, person:AaronFeickert reviewed the bridge safety assumptions with person:Rajil1213 and person:ProofOfKeags. The main issue was not that the bridge lacked safety thinking; it was that the document compressed several different properties into a single “bridge is safe after N confirmations” statement. That is too broad.

Bitcoin finality is probabilistic and chain-relative. A deposit output can be deeply confirmed on one best chain and later disappear under a sufficiently expensive reorg. The bridge must therefore define a finality threshold and make all Strata-side credit decisions conditional on that threshold. The invariant is not “Bitcoin deposits are final”; it is “the bridge never credits a deposit unless its observed outpoint is buried under the configured confirmation depth on the canonical Bitcoin chain view used by the bridge.”

Withdrawal liveness is different. A withdrawal can be valid in Strata state and still fail to land on Bitcoin because fee estimates are stale, signers are unavailable, the mempool rejects a package, the custody UTXO set is fragmented, or the active operator key set no longer corresponds to the withdrawal authorization path. Liveness must be conditional on honest operator quorum, available funds, fee policy, Bitcoin network propagation, and no unresolved key-rotation transition.

Key rotation is a third property family. It changes which operators can authorize withdrawals and may change which Bitcoin scripts are spendable. Rotation must not cause double credit, replay of already-finalized withdrawals, or permanent loss of funds. But until rotation is implemented and tested, the bridge should not claim rotation safety as part of normal deposit/withdrawal safety.

## Proposed Design

We define the bridge state machine around explicit monotonic transitions and invariant checks.

Deposit states:

`Observed -> Confirming -> Finalized -> Credited -> Reorged`

A deposit enters `Observed` when the bridge detects a Bitcoin transaction paying the recognized deposit script or address derivation path. It enters `Confirming` once the transaction appears in the bridge’s canonical Bitcoin chain view. It enters `Finalized` only after `min_deposit_confirmations` confirmations. It enters `Credited` only after the Strata-side credit event is emitted and committed with a deterministic deposit identifier.

Deposit invariant D1: no deposit may be credited unless it references a Bitcoin outpoint in `Finalized`.

Deposit invariant D2: the deposit identifier must commit to `txid`, `vout`, amount, deposit script or derivation tag, Bitcoin block hash, and finalized height. This prevents ambiguity if two observers discover the same payment through different indexes.

Deposit invariant D3: if a credited deposit is later invalidated by a Bitcoin reorg deeper than the configured threshold, the state machine must enter an explicit exceptional state rather than silently rewriting history. The response can be governance/manual recovery on devnet, but the transition must be observable.

Deposit invariant D4: duplicate observation of the same outpoint is idempotent. Multiple relayers or indexers may report the same deposit, but only one credit transition is valid.

Withdrawal states:

`Requested -> Authorized -> Signing -> Broadcast -> Confirming -> Completed | FailedRetryable | FailedFinal`

A withdrawal enters `Requested` from a finalized Strata event with a unique withdrawal identifier, amount, destination script, and fee policy. It enters `Authorized` once the bridge has verified the event against finalized Strata state. It enters `Signing` when the active operator set begins producing signatures. It enters `Broadcast` when a valid Bitcoin transaction is accepted for propagation. It enters `Completed` after the configured Bitcoin confirmation threshold.

Withdrawal invariant W1: every valid finalized withdrawal request must either progress toward broadcast or produce a machine-readable blocker reason.

Withdrawal invariant W2: withdrawals are processed exactly once by identifier. Replacement transactions for fee bumping must retain the same withdrawal identifier and must not allow duplicate settlement.

Withdrawal invariant W3: a withdrawal transaction must spend only UTXOs assigned to the bridge custody set and must pay the requested destination script and amount, net of the documented fee policy.

Withdrawal invariant W4: liveness is conditional. If honest quorum is online, the active key set matches the custody script, sufficient confirmed bridge liquidity exists, and fee policy permits confirmation, then a valid withdrawal should eventually reach `Completed`.

Withdrawal invariant W5: failures split into retryable and final. Fee underpayment, transient signer outage, and mempool rejection are retryable. Invalid destination policy, already-spent custody inputs, or mismatch with finalized Strata state are final until manual intervention or protocol repair.

Key-rotation boundary invariants:

K1: a withdrawal request binds to an operator epoch.

K2: deposits do not depend on operator epoch unless the deposit script itself changes.

K3: rotation must not make already-authorized withdrawals ambiguous. Either the old key set remains responsible for withdrawals authorized before the epoch boundary, or the new key set explicitly re-authorizes them under a deterministic migration rule.

K4: the state machine must expose `RotationPending`, `RotationActive`, and `RotationFailed` as first-class bridge conditions, not hidden signer errors.

person:Rajil1213 and person:ProofOfKeags should own the bridge-state implementation mapping. person:prajwolrg and person:MdTeach should review the Strata event assumptions. person:krsnapaudel should review indexer and bitcoind client behavior, especially reorg handling and RPC consistency. person:AaronFeickert and person:uncomputable should review the invariant language before we treat it as audit evidence.

## Trade-offs

The main trade-off is explicitness versus delivery speed. Modeling exceptional states like `Reorged`, `FailedFinal`, and `RotationPending` adds implementation work and more cases for tests, but it prevents the bridge from collapsing materially different risks into generic “failed” states.

A second trade-off is conservative finality. Higher Bitcoin confirmation thresholds reduce reorg risk but delay deposit crediting and make devnet flows feel slower. We should keep the threshold configurable by environment while documenting the production value separately from local/devnet defaults.

A third trade-off is conditional liveness language. It is less satisfying than a blanket guarantee, but it is technically correct. Bitcoin withdrawal liveness depends on fee markets, signer availability, UTXO management, and transaction relay. The design should surface blockers instead of pretending the bridge can force confirmation.

The final trade-off is key rotation isolation. Deferring rotation invariants from the first devnet scope reduces implementation pressure, but it means the bridge cannot yet claim complete operational resilience. That is acceptable if the limitation is documented and visible in release notes.

## Rollout Plan

1. Update `repo:bridge-sm-design-docs` with the invariant families above and remove any language implying unconditional bridge safety after deposit confirmation.

2. Add state-machine assertions in `repo:strata-bridge` for D1, D2, D4, W2, and W3 first. These are local safety checks and should be testable without full operator-rotation support.

3. Add reorg tests using the Bitcoin client harness from `repo:bitcoind-async-client` and relevant bridge integration fixtures. The minimum useful test is: observe deposit, advance to finality, credit once, reorg below finality, and verify the exceptional state is emitted rather than duplicate credit.

4. Add withdrawal liveness instrumentation before claiming liveness. The bridge should emit blocker reasons for signer quorum unavailable, insufficient confirmed liquidity, fee policy rejected, mempool rejection, and operator epoch mismatch.

5. Keep key rotation marked unsupported or partial until K1-K4 have implementation hooks. person:Rajil1213 should coordinate with person:krsnapaudel and person:ProofOfKeags on the operational state exposure, while person:AaronFeickert validates that the documented limitation is precise enough for audit review.

6. Before the next devnet release, require a short sign-off checklist: deposit finality tests passing, duplicate deposit tests passing, withdrawal identifier replay tests passing, blocker reasons visible in logs/metrics, and design docs updated to match implementation behavior.
