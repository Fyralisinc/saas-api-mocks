## Wins

The `bitcoind-async-client` 0.4 rollout put the Strata node path in a materially better place. The main win was not any single RPC wrapper, but the fact that we stopped treating bitcoind as a mostly-stable local dependency and started modeling it as an external service with restarts, partial availability, slow responses, and stateful wallet behavior.

person:krsnapaudel drove this from the infra side and kept the crate focused on operational reality instead of just API coverage. The timeout work was the clearest example: it delayed the initial release target, but it gave protocol callers a safer failure mode than hanging node tasks or relying on ad hoc caller-side cancellation. That tradeoff was correct.

The protocol-side feedback loop improved over the cycle. person:prajwolrg, person:MdTeach, person:delbonis, person:storopoli, person:bewakes, and person:alexhui01 helped expose where the crate API was leaking operational concerns into Strata callers, especially around retry boundaries, startup sequencing, and what errors should be considered transient. We ended up with clearer behavior for the code that actually consumes the client in `alpen` and `strata-common`, not just a cleaner crate in isolation.

Observability also moved forward. The dashboards and operational notes were not glamorous, but they closed a real gap: when a Strata node has degraded bitcoind connectivity, we now have better signals for distinguishing RPC latency, restart windows, and caller misuse. person:alexhui01 joining late in B11 and picking up dashboard polish was useful precisely because they had to learn the historical decisions cold. The onboarding material that came out of that process is better than the scattered notes we had before.

## Challenges

The ownership boundary stayed messy. The crate is infra-owned, but many of the sharp edges only appear in protocol-owned callers. That meant review priorities were often split: infra wanted to harden the client, while protocol reviewers were already loaded with core Strata milestone work. We got through it, but the process depended too much on individual availability and context-sharing.

Regtest mocks repeatedly hid production-like bitcoind restart behavior. The test setup was good enough to validate request/response correctness, but not good enough to reveal lifecycle issues. This showed up most clearly around restart timing, wallet readiness, and assumptions about RPC availability immediately after process recovery. We caught these, but later than we should have.

The timeout work created schedule pressure. In hindsight, that was a predictable conflict: safer behavior required changing expectations in callers, and caller changes are where review cost appears. We treated timeout semantics as an implementation detail for too long, when they were actually part of the operational contract.

Observability competed with milestone review bandwidth. Nobody disagreed that metrics and dashboards mattered, but they were easy to defer because they did not block local correctness. That created a late-cycle pileup where operational documentation, dashboard cleanup, and onboarding notes all converged in B11.

Remaining wallet RPC cleanup was deferred. That was the right call for release focus, but it leaves a known area where the client still carries some historical unevenness.

## What we'd do differently

We would define the crate contract earlier, with protocol callers in the room. Specifically: timeout semantics, retry ownership, transient versus terminal errors, and startup readiness should have been written down before implementation stabilized. That would have reduced review churn and made the release risk more explicit.

We would add restart-oriented tests before relying on regtest mocks for confidence. Mocked RPC tests are still valuable, but they should not be our main evidence for node operations behavior. A small production-like harness that exercises bitcoind restart, delayed wallet readiness, and in-flight request failure would have caught several issues earlier.

We would separate release blockers from operational follow-through more cleanly. Dashboard polish and onboarding docs do not need to block the crate release, but they do need owners and dates. Leaving them as “after the milestone” work made them easier to compress into the end of the cycle.

We would also onboard late-cycle contributors through decision records, not chat archaeology. person:alexhui01’s ramp exposed how much context was implicit. The resulting docs helped, but the cost came after decisions had already been made.

## Action items

- person:krsnapaudel: write and maintain the `bitcoind-async-client` operational contract covering timeout, retry, startup, and error classification semantics.
- person:MdTeach and person:prajwolrg: review protocol caller assumptions against that contract and remove caller-side behavior that duplicates or contradicts the client.
- person:bewakes and person:alexhui01: finish converting B11 operational notes into onboarding material for future node-client work.
- person:storopoli and person:delbonis: identify the minimum restart-oriented test harness needed beyond regtest mocks.
- person:krsnapaudel: file scoped follow-up issues for the deferred wallet RPC cleanup, with release impact labeled explicitly.
- person:alexhui01: keep dashboard polish tied to concrete operational questions, especially bitcoind restart windows, RPC latency, and readiness failures.
