## Highlights

This cycle was about turning strategic-round technical diligence from a set of high-context conversations into a clean, source-linked room that we can keep using after the round. The core issue was not lack of material. We had plenty: protocol notes, bridge explanations, Strata architecture docs, Glock details, Bitcoin Dollar collateral, and prior answers from partner calls. The problem was that the narrative was moving faster than the protocol confidence in some areas, and the same answers were being rewritten in slightly different ways across the memo, FAQ, and call follow-ups.

By the end of the beat, person:pramodkandel published the diligence room with a changelog and answer bank. That gives us a canonical place for partner-facing technical answers and reduces the risk that future calls drift into improvised explanations. person:john-light helped keep the devrel and external narrative coherent, especially around how we describe Strata, strata-bridge, Glock, and Bitcoin Dollar without overselling certainty.

The main technical cleanup was source-linking. person:delbonis and person:MdTeach closed the last repo references so that claims in the diligence room point back to the right repos and artifacts: repo:Technical-Whitepaper, repo:alpen, repo:strata-bridge, repo:zkaleido, and repo:bitcoin-bosd. This was late, and final review landed after two investor calls, but the room is now much closer to reusable documentation than one-off fundraising collateral.

## What Shipped

We shipped a cleaned diligence room for the strategic round, including a changelog and answer bank. The changelog matters because it gives reviewers a way to distinguish current protocol claims from older collateral. The answer bank matters because several questions were repeating across calls: trust assumptions, bridge security model, proof system boundaries, Strata sequencing, Bitcoin settlement, Glock’s role, and how Bitcoin Dollar depends on underlying protocol maturity.

person:delbonis and person:MdTeach completed the last source-linked repo references. This tightened the path from investor-facing statements back to implementation or technical artifacts. The immediate benefit is diligence hygiene. The longer-term benefit is that these references can become part of our standing technical documentation instead of getting buried in a fundraising folder.

person:krsnapaudel supported the infra-facing diligence answers where partner questions touched deployment assumptions, environment maturity, and operational readiness. This was useful because a number of infra answers depended on people already committed to release work. We still need to reduce that dependency, but the current room is no longer blocked on ad hoc answers from senior reviewers.

person:prajwolrg and person:Rajil1213 helped keep the protocol and bridge framing grounded. Their input was important where the investor narrative wanted clean, simplified claims but the engineering reality required more precision. We should keep that pattern: explain clearly, but do not compress away assumptions that actually matter.

person:AaronFeickert contributed research-side review on the technical narrative, especially where claims needed to be phrased carefully enough for sophisticated diligence readers. This helped keep the materials from sounding like pitch collateral with protocol words pasted on top.

## Coming Up

Next step is converting the diligence room into canonical docs. The highest-value pieces are the answer bank, the bridge security explanation, and the source-linked architecture references. These should not remain artifacts owned only by person:pramodkandel or devrel. They should become shared protocol, bridge, and infra documentation with clear owners.

We also need to close the gap between announcement prep and protocol confidence. The strategic narrative should reflect what the system actually supports today, what is under active implementation, and what remains design intent. This is especially important for product:strata, product:strata-bridge, product:glock, and product:bitcoin-dollar because they sit at different maturity levels but are often discussed together externally.

For the next round of partner calls, we should use the answer bank as the default source of truth. If a new answer is created live, it should either be added to the bank or explicitly marked as a one-off. That will prevent the memo, FAQ, and call follow-ups from diverging again.

## Q&A Summary

The main Q&A theme was bridge risk. Investors wanted to know what assumptions users are making, where Bitcoin finality enters the system, what the proving path guarantees, and which parts are still dependent on operator behavior or future decentralization. The answer we are converging on is concrete: describe the current bridge design, name the assumptions, link to implementation and specs, and avoid implying that future hardening already exists.

There were also repeated questions about how Strata relates to Glock and Bitcoin Dollar. The clean framing is that Strata is the base protocol context, Glock is part of the proving and verification story, and Bitcoin Dollar depends on the broader system reaching the right security and operational thresholds. We should not present these as one monolithic shipped product.

Another question was whether our materials are ready for deeper technical diligence. The answer is now closer to yes, with the caveat that some docs still need to move from fundraising-specific language into durable technical documentation.

## Shoutouts

person:pramodkandel drove the diligence room to publication and kept the changelog and answer bank from becoming scattered follow-up notes.

person:delbonis and person:MdTeach closed the last source-linked repo references under time pressure, which materially improved the credibility of the room.

person:john-light helped keep the external narrative readable without letting it drift too far from the protocol reality.

person:krsnapaudel handled infra diligence context while release work was already competing for attention.

person:prajwolrg, person:Rajil1213, and person:AaronFeickert helped keep the protocol, bridge, and research claims precise where oversimplification would have created future cleanup work.
