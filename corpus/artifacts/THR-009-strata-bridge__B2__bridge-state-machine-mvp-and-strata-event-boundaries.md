# RFC: Bridge State Machine MVP and Strata Event Boundaries

## Summary

This RFC defines the MVP boundary for the `product:strata-bridge` state machine and the event contract we need from `product:strata` before bridge implementation can be treated as stable. The goal is to reduce ambiguity around peg-in, peg-out, cancellation, and Bitcoin reorg handling while keeping the first implementation narrow enough to ship into devnet without encoding protocol assumptions we will later regret.

The bridge state machine should own bridge-local state transitions, Bitcoin transaction observation, and bridge action eligibility. It should not infer Strata consensus state from loosely typed application events or from implementation details of the Strata node. Strata should expose a small, versioned event surface that is explicit about finality, ordering, rollback, and bridge-relevant payloads.

For the MVP, I propose we support:

- Peg-in recognition after a configurable Bitcoin confirmation threshold.
- Peg-out request tracking from Strata-originated events.
- Peg-out fulfillment tracking on Bitcoin.
- Explicit cancellation state for peg-outs that are no longer executable.
- Reorg-aware rollback of Bitcoin-observed state up to a configured depth.
- A typed event boundary between Strata and the bridge state machine.

This does not attempt to complete the full bridge scope. In particular, it does not define production federation operations, all fraud/escape paths, or long-horizon liveness policy. Those remain design work for later beats. The point here is to make the state machine auditable and testable before we expand the surface.

## Motivation

The current bridge design is carrying too many implicit assumptions. During the first pass in `repo:bridge-sm-design-docs`, review from person:prajwolrg and person:AaronFeickert surfaced two gaps that are too important to leave informal: peg-out cancellation semantics and Bitcoin reorg handling. Both directly affect user funds and operator behavior.

The early delivery plan also assumed Strata event contracts would stabilize quickly. That has not happened. Protocol work is moving in parallel, and the bridge cannot safely bind itself to ad hoc event shapes from `repo:alpen` or internal sequencing decisions that may still change. We need a crisp boundary so person:MdTeach, person:prajwolrg, and the protocol team can evolve Strata internals without breaking bridge safety, while person:ProofOfKeags and I can implement bridge logic against a stable enough interface.

Ownership is another practical issue. The bridge spans protocol, infra, and research. `repo:strata-bridge` depends on Bitcoin indexing, Strata event ingestion, transaction construction, and safety policy. If we do not write down what each layer owns, we will keep resolving design questions during implementation or audit review. That already happened once: safety documentation lagged implementation and became review-driven rather than design-driven.

The MVP should therefore do less, but define the safety boundaries more tightly.

## Detailed design

### State machine responsibility

The bridge state machine is the bridge authority over bridge-local lifecycle state. It consumes events from Bitcoin and Strata, validates them against local policy, and advances records through explicit states.

It should maintain at least two top-level entities:

- `PegIn`: a Bitcoin-side deposit candidate and its Strata minting status.
- `PegOut`: a Strata-side withdrawal request and its Bitcoin fulfillment status.

The bridge state machine must be deterministic over its ordered input stream. Given the same initial snapshot and same ordered event sequence, it should produce the same state and same pending actions. This matters for auditability and for replay tests.

The state machine should not directly query Strata internals to discover bridge state. It may consume Strata events and call narrow APIs for proofs or block data if needed, but those calls must be modeled as inputs with explicit versions and finality assumptions.

### Bitcoin event model

Bitcoin observations should come from a chain tracker backed by `repo:bitcoind-async-client` or equivalent infra, with normalization inside `repo:strata-bridge`. The state machine consumes normalized Bitcoin events:

```text
BitcoinBlockConnected { hash, height, prev_hash }
BitcoinBlockDisconnected { hash, height }
BitcoinTxObserved { txid, block_hash, height, outputs, inputs }
BitcoinTxConfirmed { txid, confirmations }
BitcoinTxFinalized { txid, height, confirmations }
```

For MVP, finality is policy-based, not absolute. We should define:

```text
PEG_IN_MIN_CONFIRMATIONS = 6
PEG_OUT_FULFILLMENT_MIN_CONFIRMATIONS = 6
MAX_REORG_TRACKING_DEPTH = 100
```

