## Wins

The Mosaic announcement did what we needed it to do: it made the work legible outside the research and engineering group without pretending the implementation was farther along than it was. person:john-light and person:pramodkandel kept the public narrative focused on why Mosaic matters for Bitcoin/ZK systems, while person:mukeshdroid, person:AaronFeickert, person:Hakkush-07, and person:cyphersnake helped keep the claims technically grounded.

A major win was that we avoided flattening the research terminology into vague product language. The announcement still carried the core ideas around Mosaic, Glock, and the broader Strata path, but in a form that external readers could reason about. That translation layer was not free, but it was necessary. The questions we received afterward were sharper than expected, which is a good signal: people were engaging with the mechanism, not just reacting to a launch post.

The repo surface also held together reasonably well. Between `repo:mosaic`, `repo:mosaic-torrent`, `repo:g16`, `repo:Technical-Whitepaper`, and `repo:alpen-dashboards`, we had enough supporting material for motivated readers to trace the shape of the work. person:delbonis helped keep the protocol framing coherent, and person:krsnapaudel’s late infra support made the demo path more reproducible than it would have been otherwise.

The strongest outcome from B8 was clarity on the next layer of work. The announcement generated concrete follow-ups: docs gaps, benchmark requests, and Strata integration questions. That is a better failure mode than silence or generic excitement.

## Challenges

The main tension was that the public narrative needed to move faster than the merged implementation. We were describing an architecture whose direction was stable, but whose implementation surface was still catching up. That forced repeated judgment calls about what to say confidently, what to qualify, and what to leave out.

The FAQ backlog was larger than planned. We expected questions about the announcement, but the actual volume skewed more technical: benchmark methodology, trust assumptions, integration timing, and how Mosaic relates to Glock and Strata. We had answers internally, but not always in publishable form. That created drag for person:john-light and person:pramodkandel after launch, because follow-up writing depended on pulling context back out of research and protocol threads.

Benchmarks became a public ask sooner than we were ready for. We had enough intuition to explain the design, but not enough standardized measurement to publish cleanly. That gap matters. Without benchmark framing, external readers will fill in assumptions themselves, and some of those assumptions will be wrong.

The Strata roadmap was the other recurring pressure point. Strata integration stayed intentionally outside the launch scope, but readers naturally treated it as the next question. We did not have a crisp enough bridge between “Mosaic as announced” and “Mosaic in the Strata roadmap.” That made the announcement feel complete at the mechanism level but incomplete at the product-integration level.

Demo reproducibility also depended too much on late infra coordination. person:krsnapaudel helped unblock this, but we should not rely on end-of-window infra support for externally visible artifacts.

## What we'd do differently

We should have prepared the post-launch technical FAQ as a first-class launch artifact, not as a follow-up task. The announcement created the demand; the FAQ should have been ready to absorb it.

We should have separated three layers earlier: research correctness, implementation status, and integration roadmap. During the sprint, those layers were often discussed together because the same people understood all three. Externally, they need different documents with different levels of commitment.

We also should have defined benchmark expectations before launch, even if the numbers were not ready. A short note on what we will measure, what environment matters, and what comparisons are invalid would have reduced ambiguity.

Finally, demo reproducibility should have had an owner and a freeze date. The infra work landed, but too late to feel routine.

## Action items

- person:john-light and person:pramodkandel: publish the Mosaic technical FAQ backlog as a maintained doc, with explicit sections for benchmarks, trust assumptions, Glock relationship, and Strata integration.
- person:mukeshdroid and person:delbonis: draft the benchmark methodology before publishing any headline numbers.
- person:krsnapaudel: document the demo reproducibility path and identify infra assumptions that should be automated before the next external milestone.
- person:john-light: write a short Strata integration note that distinguishes current scope, planned integration work, and open design questions.
- person:AaronFeickert, person:Hakkush-07, and person:cyphersnake: review the terminology used in follow-up docs for correctness without expanding them into research papers.
- Team: for future announcements, require a launch bundle: announcement, FAQ, benchmark plan, repo map, and integration-roadmap note.
