## Wins

The strategic round diligence push forced us to compress a lot of implicit protocol knowledge into artifacts that could be reviewed, challenged, and reused. That was painful, but useful. We now have a clearer map of which claims about product:strata, product:strata-bridge, product:glock, and product:bitcoin-dollar are stable enough to say externally, which ones are still implementation-dependent, and which ones need narrower wording until the protocol work catches up.

person:pramodkandel kept the process moving across announcement needs, investor requests, and follow-up diligence threads. person:john-light translated a lot of technical context into material that non-implementers could consume without flattening the security model too much. On the protocol side, person:prajwolrg and person:MdTeach helped keep the repo:Technical-Whitepaper and repo:alpen claims closer to what the system actually does. person:Rajil1213 covered bridge-specific questions for repo:strata-bridge while still dealing with release pressure. person:krsnapaudel helped close infra gaps around deployment, reliability, and operational assumptions. person:AaronFeickert was important in keeping the research/security language disciplined, especially where we were tempted to overstate confidence.

The best outcome is that several diligence artifacts are no longer just fundraising collateral. We identified candidates that should become maintained product docs: bridge security assumptions, glock architecture notes, bitcoin-dollar design rationale, and a cleaner Strata system overview. That gives us a path to reduce repeated explanation load in future investor, partner, and ecosystem conversations.

## Challenges

The main tension was that investor narrative kept getting ahead of protocol confidence. Some of that is normal in a strategic round: the story has to explain where the system is going, not only where the implementation is today. But we repeatedly had to pull wording back from “this is solved” toward “this is the design target,” “this depends on X being implemented,” or “this is still under review.” That slowed the process and created avoidable churn.

Bridge and infra answers were especially constrained because the senior reviewers who could validate them were already committed to release work. person:Rajil1213 and person:krsnapaudel were being asked to answer diligence questions, review external-facing claims, and continue roadmap execution in the same window. The result was not a single failure mode, but a lot of context switching: short reviews, late clarifications, and repeated re-opening of points we thought were closed.

Ownership also became unclear after the announcement work wrapped. During the live diligence period, urgency made it obvious who needed to respond. Once the strategic round announcement was out, the artifact set started drifting. Some docs had a named drafter but no long-term owner. Some claims had been approved in-thread but not moved into canonical locations. Some repo:zkaleido and repo:bitcoin-bosd references were useful for diligence but not clearly tied into maintained product documentation.

The team also carried fatigue from parallel roadmap and diligence work. We got the work done, but not cleanly. Review queues were overloaded, and the same people were being asked to provide judgment across protocol, bridge, infra, and fundraising surfaces.

## What we'd do differently

We should separate “fundraise narrative” from “canonical technical claim” earlier. The narrative can be assembled quickly, but every technical claim should point back to a maintained source of truth or have an explicit owner and confidence level. If a claim cannot be grounded that way, it should be marked as provisional before it reaches investor-facing material.

We should also assign diligence owners by domain at the start, not discover them during review. For this round, bridge and infra depended too heavily on already-loaded reviewers. Next time, person:pramodkandel and person:john-light should be able to route questions through a small owner map: protocol, bridge, infra, research/security, product docs. That would reduce broad pings and make review expectations explicit.

Finally, we should budget post-announcement cleanup as part of the work, not as optional follow-up. The retro beat made clear that announcement completion is not the same as documentation completion.

## Action items

- person:john-light will turn the final diligence packet into an index of canonical and non-canonical artifacts.
- person:prajwolrg and person:MdTeach will identify which product:strata and repo:alpen claims should move into maintained protocol docs.
- person:Rajil1213 will nominate the bridge security and operations docs that should become canonical for product:strata-bridge.
- person:krsnapaudel will define the infra claims that need maintained operational documentation.
- person:AaronFeickert will review high-confidence versus provisional research/security language before reuse.
- person:pramodkandel will create a diligence owner map for future strategic, investor, and partner reviews.
- We will schedule a post-announcement doc hardening window for the next major external narrative push.
