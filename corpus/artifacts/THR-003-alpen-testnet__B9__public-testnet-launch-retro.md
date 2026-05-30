## Wins

We shipped the Alpen public testnet in the 2025-08-04 to 2025-11-02 window, with `product:strata` and `product:strata-bridge` available publicly across the core surface area we needed: node software, bridge path, faucet, dashboards, and checkpoint visibility. The launch landed two weeks later than the first target, but it did land, and the final state was materially better than forcing the original date with weaker observability and unclear recovery paths.

person:prajwolrg kept the launch thread coherent across protocol, bridge, and infra, especially once the review queues started converging in October. person:krsnapaudel pushed the infra pieces far enough that we had workable endpoint, faucet, dashboard, and operational visibility coverage instead of treating launch as just a protocol milestone. person:Rajil1213 and person:ProofOfKeags kept the bridge path moving under an intentionally reduced scope, which let us preserve public availability without pretending bridge completeness was done. person:MdTeach, person:storopoli, and person:bewakes helped absorb protocol review and stabilization work late in the cycle, which was necessary given how many repos were active at once: `repo:alpen`, `repo:strata-bridge`, `repo:strata-p2p`, `repo:alpen-faucet`, `repo:alpen-dashboards`, and `repo:checkpoint-explorer`.

Devrel also moved with useful urgency. person:john-light helped get public-facing material in motion early enough that the launch had an external shape, not just internal release notes. That created pressure, but it also forced us to clarify what the public testnet was actually for.

## Challenges

The main tradeoff was scope. We chose public availability over bridge completeness. Bridge exits, automated recovery, and external validator onboarding were moved into follow-up work. That was the right call by the end, but we did not frame it early or cleanly enough. Internally, we understood the distinction between “public testnet is live” and “all expected bridge/testnet operations are self-serve and complete.” Externally, that distinction needed sharper language sooner.

The final month compressed too much review into too few people. Protocol, bridge, and infra review queues converged at the same time, and the people with the most context were also the people needed for last-mile fixes. That made review latency a launch risk instead of a normal engineering constraint.

Endpoint stabilization lagged behind public docs and devrel timing. Docs moving faster than endpoints was useful for finding gaps, but it also meant some public-facing claims had to be checked against changing operational reality. We should expect this in future launches and design a tighter handoff between docs, endpoint ownership, and launch readiness.

Manual recovery paths remained part of the launch posture. That was acceptable for this testnet, but it increased operator load and made readiness harder to reason about. We had paths, but not enough automation or drill history to treat them as boring.

## What we'd do differently

Start the launch readiness checklist earlier, and make it the source of truth. For this launch, the checklist became most useful in the final stretch, when it was already carrying too many decisions. Next time it should exist before the last month and include explicit owners for endpoint stability, bridge scope, recovery, docs, dashboards, faucet behavior, and external validator readiness.

Separate “launch blocker,” “launch caveat,” and “post-launch follow-up” more aggressively. Bridge exits and automated recovery should not have lived in an ambiguous middle state for as long as they did. Once we knew they were likely follow-up work, we should have written that down and aligned devrel, protocol, bridge, and infra around the implications.

Throttle review queues before the final two weeks. We should reserve named reviewer capacity for launch-critical repos and avoid assuming normal review throughput during stabilization. The late-cycle review load was predictable in hindsight.

Make manual recovery an explicit launch mode, not an implicit fallback. If we launch with manual recovery in the posture, we should document the operator, trigger, runbook, expected time to recover, and escalation path.

## Action items

- person:prajwolrg: create the next launch readiness checklist at least four weeks before target launch, with blocker/caveat/follow-up categories.
- person:krsnapaudel: define endpoint stability criteria for public launches, including monitoring, ownership, and rollback expectations.
- person:Rajil1213 and person:ProofOfKeags: write the bridge follow-up plan for exits and recovery, with milestones that distinguish demo readiness from public operator readiness.
- person:MdTeach and person:storopoli: propose a launch review rotation for protocol-owned repos so final-week review pressure is distributed earlier.
- person:john-light: align public docs language to the launch checklist status, especially for reduced-scope features and known caveats.
- person:prajwolrg: schedule a recovery-path drill before the next public milestone and record gaps as launch blockers or explicit caveats.
