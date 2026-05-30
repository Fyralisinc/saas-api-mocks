# RFC: Public Claims Boundary for Strata Launch Materials

## Summary

This RFC defines the public claims boundary for the first Strata launch materials: the public blog post, a deeper technical appendix, and a private reviewer note. The goal is to let person:pramodkandel move launch narrative forward without forcing the protocol team to prematurely freeze every architectural detail or expose internals that are still moving in `repo:alpen` and `repo:Technical-Whitepaper`.

The proposed boundary is:

1. The public blog post may describe Strata’s purpose, threat model direction, high-level architecture, and why Bitcoin plus ZK matters.
2. The technical appendix may describe protocol components at a system level, including proving, settlement, bridge flow, sequencing assumptions, and expected verification path, but must avoid exact parameter commitments unless already reviewed.
3. The private reviewer note should contain every claim that depends on unresolved implementation details, security arguments, benchmarks, proof-system assumptions, bridge edge cases, or whitepaper language still under review.

This is not a writing-style RFC. It is a protocol-claims RFC. The key output should be a reusable decision rule for whether a sentence belongs in public launch copy, technical appendix, or private review notes.

## Motivation

The current launch narrative is blocked by a mismatch in clocks. Public communications need a coherent first version now. Protocol documentation is still changing. The implementation in `repo:alpen` is ahead of some written explanations, while `repo:Technical-Whitepaper` is still the source of truth for claims that require careful review. We should not make person:pramodkandel wait on every low-level protocol edit, but we also should not publish claims that person:delbonis, person:bewakes, person:prajwolrg, or person:MdTeach would later need to qualify or unwind.

The immediate tension is that the single launch post has become too dense. It is trying to do three jobs:

1. Explain why Strata exists.
2. Teach enough architecture for technical credibility.
3. Prove that every security claim is already nailed down.

That structure creates bad incentives. Devrel wants clarity and momentum. Protocol reviewers want precision. The result is a draft that is simultaneously too vague for engineers and too detailed for a first public post.

Splitting the material lets each artifact carry the right kind of claim. The blog post can be accurate without being exhaustive. The appendix can be more technical without pretending to be the whitepaper. The private reviewer note can preserve doubts and unresolved claims without turning the public artifact into a wall of caveats.

This also gives person:chhetri22 a clear authorship lane: draft the boundary, identify risky statements, and make review cheaper for protocol engineers.

## Detailed Design

### Artifact split

We should maintain three documents for the launch beat.

The public blog post is the primary launch artifact. It should be readable by Bitcoin engineers, ZK developers, ecosystem partners, and technically literate users. It should not require the reader to understand every proving or bridge detail. It may explain Strata as a Bitcoin-aligned ZK protocol effort and describe the product-level direction, but it must avoid implying production readiness where we only have design intent or active implementation.

The technical appendix is the public technical companion. It should be linked from the blog post or published shortly after, depending on review state. It can include diagrams, component descriptions, transaction lifecycle sketches, and more explicit discussion of assumptions. It still should not contain claims that require final cryptographic analysis, final implementation behavior, or benchmark-backed performance numbers unless those claims have an assigned reviewer.

The private reviewer note is internal. It should list claims that are useful for reviewers but not yet appropriate for publication. This includes exact phrasing candidates, evidence needed, unresolved objections, and source pointers into `repo:alpen`, `repo:Technical-Whitepaper`, and relevant issues or PRs. This is where we should park statements like “the bridge is trust-minimized under assumption X” until person:delbonis or person:bewakes has approved both the claim and the caveat.

### Claim categories

Every launch claim should be classified into one of five categories.

**Category A: Safe public framing**

These are high-level statements about intent and direction. They can appear in the blog post.

Examples:

- Strata is an effort to bring ZK-based execution closer to Bitcoin settlement.
- The design aims to preserve Bitcoin as the settlement anchor rather than replacing it with a separate trust domain.
- ZK proofs are used to make execution claims verifiable without requiring every verifier to re-execute the full state transition.

These claims are acceptable because they describe design goals without promising final security properties.

**Category B: Public technical description**

These can appear in the technical appendix and, in short form, the blog post. They describe components but avoid finality claims.

Examples:

- Strata separates execution, proving, data publication, and Bitcoin-facing settlement logic.
- A prover produces succinct evidence about a state transition, and verifier logic checks that evidence against committed state.
- Bridge flows require careful treatment of Bitcoin finality, withdrawal latency, and challenge or verification windows.

These should be reviewed by person:prajwolrg or person:MdTeach if they touch core protocol flow. If they touch bridge language, person:delbonis or person:bewakes should review.

**Category C: Conditional public claims**

These can be public only with explicit caveats. They should usually live in the appendix.

Examples:

- “The design reduces trust in off-chain operators” is acceptable only if paired with what trust remains.
- “Bitcoin verifies the outcome” must specify whether verification is direct on Bitcoin, mediated through script constraints, delayed by protocol mechanics, or dependent on future upgrades.
- “Users can withdraw to Bitcoin” must clarify the withdrawal path, latency, and assumptions.

