# RFC: Emergency Pause and Operator Responsibility Split

## Summary

This RFC proposes a split between the normal `strata-bridge` peg-out operator path and a narrowly scoped emergency pause path. The goal is to reduce the amount of bridge safety responsibility carried by the online operator set while preserving a bounded mechanism to stop withdrawal finalization when we have evidence of bridge state-machine divergence, invalid Strata event interpretation, or Bitcoin-side signing risk.

The concrete proposal is:

1. Normal peg-out processing remains an operator-driven state-machine transition over finalized Strata withdrawal events.
2. Emergency pause becomes a separate bridge control action with explicit activation conditions, expiry semantics, audit logging, and recovery procedure.
3. Operators do not decide bridge policy during the pause. They only stop progressing affected transitions and continue publishing observability data.
4. Pause authority is separated from routine operator keys and should require a higher-threshold, slower path than normal bridge operation.
5. The bridge state machine must model pause as an external guard over finalization/signing, not as an ordinary withdrawal state.

This is intentionally smaller than the original bridge hardening scope. It addresses the design concern raised during B9: our withdrawal path should not make online operators responsible for both liveness and emergency governance. person:Rajil1213 can keep bridge implementation ownership over the state-machine changes, while person:ProofOfKeags and I drive the bridge-side safety document. person:AaronFeickert should review the authority and failure assumptions, especially around whether pause can be abused to create indefinite withdrawal censorship.

## Motivation

The early bridge plan assumed stable Strata event contracts earlier than was realistic. We treated the operator as if it were consuming a clean, finalized stream of canonical withdrawal facts from `strata`, then deterministically signing Bitcoin transactions after local validation. In practice, the contract between `strata`, `strata-bridge`, `bitcoin-bosd`, and the async Bitcoin client has been moving under us.

The result is an awkward ownership boundary:

- protocol owns the event semantics;
- bridge owns withdrawal state transitions and signing policy;
- infra owns devnet reliability and observability;
- research is still validating the security model around pause, fraud evidence, and operator assumptions.

That boundary is fine if the components are small and explicit. It is not fine if the online bridge operator becomes the implicit place where unresolved protocol questions are handled.

The specific issue is emergency handling. There are real cases where the correct bridge action is “stop signing for now”:

- Strata emits a withdrawal event that passes schema validation but violates a bridge invariant.
- The bridge observes conflicting finalized checkpoints or an unexpected reorg boundary.
- The Bitcoin funding UTXO, fee policy, or signing session state does not match the local bridge database.
- A bug in event indexing causes replay, omission, or duplicate transition execution.
- The operator set detects that it may produce a valid Bitcoin transaction for an invalid withdrawal.

In the current mental model, those cases blur together with ordinary operator responsibility. That pushes too much judgment into code that should be deterministic and too much authority into keys that should mostly be online and boring.

The devnet release gave us useful coverage, but it did not exercise the full original bridge scope. We should use that fact constructively: harden the minimal withdrawal path now, and keep emergency authority out of the hot operator loop before we scale the bridge surface.

## Detailed design

### State-machine boundary

The bridge withdrawal state machine should continue to represent normal peg-out lifecycle states, for example:

- `Observed`
- `Validated`
- `BatchCandidate`
- `SigningRequested`
- `Signed`
- `Broadcast`
- `Confirmed`
- `Failed`

Pause should not be inserted as a withdrawal state like `Paused`. A paused withdrawal is not semantically a different withdrawal. Instead, pause is a global or scoped guard evaluated before irreversible actions.

The guarded actions are:

- creating a signing request;
- participating in a threshold signing session;
- broadcasting a signed Bitcoin transaction;
- marking an externally broadcast transaction as accepted if it was produced while paused.

Read-only indexing, validation, and metric publication should continue during pause. This is important because the pause period is when we most need evidence. person:krsnapaudel should make sure infra dashboards distinguish “operator not progressing because paused” from “operator unhealthy.”

### Pause scope

The first implementation should support two scopes:

1. `GlobalBridgePause`: prevents all new peg-out signing and broadcast.
2. `WithdrawalRangePause`: prevents signing/broadcast for withdrawal IDs or Strata event heights in a bounded range.

I prefer implementing the data model with a general pause record:

```text
pause_id
scope
reason_code
activated_at_strata_height
activated_at_bitcoin_height
expires_at_wall_clock
authorized_by
evidence_hash
status
```

But the first release may only expose `GlobalBridgePause` if range scoping creates too much ambiguity. The internal representation should not block range scoping later.

Reason codes should be structured, not free text:

- `STRATA_EVENT_CONTRACT_VIOLATION`
- `BRIDGE_DB_INVARIANT_FAILURE`
- `BITCOIN_SIGNING_RISK`
- `CHECKPOINT_OR_FINALITY_CONFLICT`
- `IMPLEMENTATION_BUG`
- `MANUAL_AUDIT_HOLD`

Free-form notes can exist, but automation and dashboards should key off reason codes.

### Authority split

Routine bridge operators should not hold unilateral pause authority. The online operator role is to execute the bridge protocol under known rules. Emergency pause is a governance and safety function.

Proposed authority model for devnet and early testnet:

- normal operator keys: continue current role for observation and signing;
- pause keys: held separately by a small emergency group;
- activation threshold: higher than one person, lower latency than full company consensus;
- expiry: required on every pause action;
- extension: requires a new authorization, not silent renewal.

For initial deployment, I suggest pause authorization requires at least two of:

