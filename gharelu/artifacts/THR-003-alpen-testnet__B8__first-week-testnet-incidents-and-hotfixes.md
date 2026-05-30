## TL;DR

During the first production-like week of public testnet, we kept `product:strata` online but hit two launch-readiness gaps that required hotfixes outside normal review depth:

1. `repo:strata-p2p` peers churned aggressively under public traffic, causing intermittent block propagation delays and noisy validator reconnects.
2. `repo:alpen-faucet` leaked queued payout jobs on retry, causing duplicate pending entries, rising memory, and degraded faucet latency.

Neither incident caused consensus failure, chain halt, or bridge fund loss. The network stayed live for the full week. The practical impact was degraded public UX: users saw slow RPC responses, faucet delays up to 38 minutes, and stale dashboard alerts. We resolved the p2p issue with PR `strata-p2p#184` / commit `7c41e2f`, and the faucet queue leak with PR `alpen-faucet#52` / commit `b8f3a19`.

The main root cause was launch compression: protocol, bridge, infra, docs, and devrel work converged in the final month, and we accepted manual recovery paths plus partial observability as part of the launch posture.

## Impact

- Public testnet remained online from 2025-10-20 through 2025-10-26.
- No chain halt, no unsafe reorg beyond expected testnet behavior, no bridge fund loss.
- Median RPC latency increased from ~180 ms to ~620 ms during the worst p2p churn window.
- 95th percentile block propagation rose from ~2.4 s to ~19.7 s between 2025-10-21 02:10 and 04:30 UTC.
- Faucet queue depth grew from normal steady state of <40 jobs to 3,812 queued records by 2025-10-23 16:40 UTC.
- Faucet payout latency peaked at 38 minutes; 214 users retried requests during the degraded period.
- Dashboards paged infra responders 27 times in 36 hours, 19 of which were symptoms of the same peer churn condition.
- Devrel had to update public guidance twice because docs moved faster than endpoint stabilization.

## Timeline

All timestamps UTC.

- 2025-10-20 15:00: Public testnet enters first production-like week with public RPC, faucet, dashboards, and explorer enabled.
- 2025-10-20 18:25: person:john-light reports Discord users seeing faucet requests stuck in `pending`.
- 2025-10-20 19:05: person:krsnapaudel confirms faucet worker retries are increasing but payouts are still landing.
- 2025-10-21 02:10: Dashboards start paging on peer count variance across bootnodes.
- 2025-10-21 02:35: person:bewakes observes validator logs showing repeated disconnect/reconnect loops with healthy peers.
- 2025-10-21 03:20: person:prajwolrg narrows the p2p symptom to peer scoring decay after transient RPC backpressure.
- 2025-10-21 04:10: person:storopoli confirms no consensus safety issue; blocks continue finalizing within acceptable testnet bounds.
- 2025-10-21 05:15: Infra manually rotates two bootnodes and raises peer caps from 50 to 90 as a mitigation.
- 2025-10-21 13:40: person:bewakes opens `strata-p2p#184` to damp peer-score penalties during short-lived response stalls.
- 2025-10-21 16:20: `strata-p2p#184` merged at commit `7c41e2f`; rolled to bootnodes first.
- 2025-10-21 18:05: Peer churn drops by ~72%; block propagation p95 returns under 5 s.
- 2025-10-22 09:30: Public RPC latency normalizes, but dashboards continue paging on stale thresholds.
- 2025-10-23 16:40: person:krsnapaudel finds faucet queue at 3,812 records with duplicate pending jobs for the same addresses.
- 2025-10-23 17:10: person:Rajil1213 confirms bridge status polling is healthy and unrelated to faucet retry behavior.
- 2025-10-23 18:00: person:MdTeach identifies retry path creating a new queue row before marking the old row terminal.
- 2025-10-23 19:35: person:krsnapaudel opens `alpen-faucet#52` to make enqueue idempotent on `(address, epoch_window)` and add retry finalization.
- 2025-10-23 20:15: `alpen-faucet#52` merged at commit `b8f3a19`; deployed with manual queue compaction.
- 2025-10-23 21:00: Faucet queue returns below 60 jobs; payout latency drops below 90 seconds.
- 2025-10-24 11:30: person:john-light updates public docs to clarify faucet retry behavior and expected confirmation timing.
- 2025-10-26 18:00: Week closes with network online and no recurrence of either incident.