This category is where most accidental overclaims happen. We should prefer precise but slightly longer wording over clean but misleading wording.

**Category D: Private reviewer claims**

These should not appear publicly until reviewed.

Examples:

- Any exact security theorem.
- Any claim that a bridge path is trustless, permissionless, censorship-resistant, or non-custodial without a complete assumption list.
- Any performance number, proving cost, latency estimate, throughput estimate, fee estimate, or verifier cost.
- Any statement that compares Strata favorably against named projects unless the comparison is sourced and reviewed.
- Any claim that depends on a not-yet-merged PR in `repo:alpen`.

These belong in the private reviewer note with a proposed public replacement.

**Category E: Do not publish**

These should be removed entirely from launch materials.

Examples:

- Claims that imply Strata inherits Bitcoin security without qualification.
- Claims that imply the bridge has no trust assumptions.
- Claims that present the whitepaper as final if it is still changing.
- Claims that turn design goals into delivered implementation.
- Claims that disclose internal debate, unresolved attack scenarios, or incomplete mitigation details.

The point is not secrecy for its own sake. The point is that partially explained security concerns are easy to misread and hard to correct after launch.

### Review routing

We should assign review by claim surface, not by document.

person:pramodkandel should own narrative coherence and publication sequencing. person:chhetri22 should own the claims matrix for this beat and keep a table of risky statements, current placement, reviewer, and status.

Protocol architecture claims should route to person:prajwolrg, person:MdTeach, and person:delbonis. Bridge claims should route to person:bewakes and person:delbonis. If a claim depends on whitepaper wording, the reviewer should compare against `repo:Technical-Whitepaper`, not memory. If a claim depends on implementation behavior, the reviewer should point to `repo:alpen`.

The `.github` repo should eventually hold the public contribution and disclosure posture, but for this beat it should not block the first launch narrative unless the blog references public process or security reporting.

### Recommended public wording pattern

For the blog post, use design-intent language:

> Strata is designed to make Bitcoin the settlement anchor for ZK-verified execution.

Avoid completed-security language:

> Strata inherits Bitcoin’s security.

For the appendix, use scoped mechanism language:

> In the current design, state transition validity is established by proofs checked against committed state. Bitcoin-facing settlement logic constrains how those commitments are accepted and later used for deposits and withdrawals.

Avoid broad mechanism collapse:

> Bitcoin verifies all Strata execution.

For bridge language, use explicit assumptions:

> The bridge design aims to minimize operator trust, but withdrawal safety depends on the final bridge protocol, Bitcoin confirmation depth, verification path, and liveness assumptions.

Avoid absolute bridge language:

> The bridge is trustless.

### Claims matrix

person:chhetri22 should maintain a simple table during this beat:

| Claim | Current artifact | Category | Reviewer | Status | Source |
|---|---|---:|---|---|---|
| Strata uses ZK proofs for execution validity | Blog | B | person:prajwolrg | Pending | whitepaper section |
| Bitcoin is the settlement anchor | Blog | C | person:delbonis | Needs caveat | whitepaper + repo |
| Bridge is trust-minimized | Appendix | D | person:bewakes | Private only | reviewer note |
| Expected proof latency | Reviewer note | D | person:MdTeach | Needs evidence | implementation |

The exact table format does not matter. What matters is that reviewers can see which sentences need protocol approval and which are only narrative.

## Drawbacks

This split creates more documents and more coordination overhead. person:pramodkandel will need to keep the blog, appendix, and reviewer note aligned. That is real cost.

The public blog may also feel less technically satisfying than the original dense draft. Some readers will want the whole architecture immediately. We can address that with the appendix, but the first post should still avoid becoming a whitepaper substitute.

There is also a risk that the private reviewer note becomes a graveyard for unresolved claims. To prevent that, every private-only claim should have either a proposed public-safe rewrite or a named owner for evidence.

## Alternatives Considered

One alternative is to publish a single comprehensive technical launch post. I do not recommend this. It increases review pressure, makes the post harder to read, and creates a strong chance that public wording lags behind repo and whitepaper edits.

Another alternative is to publish only a high-level narrative and defer all technical material. That is safer but too weak for this audience. Strata needs technical credibility at launch, and a purely narrative post will create avoidable questions.

A third alternative is to make the whitepaper the only technical source and keep the blog short. This is clean in theory, but the whitepaper is not currently stable enough to carry all public interpretation. The appendix gives us a controlled middle layer.

## Open Questions

1. Who has final signoff authority when narrative clarity and protocol precision conflict: person:pramodkandel, protocol reviewers, or a designated launch owner?
2. Should the technical appendix publish at the same time as the blog, or only after the private reviewer note is fully cleared?
3. What is the minimum acceptable caveat for Bitcoin settlement claims?
4. Which bridge claims require person:bewakes and person:delbonis both to approve?
5. Should performance and latency claims be banned from the first launch entirely unless backed by reproducible benchmarks in `repo:alpen`?
6. Do we want a standing claims matrix process for future public materials, or is this only for the Strata launch beat?
