## Highlights

Mosaic is now in announcement-ready shape for the July 23-31 launch beat. The main work this week was not inventing new claims; it was tightening the public package so that the story, terminology, links, diagrams, and demo path all point at the same technical reality.

person:john-light and I closed the blog draft after the final title change and one more terminology pass with research. The current framing keeps Mosaic positioned as a concrete proof-carrying data availability and verification milestone without implying that the full Strata integration path is already landed. That distinction matters: the public narrative needs to move ahead of the implementation, but not past what we can defend.

The biggest coordination issue was review bandwidth. We got the critical eyes we needed from person:mukeshdroid and person:AaronFeickert, but availability was uneven enough that we should not pretend this process scales cleanly yet. The launch checklist also stayed blocked longer than expected on dashboard screenshots and demo reproducibility. person:krsnapaudel helped close that gap from infra, and we now have a credible capture path instead of hand-wavy “we’ll screenshot it later” launch debt.

Net: Mosaic is ready to announce, with explicit remaining risks around implementation freshness, repo hygiene, and follow-on Strata expectations.

## What Shipped

The announcement package is now assembled:

- Blog draft with final title, technical framing, and launch-level claims.
- Diagram captions that explain the Mosaic flow without overloading readers with research-only vocabulary.
- Repo links for `mosaic`, `mosaic-torrent`, `g16`, and the relevant whitepaper context.
- Coordinated social snippets for launch-day distribution.
- Dashboard screenshot plan tied to `alpen-dashboards`, with infra support identified.
- Internal notes separating what is launch scope from what belongs to the Strata roadmap.

The important editorial change was the terminology pass. We replaced several phrases that were precise inside research discussions but likely to mislead externally. In particular, we avoided wording that could make Mosaic sound like a production Strata subsystem today. The document now treats Mosaic as a protocol artifact and announcement milestone with a clear path toward integration, not as a completed integration.

person:mukeshdroid helped keep the research language technically honest. person:AaronFeickert pushed on whether the security phrasing was too broad in a few places. person:cyphersnake and person:delbonis were looped where implementation details touched repo state and proof-system language. person:krsnapaudel handled the dashboard dependency and helped make the demo materials reproducible enough for launch collateral.

## Coming Up

Before publication, we still need a final checklist pass on the following:

- Confirm all public repo links resolve to the intended branches, READMEs, and examples.
- Re-run the dashboard screenshot flow once the final demo data is pinned.
- Verify that the blog links do not imply dependency ordering between Mosaic, Glock, and Strata that we cannot stand behind.
- Make sure the social snippets preserve the same scope boundaries as the blog.
- Decide whether the Technical Whitepaper link should point at the current canonical document or a narrower section reference.

After launch, the follow-up work should be more engineering-driven than communications-driven. The announcement will create questions about how Mosaic connects to Strata, where Glock sits in the proof pipeline, and which parts are already merged versus still being integrated. We should prepare a short internal FAQ so person:john-light, I, and the devrel side do not need to improvise answers in public threads.

There is also a process point: announcement readiness currently depends too much on late-stage manual review. For the next launch package, we should create a smaller standing review group earlier in the beat, with one protocol owner, one research owner, one infra owner, and one devrel owner.

## Q&A Summary

Q: Are we overstating Mosaic’s relationship to Strata?

A: The latest draft avoids saying Mosaic is already part of Strata’s live path. It says Mosaic is relevant to the Strata roadmap and explains why, but it keeps integration work outside the launch scope.

Q: Is the research terminology too diluted for correctness?

A: No. The current version translates terms for external readers, but the underlying claims are still bounded. person:mukeshdroid and person:AaronFeickert both pushed on places where simplification risked changing meaning.

Q: Can the demo be reproduced if someone asks?

A: We are closer now, but this remains the main operational risk. person:krsnapaudel has the dashboard screenshot path under control. We still need one final capture against pinned data before publishing.

Q: Do the repos match the announcement claims?

A: Mostly, but this needs the final link audit. The blog should point readers to concrete code and docs without implying every downstream integration is merged.

Q: What happens if reviewer availability slips again before launch?

A: We should freeze the scope rather than reopen claims. Any late review should remove ambiguity, not add new narrative.

## Shoutouts

person:john-light for driving the announcement package through title churn, review gaps, and scope pressure without letting the story drift.

person:pramodkandel for pulling the blog, captions, repo links, and social snippets into one coherent launch artifact.

person:mukeshdroid for tightening the research translation and catching wording that would have been easy to over-flatten.

person:AaronFeickert for pushing on security phrasing and keeping the public claims bounded.

person:krsnapaudel for unblocking the dashboard screenshot and demo reproducibility path late in the beat.

person:Hakkush-07 for research context on how Mosaic should be positioned relative to the broader proof work.

person:cyphersnake and person:delbonis for implementation-side context where repo state and protocol language needed to line up.