## Root Cause

The p2p incident was caused by an overly aggressive peer scoring rule in `repo:strata-p2p`. Under normal internal load, short response stalls were rare and did not accumulate enough penalty to evict healthy peers. Under public testnet traffic, RPC and gossip pressure created short stalls across multiple peers at the same time. The scoring code treated those stalls as independent peer quality failures, which created synchronized disconnects. The reconnects then amplified load on bootnodes and made the condition self-reinforcing.

The faucet incident was caused by non-idempotent retry handling in `repo:alpen-faucet`. When a payout attempt failed after transaction construction but before the worker marked the job terminal, retry inserted a new pending row instead of updating the existing row. Users retrying from the UI multiplied this behavior. The worker eventually processed valid payouts, but queue growth increased memory use and delayed legitimate requests.

The broader organizational root cause was launch scope pressure. We intentionally traded bridge completeness and full automation for public availability. That was a reasonable product call, but we did not fully compensate with stricter readiness gates around p2p behavior, faucet idempotency, and dashboard paging quality.

## What Went Well

- The network stayed online and consensus remained healthy.
- Infra and protocol debugged the p2p issue jointly instead of treating it as only an ops symptom.
- person:storopoli quickly separated safety risk from liveness/UX degradation, which kept the response focused.
- person:bewakes produced a small p2p patch instead of a broad scoring rewrite.
- person:MdTeach found the faucet retry bug quickly once queue records were inspected directly.
- Manual recovery paths worked: bootnode rotation, peer cap adjustment, and queue compaction bought enough time for proper fixes.
- Devrel updates were fast and accurate once the operational state was clear.

## What Went Poorly

- Hotfixes bypassed normal review depth because launch week review queues were already saturated.
- Dashboards paged too often and did not collapse related alerts into one incident.
- Public docs and devrel timing moved faster than endpoint stabilization, so users were directed to services whose behavior was still settling.
- Faucet retry semantics were not tested under duplicate user submissions.
- P2P testing did not simulate public traffic patterns with correlated stalls across otherwise healthy peers.
- We relied on person:krsnapaudel as the main infra responder for too many consecutive hours.
- The launch checklist treated manual recovery paths as acceptable without pairing each one with explicit owner, runbook, and alert threshold.

## Action Items

- person:prajwolrg: Add a public-load p2p simulation that includes correlated response stalls and validates peer-score stability. Due 2025-11-07.
- person:bewakes: Follow up `strata-p2p#184` with a peer scoring design note and test coverage for penalty decay bounds. Due 2025-11-10.
- person:krsnapaudel: Replace noisy peer-count pages with incident-level alerts keyed on propagation latency, connected peers, and bootnode saturation together. Due 2025-11-05.
- person:MdTeach: Add faucet queue idempotency tests covering duplicate submits, worker retry, partial transaction construction, and process restart. Due 2025-11-04.
- person:Rajil1213: Add bridge-facing status checks that distinguish faucet degradation from bridge degradation in public dashboards. Due 2025-11-12.
- person:storopoli: Define launch-week consensus safety checks that responders can run before deciding whether to halt, degrade, or continue. Due 2025-11-07.
- person:john-light: Gate public docs updates on endpoint readiness status from infra, not only feature availability. Due 2025-11-03.
- person:ProofOfKeags: Document manual bridge and faucet recovery paths with explicit owner handoff steps. Due 2025-11-14.
- person:prajwolrg and person:krsnapaudel: Add a launch readiness review for any future public testnet milestone where protocol, bridge, and infra queues converge in the same two-week window. Due before next public milestone.