- person:uncomputable
- person:Rajil1213
- person:ProofOfKeags
- person:AaronFeickert

This is not a long-term governance proposal. It is a concrete starting point for the next bridge devnet/testnet cycle. Before mainnet-like assumptions, we need a stronger key custody and accountability model.

### Operator behavior during pause

When a pause is active, an operator must:

1. continue following Strata and Bitcoin heads;
2. continue indexing bridge-relevant events;
3. continue validating withdrawals up to the guarded action boundary;
4. refuse to create or participate in signing sessions covered by the pause scope;
5. refuse broadcast of locally held signed transactions covered by the pause scope;
6. emit metrics and structured logs containing `pause_id`, scope, and blocked action.

An operator must not:

- mutate withdrawal state to a terminal failure solely because of pause;
- discard pending withdrawal data;
- reinterpret Strata events using ad hoc local rules;
- resume automatically after expiry without rechecking bridge invariants.

Expiry means the pause authorization is no longer valid. It does not mean the bridge blindly resumes. On expiry, operators should enter `resume_check_required` locally and run deterministic checks before progressing guarded actions.

### Resume checks

Resume requires deterministic verification of:

- Strata finalized height is at or beyond the pause activation height.
- No conflicting bridge events exist in the paused range.
- Withdrawal database state matches replay from finalized Strata events.
- No signing sessions were completed during the pause for affected withdrawals.
- Bitcoin-side UTXOs and pending transactions match expected bridge state.
- All operators agree on the next signable withdrawal batch root.

The batch root should be a compact commitment over withdrawal IDs, amounts, destination scripts, source event identifiers, and fee policy. person:MdTeach and person:prajwolrg should review the exact commitment fields from the protocol side so we do not accidentally omit a consensus-relevant value.

### Evidence and audit trail

Every pause activation should include an `evidence_hash`, even if the initial evidence is a short incident note stored in `bridge-sm-design-docs`. We should not allow “someone said stop” to be the only durable record.

The bridge should write an append-only local audit record for:

- pause observed;
- guarded action blocked;
- resume check started;
- resume check passed or failed;
- first post-resume signing action.

For devnet, logs are enough. For later testnet, these records should be exportable and comparable across operators. person:krsnapaudel and person:Zk2u should align on where these records live operationally.

### Implementation plan

Implementation should be split into three PR tracks:

1. `strata-bridge`: add pause record parsing, guarded action checks, metrics, and local resume checks.
2. `bridge-sm-design-docs`: define state-machine guard semantics, activation conditions, and incident examples.
3. `strata` integration: stabilize the event identifiers and finality fields needed for deterministic replay.

person:Rajil1213 should drive the bridge state-machine implementation. person:ProofOfKeags should review operator behavior against the bridge code. I will own the safety model text and coordinate research review with person:AaronFeickert. person:prajwolrg and person:MdTeach should review event-contract assumptions before we treat any replay check as sufficient.

## Drawbacks

This adds another control path to an already cross-team bridge system. If we implement it poorly, pause becomes a vague manual override rather than a precise safety mechanism.

It also introduces a censorship vector. Any emergency pause authority can delay withdrawals. Expiry limits the blast radius but does not remove the risk, especially if repeated pauses are allowed under weak evidence.

There is also implementation complexity around signed-but-not-broadcast transactions. If the bridge has already produced a valid Bitcoin transaction before pause activation, the system may not be able to prevent external broadcast. The pause mechanism must be documented as preventing operator participation and broadcast, not as a magical rollback of Bitcoin-side artifacts.

Finally, this may slow delivery. The original bridge scope already slipped because safety documentation lagged implementation. This RFC intentionally keeps the first version narrow, but even narrow emergency handling needs careful tests.

## Alternatives considered

One alternative is to keep pause as an operator-local kill switch. That is simple, but it makes safety behavior inconsistent across operators and creates unclear accountability. It also does not solve the authority problem; it just hides it in deployment config.

Another alternative is to model pause as a normal withdrawal state. I reject this because pause is not a property of a withdrawal. It is a guard on bridge actions. Encoding it as state risks corrupting replay semantics and makes resume harder to reason about.

A third alternative is no pause mechanism: rely on conservative validation and refuse invalid withdrawals. This is attractive in a fully mature system, but we are not there. While Strata event contracts and bridge replay checks are still stabilizing, we need an explicit stop condition for cases where the validator itself may be wrong.

A fourth alternative is a protocol-level pause emitted by Strata. That may be useful later, but it couples bridge emergency response to protocol governance and Strata release timing. For this phase, bridge pause should be bridge-local while consuming finalized Strata data.

## Open questions

1. What is the maximum acceptable pause duration for devnet, testnet, and mainnet-like deployments?
2. Should range-scoped pause ship in the first implementation, or should we start with global pause only?
3. Who exactly holds pause authority after devnet, and what key custody rules apply?
4. What evidence threshold is sufficient to activate pause without creating a low-friction censorship tool?
5. Should resume require cross-operator agreement on a batch root, or is local deterministic replay enough for the first release?
6. How do we handle transactions signed before pause but broadcast during pause by an external party?
7. Which Strata event fields are stable enough to commit into the withdrawal batch root?
8. Where should long-term pause audit records live: bridge database, infra log pipeline, or a dedicated incident repository?

My recommendation is to accept the split now, implement global pause first, and require a follow-up RFC before any mainnet-like deployment that defines authority, custody, maximum duration, and public accountability.
