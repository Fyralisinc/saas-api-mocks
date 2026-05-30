## Wins

The biggest win was that the zkaleido API boundary mostly held through a year of pressure from product integration. We started with a more general research-facing proving backend interface, then repeatedly narrowed it around what Glock and Strata actually needed. That narrowing was frustrating at points, but it forced useful separation: circuit-facing abstractions stayed reasonably clean, backend-specific details did not leak as much as they could have, and downstream consumers got something they could integrate without carrying the full research surface area.

person:storopoli and person:mukeshdroid did a lot of the heavy lifting in turning benchmark and backend concerns into concrete reviewable code rather than open-ended design discussion. person:ceyhunsen and person:cyphersnake helped keep the implementation grounded across repo boundaries, especially where zkaleido touched g16 and hash-benchmarks. person:Zk2u’s infra context was useful when we had to distinguish real proving backend behavior from environment noise.

We also learned which abstractions are actually valuable. Genericity around backend selection and proof plumbing paid for itself. Genericity around every possible research workflow did not. That is an important distinction for 2026: zkaleido should remain flexible where the protocol stack needs substitutability, but not pretend to be a universal proving framework when the immediate value is reliable support for Glock and Strata.

The final B11 retro period also clarified the release model. Monthly zkaleido releases are the right default. They are frequent enough to avoid large integration dumps, but not so frequent that every backend tweak forces immediate coordination across zkaleido, g16, hash-benchmarks, and alpen.

## Challenges

Benchmark instability was the recurring technical drag. We spent too much time arguing about performance claims that were hard to reproduce or hard to attribute. Some regressions were real, some were measurement artifacts, and some were caused by changes in dependency versions or hardware assumptions. That made review slower and lowered confidence in optimization work.

The second challenge was cross-repo timing. zkaleido changes often looked self-contained until Glock or Strata integration pulled in g16, hash-benchmarks, or alpen changes at the same time. Release work then bunched near integration windows, which created pressure to merge incomplete cleanup or defer profiling work.

The research/product tension was real. As driver, I often pushed to preserve API generality because we knew the proving backend surface would keep evolving. But Glock and Strata delivery needs were concrete, scheduled, and easier to validate. In practice, product constraints usually won, and that was often correct. The cost was that batching work drifted and some memory profile questions remained unresolved by the end of the thread.

We also did not assign release cadence ownership early enough. Everyone agreed releases should be more predictable, but nobody consistently owned the mechanics until late. That left too much implicit coordination between person:AaronFeickert, person:storopoli, person:mukeshdroid, person:ceyhunsen, and person:cyphersnake.

## What we'd do differently

I would define the benchmark contract earlier. Before making performance claims, we should have specified datasets, hardware assumptions, warmup behavior, version pinning, and acceptable variance. The lack of this contract made otherwise useful benchmark work harder to trust.

I would also split API design into two explicit layers: a stable integration API for Glock and Strata, and an experimental research API that can change without blocking releases. We effectively discovered this split through repeated narrowing. Next time we should name it upfront.

For cross-repo work, I would create integration branches or release trains earlier instead of relying on ad hoc coordination near the end of a window. zkaleido cannot be treated as isolated infrastructure when its meaningful consumers live across product and protocol repos.

Finally, I would treat memory profiling as release-blocking only when we have concrete thresholds. We carried unresolved memory profile work as a concern, but without crisp pass/fail criteria it became easy to defer and hard to prioritize against delivery.

## Action items

1. Assign explicit monthly zkaleido release ownership. person:AaronFeickert will own the first cycle, then rotate with person:storopoli and person:mukeshdroid unless another owner is named.

2. Define a benchmark policy for repo:zkaleido and repo:hash-benchmarks: pinned dependency versions, machine profile, input sets, variance bounds, and required reporting format.

3. Separate stable integration APIs from experimental research APIs in zkaleido documentation and review labels. Glock and Strata-facing changes should target the stable surface by default.

4. Create a cross-repo integration checklist covering repo:zkaleido, repo:g16, repo:hash-benchmarks, and repo:alpen before each release window.

5. Convert unresolved memory profile work into tracked issues with measurable thresholds, owners, and release impact labels.

6. Keep batching work visible as a roadmap item rather than letting it sit as implicit technical debt behind backend hardening.
