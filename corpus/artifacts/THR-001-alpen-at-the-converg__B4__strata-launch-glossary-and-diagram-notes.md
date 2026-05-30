# Strata Launch Glossary and Diagram Notes

## Goal

Define the first launch-facing glossary and diagram notes for `product:strata` so `person:pramodkandel`, `person:chhetri22`, and the protocol reviewers can write public material without re-litigating terminology every day.

The immediate output is not a polished external blog post. It is a shared internal source of truth for words and pictures: what we call each component, which claims are safe to make publicly, which repo or whitepaper artifacts support those claims, and which architectural details we intentionally leave out until they are stable.

For beat B4, I want this to unblock three parallel tracks:

1. `person:pramodkandel` can draft the first technical blog with fewer protocol round-trips.
2. `person:chhetri22` can review public wording against a concrete glossary instead of scattered Slack context.
3. `person:delbonis`, `person:bewakes`, `person:MdTeach`, and I can keep onboarding and implementation PRs moving without every docs question becoming a protocol design meeting.

## Non-goals

This doc does not define the final Strata protocol spec. The `repo:Technical-Whitepaper` remains the normative destination for protocol-level claims.

This doc does not commit us to permanent product names for every internal component. Some labels are intentionally launch-safe rather than academically complete.

This doc does not expose proving-system internals, bridge custody details, operator assumptions, or exact fraud/escape paths beyond what we are ready to defend publicly.

This doc does not replace diagrams in the whitepaper. It defines the launch diagrams that simplify the system enough for first-contact readers while staying technically honest.

## Background

The launch narrative is moving faster than the protocol docs. That is expected, but it creates risk: public wording can drift from the repo, and simple diagrams can accidentally imply properties we have not implemented or proven.

Over the last two weeks, `person:bewakes` and I have been turning loose protocol notes into diagrams, repo references, and a glossary that can survive review. The hard part is not drawing boxes. It is deciding what each box is allowed to mean.

The current public story needs to explain Strata as a Bitcoin settlement system using validity proofs, without implying that Bitcoin itself verifies arbitrary ZK proofs today. The reader should understand that Strata posts commitments and data to Bitcoin, uses ZK proofs to attest to state transitions, and relies on bridge and settlement machinery that must be described carefully. We should avoid saying “Bitcoin verifies Strata” unless the sentence is scoped to what Bitcoin actually enforces.

The core tension is terminology churn. We have used overlapping names for execution state, rollup state, checkpoint state, bridge state, proof inputs, and settlement outputs. In code and research notes, that is survivable. In public launch material, it creates contradictions fast.

## Proposed design

### Glossary source of truth

Create a launch glossary with three columns:

| Term | Public definition | Internal notes |
|---|---|---|
| Strata | A Bitcoin-settled validity rollup architecture for scaling Bitcoin-native applications. | Avoid “Bitcoin L2” as the only definition; it is useful shorthand but underspecified. |
| Batch | An ordered set of Strata transactions processed into a new rollup state. | Do not imply fixed batch size or cadence unless confirmed in `repo:alpen`. |
| State root | Commitment to the Strata state after executing a batch. | Public-safe if we avoid committing to exact tree shape. |
| Validity proof | A zero-knowledge proof that a state transition followed protocol rules. | Say “validity proof,” not just “ZK proof,” when the correctness claim matters. |
| Checkpoint | A Bitcoin-published commitment tying Strata state to Bitcoin settlement history. | Avoid implying Bitcoin directly verifies full transition validity. |
| Bridge | The mechanism for moving BTC economic exposure into and out of Strata. | Public wording needs review from `person:Rajil1213`, `person:ProofOfKeags`, and `person:uncomputable`. |
| Operator | Entity or process responsible for proposing batches/checkpoints. | Do not over-spec decentralization properties yet. |
| Prover | System that generates validity proofs for Strata state transitions. | Keep hardware/performance claims out until measured. |
| Verifier | Logic that checks validity proofs outside Bitcoin consensus. | Be explicit when verification is off-chain, client-side, or protocol-side. |
| Data availability | The ability for users/verifiers to obtain enough transaction or state data to independently follow the chain. | Avoid saying Bitcoin provides all DA unless we specify exactly what is posted. |

This glossary should live in the launch docs area, with links back to `repo:alpen` and `repo:Technical-Whitepaper` where possible. `person:delbonis` should review for consistency with the whitepaper, and `person:bewakes` should review against current implementation names.

