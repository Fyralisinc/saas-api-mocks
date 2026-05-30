## Goal

Define the message architecture for the first public launch of Strata: what we say, what we deliberately do not say yet, and how the first technical blog maps onto the repo and whitepaper without over-committing to implementation details that are still moving.

The immediate goal is to give person:pramodkandel, person:chhetri22, and reviewers a stable structure for public wording during B2, while person:delbonis and the protocol team continue tightening architecture in `repo:alpen` and `repo:Technical-Whitepaper`. This should unblock the launch narrative faster than the full protocol docs, but still keep the public claims technically defensible.

The core public claim should be:

Strata is a Bitcoin-native ZK rollup system that uses Bitcoin as the settlement and asset layer, while moving execution and proof verification logic off-chain and into a protocol stack designed around validity proofs, bridge safety, and minimized trust assumptions.

## Non-goals

This document is not the protocol spec. It should not define final bridge semantics, verifier placement, DA guarantees, prover economics, or operator decentralization timelines.

It is also not a marketing positioning doc. We should avoid broad claims like “scaling Bitcoin” unless immediately qualified with the actual mechanism. We should not imply that Strata inherits all Bitcoin security properties for all state transitions. We should distinguish between BTC custody/security, rollup state validity, data availability, and liveness assumptions.

We should not publish final diagrams of architecture paths that person:prajwolrg, person:MdTeach, person:bewakes, or person:delbonis may still change. We can describe the conceptual layers, but not freeze module boundaries unless already reflected in `repo:alpen` and expected to survive.

We should not make claims about mainnet launch timing, trustless withdrawal completeness, BitVM-style verification, opcode dependencies, or covenant assumptions unless they are already committed in reviewed technical material.

## Background

The public narrative is lagging the engineering work. The repo and whitepaper are changing faster than the prose, and early review has shown disagreement about how much architecture should be exposed in the first blog. That disagreement is valid. If we expose too little, the launch reads like generic Bitcoin L2 language. If we expose too much, we create stale public commitments and force the protocol team to carry old wording after the design changes.

During B2, person:chhetri22 joined review while still onboarding. That is useful because the launch blog needs to survive a cold technical read. If a new engineer cannot reconcile the text with the repo architecture after a short review pass, external readers will not do better. person:delbonis should be the primary reviewer for protocol correctness, with person:prajwolrg and person:MdTeach pulled in only for specific architecture claims that touch their current work.

The first blog should establish a vocabulary we can keep using:

- Bitcoin settlement layer: where BTC lives and where final externally visible commitments anchor.
- Strata rollup state: the off-chain state machine whose transitions are proven.
- Validity proof system: the mechanism used to attest correct execution.
- Bridge: the path by which BTC or BTC-representing claims move between Bitcoin and Strata.
- Operators/provers: the parties that produce blocks, proofs, and publication artifacts.
- Watchers/challengers, if applicable: parties that protect liveness or detect invalid publication behavior.

The important framing is that Strata is not “an EVM chain with Bitcoin branding.” It is a protocol stack built around Bitcoin constraints: limited scripting, expensive L1 blockspace, slow finality, no native general-purpose verifier, and a strong preference for designs where users can independently verify state claims.

## Proposed Design

The launch narrative should be organized as a layered message tree.

First layer: problem statement. Bitcoin has the strongest monetary asset and settlement network, but Bitcoin L1 is intentionally constrained. Those constraints are good for money, but they make high-throughput applications impractical directly on L1. Strata’s thesis is that we can preserve Bitcoin as the asset and settlement base while moving execution into a validity-proven environment.

Second layer: mechanism. Strata batches off-chain execution, commits state updates, and uses ZK proofs to make state transition validity externally checkable. The blog should be explicit that “ZK” here means succinct validity proofs for execution correctness, not privacy by default. We should avoid privacy connotations unless the specific component provides privacy.

