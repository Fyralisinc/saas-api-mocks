# RFC: Strategic Round Diligence Room Scope

## Summary

This RFC defines the scope, ownership, and review path for the strategic round technical diligence room covering `product:strata`, `product:strata-bridge`, `product:glock`, and `product:bitcoin-dollar` during the first two-week diligence package window, January 9-19, 2025.

The goal is not to produce investor-facing narrative first. The goal is to assemble a technically defensible canonical packet that can survive detailed review by sophisticated Bitcoin, cryptography, and infrastructure diligence teams. Some of the output can later become polished collateral, but the primary artifact should be accurate internal documentation with clear claims, explicit assumptions, and named unresolved risks.

I propose we structure the room around five canonical documents:

1. Strata protocol state and roadmap
2. Strata bridge architecture and threat model
3. Proving roadmap for Glock / zkaleido integration
4. Bitcoin-dollar technical positioning
5. Known risks, mitigations, and open research questions

person:pramodkandel will drive coordination and investor-readability edits. person:john-light should help convert canonical technical docs into external-friendly summaries only after protocol owners approve the underlying claims. Protocol substance should be owned by person:prajwolrg, person:MdTeach, person:delbonis, and person:AaronFeickert. Bridge substance should be owned by person:Rajil1213, with review from research and protocol as needed. Infrastructure-dependent answers should be routed through person:krsnapaudel, but we should avoid blocking release work on diligence unless a claim cannot be made responsibly without infra validation.

## Motivation

The current tension is that the investor narrative is ahead of protocol confidence. That is normal during a strategic round, but it is dangerous in our domain because diligence will not stop at market framing. We should assume reviewers will ask concrete questions about Bitcoin finality assumptions, bridge custody and withdrawal paths, proof system constraints, verifier costs, DA assumptions, operator powers, sequencer failure modes, and what is actually deployed versus merely designed.

If we answer these with one-off collateral, we create two problems.

First, we increase inconsistency. A bridge risk answer in a fundraising memo may drift from the bridge repo, the whitepaper, and what engineering actually intends to ship. That creates avoidable diligence risk and internal confusion.

Second, we consume senior reviewer bandwidth repeatedly. person:Rajil1213, person:prajwolrg, person:MdTeach, person:AaronFeickert, person:delbonis, and person:krsnapaudel are already committed to release work. We should not make them re-answer the same conceptual questions in Slack, decks, and call prep docs. A canonical diligence room lets us pay the review cost once, then reuse the approved language.

This is also a forcing function for stale docs. Sections of `repo:Technical-Whitepaper` are out of date relative to current Strata and bridge thinking. Diligence should not fork the truth. Where fundraising requires a concise answer, that answer should trace back to a maintained technical source or create one.

## Detailed Design

### Scope of the diligence room

The diligence room should include canonical markdown documents, diagrams where useful, and a claim register. I do not think we should start with a deck. Decks compress uncertainty too early.

The initial directory or workspace can be organized as:

- `00-index.md`
- `01-strata-protocol-state.md`
- `02-bridge-architecture-threat-model.md`
- `03-proving-roadmap.md`
- `04-bitcoin-dollar-positioning.md`
- `05-risks-open-questions.md`
- `claims-register.md`
- `review-log.md`

The `00-index.md` should define which docs are canonical, who owns each section, and the last reviewed date. The `claims-register.md` should list externally reusable claims, their source doc, reviewer, and status: `draft`, `approved`, `approved-with-caveat`, or `do-not-use`.

This may feel bureaucratic, but it is cheaper than discovering on an investor call that the bridge claim in the memo is stronger than what engineering believes.

### Strata protocol state

Owner: person:prajwolrg  
Reviewers: person:MdTeach, person:delbonis, person:AaronFeickert

This document should describe Strata as it exists now, not as a generalized rollup essay. It should cover:

- Execution model and state transition boundaries
- Relationship between L2 blocks, batches, and Bitcoin anchoring
- Current settlement and finality assumptions
- Operator / sequencer responsibilities
- Data availability assumptions
- Fault handling and degraded-mode behavior
- What is implemented in `repo:alpen` versus planned
- Known protocol limitations for the next release

The most important part is to distinguish hard protocol guarantees from operational expectations. If a property depends on honest operators, monitoring, parameter choice, or future proof integration, we should say that directly.

Suggested claim format:

> Strata currently provides X under assumptions A, B, and C. It does not yet provide Y until milestone M.

This document should also identify any whitepaper deltas. If `repo:Technical-Whitepaper` describes an older mechanism, we should record whether to update the whitepaper now or mark that section as superseded in the diligence room.

### Bridge architecture and threat model

Owner: person:Rajil1213  
Reviewers: person:AaronFeickert, person:MdTeach, person:prajwolrg

The bridge document is likely the highest-risk diligence artifact. We should assume investors will ask whether the bridge is a federation, multisig, BitVM-style construction, ZK-verified bridge, covenant-dependent design, or some staged combination. We need precise language.