These values should be configurable per environment, with devnet defaults lower than signet/mainnet-like environments. person:krsnapaudel should own infra configuration wiring, while bridge logic treats them as injected policy.

A Bitcoin reorg must be represented as disconnected blocks followed by connected blocks. The state machine must reverse effects for any non-finalized observations in disconnected blocks. If a finalized transaction is reorged out, the state machine should enter `NeedsOperatorReview`, not silently recover. That case indicates either policy failure, extremely deep reorg, or incorrect finality handling.

### Peg-in lifecycle

A peg-in starts when the bridge observes a Bitcoin transaction paying to a recognized deposit script.

Proposed states:

```text
Detected
Confirming
Confirmed
SubmittedToStrata
AcceptedByStrata
Rejected
ReorgedOut
NeedsOperatorReview
```

The state machine may move from `Detected` to `Confirming` once the transaction is in a connected block. It moves to `Confirmed` once the configured confirmation threshold is met. It moves to `SubmittedToStrata` only after the bridge has emitted or recorded the action to submit the peg-in proof/mint request to Strata.

`AcceptedByStrata` requires an explicit Strata event. The bridge should not infer success from absence of error or from local submission success.

If a Bitcoin reorg removes the deposit before confirmation, the peg-in moves to `ReorgedOut` or back to `Detected` if the transaction appears in mempool tracking. For MVP I prefer `ReorgedOut` plus rediscovery through a new observation, because it keeps replay behavior simple.

### Peg-out lifecycle

A peg-out starts from a Strata event, not from bridge-local intent. The bridge should only construct or broadcast Bitcoin fulfillment transactions for peg-outs that Strata has made eligible.

Proposed states:

```text
Requested
Eligible
Signing
Broadcast
Confirming
Fulfilled
Cancelled
Expired
Rejected
NeedsOperatorReview
```

`Requested` means Strata has emitted a bridge withdrawal request. `Eligible` means the request is mature and executable under Strata rules. These may be the same event in early devnet, but the event type should still distinguish them or include an explicit eligibility field.

`Signing` and `Broadcast` are bridge-local operational states. They must not be interpreted as Strata state. If a broadcast transaction is reorged out before finality, the peg-out returns to `Eligible` or `Broadcast` depending on whether the transaction remains known and valid. If the transaction conflicts with another observed spend, the state machine must enter `NeedsOperatorReview`.

### Cancellation semantics

Cancellation cannot be an implementation afterthought. A peg-out cancellation means a previously requested withdrawal must no longer be fulfilled by the bridge. The source of truth for cancellation is Strata, not the bridge operator.

For MVP, cancellation should be represented by an explicit Strata event:

```text
BridgePegOutCancelled {
  request_id,
  strata_block_id,
  reason,
  supersedes_event_id?
}
```

Allowed cancellation reasons:

```text
UserCancelled
Expired
InvalidatedByReorg
ProtocolRejected
OperatorUnsafe
```

The bridge must handle cancellation differently depending on local state:

- If state is `Requested` or `Eligible`, move to `Cancelled` and remove pending actions.
- If state is `Signing`, abort signing if possible; otherwise move to `NeedsOperatorReview`.
- If state is `Broadcast` or `Confirming`, cancellation is too late unless the Bitcoin transaction is not actually confirmed and can be safely abandoned. Default to `NeedsOperatorReview`.
- If state is `Fulfilled`, cancellation is invalid and should be recorded as a protocol inconsistency.

This conservative behavior is intentional. Once a Bitcoin transaction has been broadcast, the bridge cannot promise cancellation. Strata events must not imply that Bitcoin settlement can be undone.

person:uncomputable should review this part from the bridge research side, because cancellation semantics touch user expectations and any later escape hatch design.

### Strata event boundary

The bridge needs a versioned event interface from Strata. I propose the following minimal event set:

```text
BridgePegInAccepted {
  deposit_txid,
  deposit_vout,
  strata_block_id,
  recipient,
  amount,
  event_id
}

BridgePegInRejected {
  deposit_txid,
  deposit_vout,
  strata_block_id,
  reason,
  event_id
}

BridgePegOutRequested {
  request_id,
  strata_block_id,
  recipient_script,
  amount,
  fee_policy,
  event_id
}

BridgePegOutEligible {
  request_id,
  strata_block_id,
  event_id
}

BridgePegOutCancelled {
  request_id,
  strata_block_id,
  reason,
  event_id
}

StrataBridgeEventReverted {
  event_id,
  strata_block_id,
  reason
}
```

