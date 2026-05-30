## Wins

We got the first public launch narrative across the line without waiting for every protocol document to stabilize. That was the right tradeoff. The repo and whitepaper were still moving, and if we had blocked the launch package on final architecture language, we would have missed the window entirely.

person:pramodkandel drove the package end-to-end and kept the framing tied to product:strata instead of drifting into generic Bitcoin scaling language. That helped the launch read as a concrete technical direction rather than a broad positioning exercise. person:delbonis, person:bewakes, person:prajwolrg, and person:MdTeach gave enough protocol-side review to keep the public wording from overcommitting, especially around architecture boundaries that were still being edited in repo:alpen and repo:Technical-Whitepaper.

We also learned where our public surface is brittle. The first blog forced us to reconcile repo-level naming, whitepaper phrasing, and external-reader expectations. That exposed gaps earlier than a purely internal docs pass would have. In particular, feedback on the blog made it obvious that readers need a public technical explainer that sits between the launch narrative and the full whitepaper.

The launch also created a usable baseline for future docs. We now have concrete language for what product:strata is, what problem it is solving, and which technical details we are not ready to expand publicly. That baseline should reduce review time for the next external artifact if we treat it as maintained source material rather than one-off copy.

## Challenges

The biggest challenge was that the launch narrative needed to move faster than the protocol docs. That meant public wording repeatedly lagged behind repo and whitepaper edits. We had several moments where a sentence was technically correct when drafted but became stale after changes landed elsewhere.

There was also real disagreement about how much architecture to expose. Early engineering feedback pulled in different directions: enough detail to establish credibility, but not so much that we freeze internal designs prematurely or invite readers to reason from incomplete interfaces. That tension was valid, but we did not have a clean decision path for resolving it. As a result, some edits became repeated loops over the same question: “Is this explanation useful, or is it accidentally committing us?”

Ownership also got fuzzy after the launch package shipped. In the B8 retro window, we recorded follow-up work around a public technical explainer and cleaner repo-level onboarding docs, but those items did not immediately have owners. That is a failure mode for docs-heavy work: the artifact ships, everyone agrees on the next thing, and then the next thing becomes ambient responsibility.

The blog feedback also created new roadmap questions. Some reader confusion was purely explanatory, but some of it pointed at product and protocol sequencing. We need to separate “we should explain this better” from “we have not decided this yet” faster.

## What we'd do differently

Next time, we should define the source of truth before drafting public copy. For this launch, repo:alpen, repo:Technical-Whitepaper, and the blog draft all acted as partial sources of truth. That created unnecessary reconciliation work. A short launch-language spec, owned by person:pramodkandel and reviewed by protocol, would have made later edits cheaper.

We should also split review into two explicit passes. First: technical correctness from protocol reviewers like person:delbonis, person:bewakes, person:prajwolrg, and person:MdTeach. Second: public readability and sequencing from devrel. Mixing those concerns in the same pass made it harder to tell whether a comment was blocking, clarifying, or roadmap-shaping.

Finally, we should not let follow-up docs leave retro without named owners. If the public technical explainer and repo onboarding docs matter, they need owners, scope, and expected date ranges.

## Action items

- Assign a single owner for the public technical explainer for product:strata. Proposed owner: person:pramodkandel, with protocol review from person:delbonis and person:prajwolrg.
- Create a repo-level onboarding docs issue in repo:alpen covering project structure, local setup, and where architecture decisions live. Proposed reviewers: person:bewakes and person:MdTeach.
- Maintain a launch-language source file in repo:.github or repo:Technical-Whitepaper for externally approved wording.
- Add a lightweight review rubric for public technical posts: correctness, commitment risk, reader model, and dependency on unstable roadmap items.
- Convert blog feedback into two queues: documentation fixes and roadmap questions. Documentation can move immediately; roadmap questions need protocol owner review before becoming public claims.