This document should include:

- Deposit path
- Withdrawal path
- Watcher / operator roles
- Key material and signing assumptions
- Emergency controls, if any
- Liveness assumptions
- Challenge or dispute paths, if applicable
- Reorg handling
- Failure modes for halted sequencer, halted prover, unavailable operator, and malicious bridge participant
- User loss scenarios
- Current implementation status in `repo:strata-bridge`

The threat model should explicitly separate safety from liveness. For example, “users cannot withdraw promptly” and “users can lose funds” are different risk classes. We need to state which failures degrade UX, which require governance or operator intervention, and which are unacceptable.

If the bridge threat model is incomplete, the diligence room should say so in a controlled way. I would rather include a narrow, accurate bridge claim than a broad claim that person:Rajil1213 or person:AaronFeickert cannot defend.

### Proving roadmap: Glock and zkaleido

Owner: person:MdTeach  
Reviewers: person:prajwolrg, person:AaronFeickert, person:delbonis

The proving roadmap should explain how `product:glock` and `repo:zkaleido` fit into the Strata roadmap without overstating near-term production readiness.

Required sections:

- What is being proven
- Circuit / VM boundary
- Expected verifier target
- Proof aggregation plan, if any
- Current benchmark status
- Bottlenecks: proving time, memory, recursion, verifier cost, witness generation
- Dependency on protocol changes
- What must be true before proofs secure user funds or bridge withdrawals

This doc should be especially careful about “ZK rollup” phrasing. If a current milestone uses validity proofs for a subset of the system, or if proofs are not yet part of the live safety path, we should describe that accurately. The strongest credible claim is better than the most exciting claim.

### Bitcoin-dollar positioning

Owner: person:pramodkandel  
Reviewers: person:john-light, person:AaronFeickert, person:prajwolrg

This document should define what `product:bitcoin-dollar` means technically. It should not become a stablecoin marketing memo. It needs to answer:

- Is the Bitcoin-dollar issued on Strata?
- What collateral, redemption, or synthetic mechanism is assumed?
- What bridge dependency exists?
- What oracle dependency exists, if any?
- What are the Bitcoin security claims?
- What parts are protocol, application, liquidity, or partner-dependent?
- What is explicitly out of scope for the first release?

The positioning should be legible to investors, but the core doc should be reviewed as a technical artifact. person:john-light can help make the framing readable, but protocol and research reviewers should gate the claims.

### Review process

Each document should have exactly one owner. Reviewers can comment, but the owner is responsible for resolving feedback and marking claims as approved or caveated.

Proposed schedule:

- Jan 9-10: Create skeleton docs and assign owners
- Jan 11-14: Owners draft technical content
- Jan 15-16: Review by protocol, bridge, research, and infra owners
- Jan 17: Claim register freeze for first diligence package
- Jan 18-19: Convert approved claims into investor room artifacts

person:krsnapaudel should only be pulled into infra review for claims involving deployment topology, monitoring, uptime, key management, release process, or production readiness. We should batch these questions rather than interrupting infra repeatedly.

## Drawbacks

This process adds upfront structure during an already busy release window. It may feel slower than writing a memo or deck directly.

It also exposes uncertainty. A canonical risk document will make some gaps more visible, especially around bridge threat modeling and proving timelines. That may be uncomfortable for fundraising, but hiding uncertainty does not remove it. It just moves the failure point to diligence calls.

There is also a risk that the diligence room becomes stale after the strategic round. To avoid that, anything that represents durable protocol truth should either update the relevant repo docs or be clearly marked as diligence-only context.

## Alternatives Considered

One alternative is to produce only an investor deck and FAQ. This is faster, but it optimizes for presentation rather than correctness. It also forces senior engineers to review compressed claims without enough surrounding context.

Another alternative is to point investors directly at existing repos and the whitepaper. That avoids extra writing, but the current whitepaper has stale sections, and repos alone do not explain product-level assumptions. Diligence teams need curated entry points.

A third alternative is to separate internal technical docs from external fundraising artifacts entirely. That is cleaner in theory, but in practice it leads to drift. The better model is a canonical internal source with externally approved excerpts derived from it.

## Open Questions

1. Which current `repo:Technical-Whitepaper` sections are inaccurate enough that we should block external sharing until updated?

2. What is the minimum bridge threat model that person:Rajil1213 and person:AaronFeickert are comfortable approving for the first diligence package?

3. Can we make any production-readiness claims about `product:glock` in this window, or should all proving language remain roadmap-oriented?

4. Who has final authority to approve externally reusable protocol claims when research and engineering disagree on wording?

5. Should the claim register live in a repo, Notion-style workspace, or the diligence room itself?

6. What Bitcoin-dollar claims are technical commitments versus commercial positioning?

7. Do we need a separate reviewer for security-sensitive operational claims, or can person:krsnapaudel batch-review infra assumptions for this round?