Third layer: Bitcoin relationship. Strata should be described as Bitcoin-native because its bridge and settlement design are built around BTC and Bitcoin constraints, not because every rollup transition is verified directly by Bitcoin script today. The wording should say that Bitcoin remains the base asset and settlement reference, while the rollup protocol supplies additional validity and bridge logic around it.

Fourth layer: trust model. We should include a short “what users rely on” section in the first technical blog. This is the most important credibility device. A draft structure:

Users rely on Bitcoin for L1 transaction ordering and finality. They rely on Strata validity proofs for rollup state transition correctness. They rely on bridge protocol assumptions for deposit and withdrawal safety. They rely on data publication and operator liveness assumptions to exit or continue using the system.

That paragraph should be reviewed by person:delbonis and person:bewakes before publication. If person:uncomputable or person:ProofOfKeags are available, bridge-specific wording should go through them too.

Fifth layer: what we are building now. The blog should point readers to the early architecture without pretending it is complete. It can mention `repo:alpen` as the implementation home and `repo:Technical-Whitepaper` as the evolving technical reference. It should not over-index on line-by-line repo internals. Public readers need enough specificity to see this is real engineering, not enough to treat an early module layout as a stable API.

The first technical blog should have this outline:

1. Why Bitcoin needs validity-proven execution layers
2. What Strata is
3. How Strata separates settlement, execution, proving, and bridging
4. What the ZK proof system proves
5. What the bridge must guarantee
6. What assumptions remain
7. Where to follow the technical work

We should maintain a launch claims table in `.github` or the launch working doc. Each public claim gets an owner and source:

- Claim: Strata uses validity proofs for rollup state transitions. Owner: person:delbonis. Source: whitepaper section or repo module.
- Claim: Strata is Bitcoin-native. Owner: person:pramodkandel with technical review by person:chhetri22. Source: bridge and settlement design notes.
- Claim: ZK does not imply privacy by default. Owner: person:MdTeach or person:prajwolrg. Source: proof system notes.
- Claim: Bridge design minimizes trust assumptions. Owner: person:ProofOfKeags or person:uncomputable. Source: bridge design doc when available.

No claim ships without an owner and a source pointer.

## Trade-offs

The main trade-off is speed versus precision. Waiting for the full whitepaper would make the launch cleaner technically, but it would delay public narrative until after the window where it is needed. Publishing now means we need language that is accurate at the level of architecture, not final at the level of implementation.

The second trade-off is openness versus design churn. Exposing conceptual layers is useful: it lets technical readers understand Strata’s shape and gives devrel a stable vocabulary. Exposing internal component names too early is brittle. If person:delbonis or person:bewakes renames or splits modules, old public language becomes a liability.

The third trade-off is ambition versus trust. It is tempting to describe the end-state trust model. We should instead describe the path and separate current implementation, intended design, and research direction. Bitcoin/ZK readers will forgive incomplete systems faster than they forgive collapsed assumptions.

The fourth trade-off is accessibility versus technical density. The first blog should be readable by Bitcoin engineers who are not ZK specialists, but it should not simplify away the hard parts. We should name settlement, validity, DA, bridge safety, and liveness explicitly.

## Rollout Plan

person:pramodkandel owns the narrative draft and keeps the claims table current.

person:chhetri22 performs the first cold technical read. The review question is not “is this polished?” but “can a new engineer map each public claim to the current architecture without guessing?”

person:delbonis performs the protocol correctness review for settlement, execution, and proving language. Any unresolved disagreement blocks publication of that paragraph, not necessarily the whole blog.

person:prajwolrg, person:MdTeach, and person:bewakes are optional targeted reviewers for proof-system and state-transition claims. We should avoid broad review blasts unless a claim touches their area.

Before publishing, the draft should pass three checks:

1. Every public technical claim has an owner and a source.
2. No paragraph implies Bitcoin directly verifies more than the current design supports.
3. The trust model separates Bitcoin finality, rollup validity, bridge safety, DA, and liveness.

After publication, person:pramodkandel should maintain a short follow-up queue: bridge deep dive, proving architecture, and Strata state model. The first blog should create the map; later posts can fill in the territory once the protocol docs catch up.
