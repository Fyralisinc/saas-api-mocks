# RFC: Public Claims Boundary for Mosaic and Glock

## Summary

This RFC defines the boundary for public technical claims we should make in the Mosaic announcement materials, especially where Mosaic data availability intersects with Glock verification and the longer Strata roadmap.

The short version: we can publicly say that Mosaic provides a data availability layer designed for Bitcoin-adjacent ZK protocol systems, and that Glock provides verification machinery intended to check claims over Mosaic-published data. We should not say that Mosaic alone gives finality, that Glock makes unavailable data available, or that the current demo represents the full Strata integration path.

For this launch window, our public posture should be:

- Mosaic is an experimental data availability construction for publishing and retrieving protocol data.
- Glock is a verification component used to check commitments, proofs, or derived statements about that data.
- The combination improves auditability and verifiability of off-chain protocol state, but it does not remove availability assumptions.
- Failure modes should be named explicitly: withheld data, equivocation around commitments, insufficient peer replication, indexing failures, and delayed proof generation.
- Strata integration should be described as downstream roadmap work, not as part of the launch claim.

This document is mainly for person:john-light, person:pramodkandel, research, and protocol reviewers aligning the announcement, diagrams, whitepaper language, and demo script. I am writing it from the research side because the current narrative risks compressing three distinct properties, availability, verification, and settlement, into one public concept.

## Motivation

The Mosaic announcement needs to move before all implementation details are merged across repo:mosaic, repo:mosaic-torrent, repo:g16, repo:Technical-Whitepaper, and repo:alpen-dashboards. That is acceptable if our claims are deliberately scoped. It is risky if we let the public version imply stronger guarantees than the implementation or research model currently supports.

The main tension in this beat is terminology drift. In research notes, we use terms like “availability,” “retrievability,” “commitment opening,” “verification,” “settlement,” and “finality” with fairly narrow meanings. In public writing, these collapse easily into “Mosaic makes data available and Glock proves it.” That sentence is too vague and likely too strong.

person:AaronFeickert has already pushed back on finality language, correctly. Finality is not the property Mosaic or Glock gives by themselves. Bitcoin finality is a property of Bitcoin confirmation depth and reorg assumptions. Protocol finality for Strata depends on bridge logic, state transition validity, dispute or proving rules, and user withdrawal assumptions. Mosaic can support the data publication side of that system, but we should not describe it as finalizing rollup state.

The second motivation is diagram correctness. The current narrative diagrams are missing adversarial cases. If we only show the happy path, data published to Mosaic, proof checked by Glock, state accepted by Strata, the visual story suggests a complete pipeline. For this announcement, we need diagrams that show at least one failure branch: data commitment exists but chunks are unavailable; proof verifies but the underlying data retrieval path is degraded; indexer sees a commitment but cannot reconstruct the payload.

The third motivation is demo reproducibility. person:krsnapaudel and infra support are likely needed late for dashboards, seed nodes, or repeatable torrent behavior. We should not make the demo carry claims it cannot reproduce deterministically. If the demo shows a working path, the announcement text should frame it as a demonstration of the current construction, not a guarantee of production liveness.

## Detailed design

### Claim categories

We should divide public claims into three categories: allowed, conditional, and disallowed.

Allowed claims are statements we can make without footnotes or special qualification:

- Mosaic publishes protocol data against cryptographic commitments.
- Mosaic is designed to make protocol data retrievable by independent participants.
- Glock verifies statements about committed data.
- Mosaic and Glock are being developed as components in Alpen’s Bitcoin/ZK protocol stack.
- The current work informs future Strata integration, but is not itself the complete Strata integration.

Conditional claims are acceptable only with explicit assumptions:

- “Available” may be used only if we state the relevant replication and retrieval assumption.
- “Verified” may be used only for the specific statement Glock checks.
- “Permissionless verification” may be used only if the verifier has access to the required data, commitments, and proof artifacts.
- “Bitcoin-aligned” may be used for design intent, not as a claim that Bitcoin consensus verifies Mosaic data.
- “Production path” may be used only to describe direction, not launch readiness.

Disallowed claims for this announcement:

- Mosaic finalizes state.
- Glock proves data availability in the absolute sense.
- Mosaic inherits Bitcoin’s security directly.
- Strata already uses Mosaic and Glock in the launch configuration, unless person:delbonis and protocol confirm the integration path has actually landed.
- Users can rely on Mosaic for withdrawals or bridge safety in the present release.
- The demo demonstrates all adversarial cases.

### Terminology rules

We should use “data availability” carefully. In rollup terminology, DA often means a system property under explicit honest participant, sampling, or erasure coding assumptions. If Mosaic currently provides publication and retrieval with peer replication, then “availability layer” is acceptable as product-category language, but technical paragraphs should say “retrievability under replication assumptions” or “data publication and retrieval.”

We should avoid “finality” unless the sentence says what finalizes and where. Good wording:

“Mosaic does not provide Bitcoin finality. It publishes data used by higher-level protocols, which may separately define finalization rules.”

Bad wording:

“Mosaic gives finality to off-chain state.”

For Glock, we should say “verifies statements” rather than “verifies Mosaic.” Glock does not verify a product; it verifies specific claims over commitments, encodings, or proof inputs. person:Hakkush-07 and person:cyphersnake should sanity-check the exact statement vocabulary against repo:g16.

For Strata, we should use “roadmap,” “target integration,” or “future integration work.” We should not use “powers Strata” unless person:delbonis, person:prajwolrg, or person:storopoli explicitly confirm the relevant path has merged and is enabled.

### Public narrative shape

The announcement should have this technical arc:

1. Bitcoin settlement is strong but intentionally bandwidth-constrained.
2. ZK protocol systems need a disciplined way to publish, retrieve, and verify off-chain data.
3. Mosaic addresses the publication and retrieval side.
4. Glock addresses the verification side for specific claims over committed data.
5. Together, they are building blocks for Alpen’s broader Bitcoin/ZK protocol stack, including future Strata work.
6. The current release is an implementation milestone and public technical preview, not a full system security claim.

This keeps the story strong without overstating. It also lets person:john-light and person:pramodkandel write clearly for external readers without flattening the model.

### Diagram requirements

Every public technical diagram should label the trust or failure assumption it relies on. Minimum required diagrams:

- Happy path: producer commits data, publishes chunks through Mosaic, verifier obtains required data, Glock verifies the statement.
- Unavailable data path: commitment exists, but verifier cannot retrieve enough data; verification cannot substitute for missing data.
- Future integration boundary: Mosaic and Glock feed into a Strata-shaped box marked “roadmap / integration target,” not “live dependency.”

The second diagram is important. It makes the core boundary visually obvious: Glock can reject or fail to verify when inputs are unavailable, but it does not force the network to serve missing chunks.

person:AaronFeickert should review the adversarial labels. person:mukeshdroid will keep the research terminology aligned. person:john-light can simplify the language after the boundary is approved, but the failure branches should remain visible.

### Review process

Before publication, I propose the following review sequence:

1. Research review by person:AaronFeickert, person:Hakkush-07, and person:cyphersnake for correctness of terminology and assumptions.
2. Protocol review by person:delbonis and one of person:prajwolrg or person:storopoli for Strata boundary language.
3. Infra review by person:krsnapaudel for demo reproducibility, dashboard wording, and any claims tied to live availability.
4. Devrel review by person:john-light and person:pramodkandel for readability after the technical boundaries are locked.

The key ordering point is that devrel editing should happen after the claim boundary is fixed, not before. Otherwise we will keep rediscovering the same finality and availability issues in softer language.

## Drawbacks

The main drawback is that narrower language may feel less launch-ready. “Mosaic provides retrievability under replication assumptions” is less clean than “Mosaic makes data available.” But the cleaner version creates a correctness debt we will have to repay when external researchers ask what exact DA property we mean.

Another drawback is that adversarial diagrams can make the announcement feel less polished. I think this is acceptable. In Bitcoin/ZK protocol work, showing the failure model is part of credibility. A diagram with a missing-data branch is not a weakness if the text explains that the system is designed around explicit assumptions rather than hidden ones.

A third drawback is internal coordination cost. This RFC asks for review from research, protocol, infra, and devrel during a tight launch window. However, the cost of one review pass is lower than the cost of editing public claims after publication.

## Alternatives considered

One alternative is to keep the public announcement high-level and avoid detailed claims. I do not recommend this. Mosaic and Glock are technical products. If we stay vague, readers will infer stronger claims from familiar rollup vocabulary, especially around data availability and finality.

Another alternative is to publish a strong narrative now and put precise assumptions in a later technical note. This is risky because the announcement will be the canonical first impression. Later corrections rarely travel as far as launch language.

A third alternative is to block the announcement until implementation, diagrams, dashboards, and Strata integration language are all complete. That would reduce ambiguity but probably misses the current communication window. The better compromise is a bounded announcement: clear milestone, clear assumptions, clear roadmap boundary.

A fourth alternative is to separate Mosaic and Glock entirely in public messaging. That would avoid some confusion, but it would also hide the important design relationship. The right framing is not separation; it is composition with explicit limits.

## Open questions

What exact Glock statement should we name publicly for this launch? We need a short formulation that person:Hakkush-07, person:cyphersnake, and person:AaronFeickert are comfortable defending.

Can we use “data availability layer” in the headline while using narrower terminology in the body? My current view is yes, but only if the first technical paragraph defines the assumptions.

What demo failure case can we reproduce reliably? If person:krsnapaudel can support a deterministic unavailable-chunk or degraded-peer scenario, we should include it. If not, the public demo should stay on the happy path and the article should carry the failure explanation.

How close should we place Strata in the announcement? I recommend one roadmap paragraph and one boundary diagram, not a full integration narrative.

Who owns final approval on public claims? My proposal: person:mukeshdroid owns research wording, person:AaronFeickert has blocking review on finality and assumptions, person:delbonis has blocking review on Strata scope, and person:john-light owns final publication text once those constraints are satisfied.