### Diagram set

We should produce three launch diagrams, each with constrained scope.

**Diagram 1: Strata high-level loop**

Boxes:

- Users submit transactions to Strata.
- Strata nodes execute transactions and update state.
- Prover generates validity proof for state transition.
- Checkpoint commitment is published to Bitcoin.
- Verifiers track Bitcoin checkpoints and Strata data.

Allowed claim: Strata state transitions are proven and anchored to Bitcoin through checkpoint commitments.

Avoided claim: Bitcoin consensus validates every Strata transaction or proof.

**Diagram 2: Bitcoin anchoring path**

Boxes:

- Batch metadata
- State root
- Proof reference or proof artifact
- Bitcoin transaction carrying commitment
- Verifier observing Bitcoin chain

This diagram should distinguish “data posted to Bitcoin” from “data available elsewhere.” If the launch blog uses “settled on Bitcoin,” the diagram must show what is actually in the Bitcoin transaction: commitments, ordering anchors, and any protocol-specific metadata we are comfortable naming.

`person:MdTeach` should review this one because it is where most accidental overclaims happen.

**Diagram 3: Bridge concept boundary**

Boxes:

- Bitcoin locking side
- Strata representation side
- Withdrawal/exit path
- Proof/checkpoint dependency
- Watcher or verifier role

This is intentionally abstract. The goal is to show that the bridge is not a generic multisig wrapper in our narrative, while also not publishing premature mechanics. `person:Rajil1213`, `person:ProofOfKeags`, and `person:uncomputable` should own final bridge wording.

### Public wording rules

Use:

- “Bitcoin-settled”
- “validity-proven state transitions”
- “checkpointed to Bitcoin”
- “verifiers can follow Strata state from published commitments and available data”
- “designed to minimize new trust assumptions”

Avoid, unless specifically approved:

- “Bitcoin verifies Strata proofs”
- “trustless bridge”
- “fully decentralized sequencer”
- “instant finality”
- “inherits Bitcoin security” without explaining which part
- “ZK rollup on Bitcoin” as a standalone claim

For the first blog, `person:pramodkandel` can use shorthand in the headline or intro, but the first technical paragraph needs the more precise phrasing.

## Trade-offs

The glossary will simplify some terms more than protocol engineers prefer. That is acceptable for launch if each simplification is conservative. A public reader does not need the full state machine, but they must not walk away with a false model of what Bitcoin enforces.

The diagrams will hide some internal boundaries. For example, the prover pipeline, batch proposal flow, and verifier logic could each deserve their own technical diagram. Including them now would make the first blog slower and more brittle because those components are still moving in `repo:alpen`.

The biggest risk is that “Bitcoin-settled” becomes a vague phrase. We should use it, but pair it with concrete explanation: Strata publishes commitments to Bitcoin, uses Bitcoin ordering as a settlement anchor, and lets verifiers reason about Strata history relative to Bitcoin history. Where enforcement happens outside Bitcoin consensus, we say so.

Another trade-off is review load. Every launch edit cannot require all protocol engineers. The glossary gives us a smaller review surface: if wording matches approved terms, `person:chhetri22` can approve most edits; if wording touches bridge mechanics, settlement guarantees, or proof verification assumptions, it escalates to the relevant owners.

## Rollout plan

First, I will draft the glossary and three rough diagrams from current notes and repo references. `person:bewakes` will check implementation naming, especially around batches, state roots, and checkpoint data.

Second, `person:delbonis` and `person:MdTeach` will review the settlement and validity-proof wording against `repo:Technical-Whitepaper`. Any disputed term gets one launch-safe definition and one internal note explaining what we are postponing.

Third, `person:pramodkandel` will use the glossary to produce the first technical blog draft. The draft should link every major claim back to either the glossary, whitepaper, or a stable repo reference.

Fourth, `person:chhetri22` will review the public draft using a checklist:

- Does the article distinguish Bitcoin anchoring from Bitcoin proof verification?
- Does it avoid finality claims we cannot defend?
- Does bridge wording stay within approved language?
- Do diagrams match the text?
- Are all named components present in either `repo:alpen` or `repo:Technical-Whitepaper`?

Finally, after the first blog is approved, we freeze the launch glossary for the public launch window. Protocol docs can keep evolving, but launch copy should not rename core concepts unless `person:delbonis`, `person:pramodkandel`, and the relevant component owner agree that the old term is actively misleading.
