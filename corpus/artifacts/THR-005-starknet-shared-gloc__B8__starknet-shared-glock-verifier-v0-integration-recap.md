## Highlights

We closed the Starknet shared Glock verifier v0 milestone and moved it from a research-adapter thread into an integration-ready package. The important change is scope clarity: this is no longer a thin compatibility shim around existing verifier code. It is a shared package with frozen v0 fixtures, explicit interface expectations, and a path for Starknet-side integration without each downstream team reinterpreting the protocol boundary.

The collaboration started with a narrower estimate, but the final shape is better for the work ahead. The external integration pressure forced us to privilege stable inputs, stable outputs, and reproducible fixtures over local benchmark polish. That was the right trade. Benchmarks still matter, but for this milestone the biggest risk was interface drift between research notation, package implementation, and protocol integration assumptions.

The public note is intentionally conservative. It says what shipped and how collaborators can evaluate it, but it does not imply mainnet readiness. That distinction matters. We now have a shared verifier artifact that can be integrated and tested across repos, not a production deployment claim.

## What Shipped

The shared Glock verifier package is now integration-ready for v0 consumers. The shipped package includes frozen v0 fixtures, verifier entry points, and documentation sufficient for a downstream team to wire against the expected interface without depending on informal Slack context or research-side notation.

The fixture freeze is the main deliverable. It gives us a stable target for Starknet integration, regression checks, and external review. We can now distinguish between implementation bugs, fixture incompatibilities, and protocol-level changes instead of mixing them together during debugging.

The docs landed after the code freeze, which was not ideal, but the final result is acceptable for this milestone. The important part is that the docs now describe the v0 surface as shipped, rather than describing an aspirational interface. That should reduce back-and-forth with external collaborators and keep package consumers anchored to the same semantics.

We also shipped a short public recap. The recap avoids overclaiming and keeps the message focused on shared verifier availability, integration status, and next steps. It should be usable by devrel and external protocol collaborators without creating expectations that the verifier is already hardened for mainnet use.

Two follow-up tickets are now explicit: batching and benchmark cleanup. Batching was deliberately deferred from this milestone because pulling it in would have reopened interface questions too late in the cycle. Benchmark cleanup also remains open because benchmark presentation is less important than fixture stability at this stage. Both are now tracked instead of being buried as loose ends.

## Coming Up

The next priority is integration feedback. Once consumers wire against the package, we should expect some friction around naming, serialization assumptions, and how research concepts map into protocol code. The goal is not to avoid all churn, but to keep any churn deliberate and versioned.

Batching is the first substantive protocol-facing follow-up. We need to decide whether batching belongs as a direct extension of the v0 interface or as a separate layer with its own fixtures. That decision should be made before implementation work expands, because it affects verifier ergonomics and downstream test design.

Benchmark cleanup should follow once the interface is no longer moving. We need useful numbers, but benchmark polish before integration feedback would create false precision. The immediate ask is to clean up benchmark scripts, document what they measure, and separate performance notes from correctness fixtures.

We should also keep the public communication narrow. Until the verifier has gone through downstream integration and review, the message should remain: integration-ready shared package, frozen v0 fixtures, active follow-up work. No mainnet framing.

## Q&A Summary

One question was whether the package should have stayed as a thin adapter. The answer is no, given where the collaboration landed. A thin adapter would have been faster locally but weaker as a shared artifact. The package design gives us a common interface and test target, which is more valuable for Starknet collaboration.

There was also discussion about why batching missed the milestone. The short version is that batching would have changed the surface area too late. Deferring it lets us ship a stable v0 and handle batching as an explicit extension rather than a hidden late-stage requirement.

On docs timing, the concern was valid: merging docs after code freeze increases review pressure. For v0, the final docs match the shipped code closely enough. For the next cycle, docs should track interface decisions earlier so research and protocol expectations converge before freeze.

On readiness language, we agreed to keep the public recap precise. Integration-ready does not mean production-ready or mainnet-ready. That wording protects both us and collaborators from treating this milestone as more mature than it is.

## Shoutouts

person:AaronFeickert drove the collaboration through the scope change and kept the research side grounded in what the integration actually needed.

person:cyphersnake and person:ceyhunsen carried the package work across the final freeze and helped turn the verifier into something downstream teams can consume directly.

person:prajwolrg helped keep the protocol integration expectations concrete, especially around fixtures and interface stability.

person:Hakkush-07 and person:Zk2u contributed research and infra context that made the shared package less ambiguous.

person:storopoli helped pressure-test the protocol-side assumptions and kept the implementation path tied back to Strata needs.

person:john-light handled the public recap carefully, with the right constraint: useful externally, but no mainnet-readiness claims.
