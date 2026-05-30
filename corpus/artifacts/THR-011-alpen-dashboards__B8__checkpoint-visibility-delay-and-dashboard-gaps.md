## TL;DR

On 2025-10-14, staging users and operators saw delayed checkpoint visibility for `product:strata` state propagated through `product:strata-bridge`. Bridge-side alerts fired as expected, but the Strata liveness dashboard made the system look healthier than it was because panels used averaged checkpoint intervals instead of tail latency and age-of-last-checkpoint. The incident lasted 74 minutes from first alert to confirmed recovery.

No funds were at risk. No mainnet systems were affected. The main impact was operational: triage took longer than it should have because bridge and protocol dashboards described the same failure mode with different terms and different aggregation windows.

The immediate fix was to add explicit checkpoint age and p95/p99 visibility panels in `repo:alpen-dashboards` and tighten staging paging thresholds. Follow-up work is needed to make cross-repo observability ownership explicit across `repo:alpen`, `repo:strata-bridge`, and `repo:mosaic`.

## Impact

From 2025-10-14 09:18 UTC to 10:32 UTC, checkpoint visibility in staging lagged by up to 41 minutes. Expected staging checkpoint visibility was under 8 minutes p95. During the incident:

- 17 checkpoints were produced late or became visible late, depending on which side of the pipeline was being observed.
- Bridge alerts fired 6 times, correctly flagging delayed checkpoint consumption.
- Strata liveness panels showed a 12-minute rolling average, which stayed below the configured 15-minute warning threshold for most of the incident.
- Two staging bridge test runs were paused by person:Rajil1213 and person:ProofOfKeags.
- One protocol review slot was lost while person:prajwolrg and person:delbonis helped correlate bridge and Strata metrics manually.

No production customer impact, no data loss, and no consensus safety issue were identified.

## Timeline

All times UTC on 2025-10-14.

09:18 - Bridge staging alert fires: `checkpoint_visibility_delay_seconds > 900` for 3 consecutive evaluations. person:krsnapaudel acknowledges.

09:22 - person:Rajil1213 confirms bridge watcher is not stalled and reports that latest observed checkpoint is 23 minutes behind expected staging cadence.

09:27 - Strata dashboard liveness panel still shows green because the rolling average interval is 11.8 minutes over a 1-hour window.

09:34 - person:prajwolrg joins triage and checks sequencer and checkpoint producer logs in `repo:alpen`. No crash loop or restart is observed.

09:41 - person:delbonis identifies that the dashboard panel is averaging checkpoint intervals and not showing age of latest visible checkpoint.

09:49 - person:MdTeach is asked to sanity-check whether this could be a proof generation slowdown. No proof backlog above normal staging variance is found.

09:56 - person:krsnapaudel temporarily lowers staging alert evaluation from 15 minutes to 8 minutes and adds an ad hoc Grafana query for `max(time() - checkpoint_visible_timestamp)`.

10:08 - Delayed visibility peaks at 41 minutes. Bridge tests remain paused.

10:17 - Backlog begins clearing after the checkpoint indexer catches up. We did not find a code deploy directly correlated with recovery.

10:32 - Checkpoint visibility returns to under 7 minutes. Bridge staging tests resume.

11:05 - person:delbonis opens dashboard follow-up PR `alpen-dashboards#87`, adding checkpoint age, p95, and p99 panels.

11:42 - person:krsnapaudel opens `alpen-dashboards#88`, updating staging alert thresholds and routing labels.

13:10 - Incident review starts with person:krsnapaudel, person:prajwolrg, person:delbonis, person:Rajil1213, and person:ProofOfKeags.

## Root Cause

The technical root cause was not a single service failure. The system had a transient staging delay in checkpoint indexing/visibility, and our observability stack made that delay harder to diagnose than necessary.

Bridge metrics measured the symptom directly: elapsed time since the last bridge-visible checkpoint. Protocol dashboards measured checkpoint production cadence using rolling averages. Those are both useful signals, but during this incident they answered different questions:

- Bridge asked: “How old is the latest checkpoint I can act on?”
- Strata liveness asked: “What is the average interval between recent checkpoints?”

The average interval hid tail behavior. A few normal checkpoints inside the 1-hour range diluted the stalled period, so the dashboard stayed green while the operational state was clearly degraded.

The organizational root cause was unclear ownership. `repo:alpen-dashboards` started as Grafana cleanup work driven by person:krsnapaudel, but it had become cross-repo observability infrastructure for `product:strata`, `product:strata-bridge`, and `product:mosaic`. We did not have a written owner for metric vocabulary, alert semantics, or staging threshold policy across bridge and protocol teams.

## What Went Well

Bridge alerts fired correctly and gave us the first reliable signal. person:Rajil1213 and person:ProofOfKeags paused staging bridge tests quickly, which avoided noisy downstream failures.

person:krsnapaudel had enough Grafana and infra context to create an ad hoc query during the incident rather than waiting for a full dashboard change.

person:prajwolrg and person:delbonis were able to correlate protocol-side logs with bridge-visible delay without finding evidence of a consensus or proof safety issue.

The incident gave us a concrete test case for the dashboards. Before this, the dashboard cleanup work was mostly judged by readability. This showed where the panels failed under operational pressure.

## What Went Poorly

The Strata liveness panel encoded a misleading success condition. A green panel during a 41-minute visibility delay is worse than a missing panel because it actively slows triage.

We used different language for the same failure mode. Bridge used “visibility delay”; protocol used “checkpoint interval” and “liveness.” That forced humans to translate during an incident.

Paging thresholds were too conservative for staging. The system waited for a condition that was already operationally relevant.

Dashboard ownership was implicit. person:krsnapaudel was the driver, but protocol and bridge semantics lived with different teams. That made it unclear who could approve alert meaning versus panel layout.

Operational reliability work again competed with feature review queues. We pulled person:prajwolrg and person:delbonis into manual triage while protocol review backlog was already tight.

## Action Items

- person:krsnapaudel: Merge `alpen-dashboards#87` and `alpen-dashboards#88`; include checkpoint age, p95, p99, and max visibility delay panels for staging and production. Due 2025-10-17.

- person:delbonis: Define canonical checkpoint observability terms across protocol dashboards: produced, indexed, visible, consumed, finalized. Add this to `repo:alpen-dashboards/docs/checkpoint-metrics.md`. Due 2025-10-21.

- person:Rajil1213: Map bridge alert names to the canonical checkpoint terms and update `repo:strata-bridge` metric labels where needed. Due 2025-10-24.

- person:prajwolrg: Add protocol-side metric review to checkpoint-related PR checklist in `repo:alpen`. Due 2025-10-24.

- person:ProofOfKeags: Add a staging bridge test assertion that fails when checkpoint visibility age exceeds 15 minutes, independent of dashboard state. Due 2025-10-28.

- person:MdTeach: Document proof-generation backlog signals that should be checked before treating checkpoint delay as a proof issue. Due 2025-10-28.

- person:john-light: Update internal staging status notes so devrel-facing summaries distinguish delayed visibility from delayed finality. Due 2025-10-31.

- person:krsnapaudel and person:delbonis: Establish dashboard ownership rules for `product:strata`, `product:strata-bridge`, and `product:mosaic`, including who approves alert semantics. Due 2025-11-05.
