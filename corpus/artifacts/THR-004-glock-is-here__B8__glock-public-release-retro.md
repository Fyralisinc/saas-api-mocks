## Wins

We shipped the public Glock release in the 2025-11-13 to 2025-11-17 window, one week later than the first target, but with a better demo than we originally scoped. The extra week was not free, but it converted a release that would have been mostly claims-and-context into something people could actually inspect and run.

person:AaronFeickert drove the release thread through a fairly high-friction boundary: research claims, implementation state, benchmark evidence, and external-facing language all had to converge. person:mukeshdroid and person:Hakkush-07 helped keep the technical argument honest as the public framing tightened. The biggest win was that we narrowed the release claims instead of letting launch pressure outrun review. That was the right call.

The demo came out stronger than planned. The work across product:glock, repo:g16, repo:hash-benchmarks, repo:Technical-Whitepaper, and repo:alpen gave us a clearer public artifact than a standalone announcement would have. person:Zk2u and person:ceyhunsen helped surface the infrastructure and reproducibility assumptions that mattered for external readers, while person:john-light kept the public-readability path moving without turning the release into marketing copy.

We also had useful protocol-side pressure from person:storopoli. Even though protocol integration guidance ultimately moved to a follow-up thread, the questions raised during this release made the boundary clearer: Glock public release material should explain what is true now, what has been demonstrated, and what remains an integration decision for repo:alpen rather than pretending those are the same thing.

## Challenges

The main challenge was sequencing. Public claims had to be narrowed while launch planning was already underway. That created avoidable churn: review comments were not just polishing language, they were changing the shape of what we were comfortable saying externally. The release would have been easier if claim review had started before the announcement structure had hardened.

Benchmark reproducibility became release-critical too late. repo:hash-benchmarks was not just supporting evidence; it became part of whether the release felt credible. Once we realized that, we had to treat benchmark automation and rerun clarity as blockers. That compressed work that should have been part of the release definition from the start.

We also deferred protocol integration notes to keep the release focused. I still think that was the right release decision, but it left an unresolved ownership gap. The retro beat records that the follow-up integration owner was undecided, and that is the kind of loose end that tends to become background debt. The public release now creates demand for “how does this fit into Alpen?” and we need a crisp answer path rather than ad hoc responses.

The cross-repo nature of the release also made it harder to know what was actually done. product:glock touched implementation, benchmarks, whitepaper language, and repo:alpen integration context. We did not always have a single release checklist that distinguished must-ship, should-ship, and follow-up work.

## What we'd do differently

We should define the public claim envelope before drafting the release shape. For technical releases like this, the first gate should be: what are we claiming, what backs each claim, who has reviewed it, and what wording is explicitly out of bounds? That would have reduced late rewrites and made the launch-readiness conversation less subjective.

We should treat benchmark reproducibility as a first-class release artifact from day one. If benchmark numbers are part of the public story, then the scripts, environment notes, expected outputs, and rerun path are not supporting details. They are release criteria.

We should split “release now” and “integration next” more deliberately. Deferring protocol integration notes was correct for focus, but we should have assigned the follow-up owner before closing the release retro. For future releases, a deferred integration thread should have an owner, scope, and target date before the public release ships.

We should also keep the demo as a forcing function. The demo made the release better because it forced us to connect claims to observable behavior. Next time, we should start from the demo path earlier and let that expose missing repo, benchmark, and documentation work sooner.

## Action items

- person:AaronFeickert: write a lightweight claim-review checklist for future research/protocol public releases, including claim, evidence, reviewer, and excluded wording.
- person:mukeshdroid and person:Hakkush-07: document the minimum technical review bar for product:glock follow-up claims.
- person:Zk2u and person:ceyhunsen: turn repo:hash-benchmarks reproducibility requirements into a reusable release checklist.
- person:john-light: capture the external-facing release structure that worked here, with a clear place for “not covered in this release.”
- person:storopoli: propose the owner and scope for the protocol integration follow-up thread for repo:alpen.
- person:AaronFeickert: close the retro only after the integration follow-up has an assigned owner and target date.