Every event must include:

- Stable `event_id`.
- Strata ordering key.
- Event schema version.
- Whether the event is provisional or finalized under Strata’s own finality model.

The bridge must be able to process provisional events, but it should only perform irreversible Bitcoin actions from finalized or policy-accepted events. For devnet, we can use a weaker policy, but the event type should not be devnet-specific.

The key rule is: Strata must tell the bridge when bridge-relevant state changes, and must tell the bridge when those changes are reverted. The bridge should not derive rollback by polling current state and diffing.

person:prajwolrg and person:MdTeach should confirm whether this event model maps cleanly to the current protocol implementation. If it does not, we should change the event boundary before changing bridge state transitions.

### Testing and documentation

The MVP should include replay tests over scripted event streams:

- Normal peg-in success.
- Peg-in reorg before confirmation.
- Peg-in reorg after local submission but before Strata acceptance.
- Normal peg-out fulfillment.
- Peg-out cancellation before signing.
- Peg-out cancellation after broadcast.
- Strata event revert for a requested peg-out.
- Bitcoin conflicting spend for a broadcast peg-out.

The design docs should include state diagrams generated from the transition table or kept close enough to implementation that drift is obvious. person:AaronFeickert’s review should not have to reconstruct safety policy from code paths.

## Drawbacks

This design slows the bridge MVP by requiring typed Strata events and explicit cancellation handling before we complete the broader bridge feature set. That is deliberate, but it means devnet scope stays narrower than the original bridge ambition.

The state machine also becomes more conservative around ambiguous cases. Several situations route to `NeedsOperatorReview` instead of attempting automatic recovery. That is operationally annoying, and person:krsnapaudel and person:sapinb will need clear runbooks once this moves beyond local devnet. However, silent recovery in fund-moving code is worse than operational friction.

The event boundary may feel heavy for early protocol code. Still, the bridge is exactly the wrong place to depend on unstable internal structures. If the interface is painful now, it will be worse after more bridge logic accumulates.

## Alternatives considered

One alternative is to keep bridge logic tightly coupled to Strata internals until the protocol stabilizes. This is faster in the short term, but it makes the bridge implementation a moving target and weakens auditability. It also pushes protocol assumptions into bridge code without an explicit review point.

Another option is to ignore cancellation for the MVP and only support happy-path peg-outs. I do not think this is acceptable. Even if user-initiated cancellation is not exposed in devnet, protocol invalidation and expiry still need a state representation. Otherwise we will build signing and broadcast paths around the assumption that every request remains executable forever.

We could also treat Bitcoin reorgs as infra-level concerns and only feed finalized transactions to the state machine. That simplifies bridge state, but hides important behavior. The bridge still needs to know when a previously observed transaction is no longer valid, especially during testing and devnet operation. A normalized reorg event stream gives us better coverage.

Finally, we could use a generic event bus without bridge-specific schemas. That keeps Strata flexible, but shifts validation and interpretation into the bridge. For fund movement, I prefer boring, explicit, versioned events.

## Open questions

What Strata finality signal is strong enough for irreversible Bitcoin peg-out actions? For devnet we can use a weak threshold, but the type should anticipate stricter policy.

Should `BridgePegOutRequested` and `BridgePegOutEligible` be separate events in the first implementation, or a single event with eligibility metadata? I prefer separate events unless person:prajwolrg or person:MdTeach see a concrete protocol reason not to.

What is the maximum reorg depth we want to support in local state before requiring manual intervention? `100` is a proposed policy value, not a protocol claim.

How should fee bumping be represented in the peg-out lifecycle? The MVP can treat replacement transactions as part of `Broadcast`/`Confirming`, but production behavior will need explicit replacement tracking.

Who owns the canonical transition table: `repo:strata-bridge` implementation, `repo:bridge-sm-design-docs`, or generated docs from code? My preference is implementation-owned transitions with generated diagrams committed into the design docs.

What information must be included in cancellation reasons for audit review? person:AaronFeickert and person:uncomputable should weigh in before we freeze the schema.

Can `repo:bitcoin-bosd` provide enough script classification for peg-in detection in MVP, or do we need bridge-specific script recognition in `repo:strata-bridge` first?
