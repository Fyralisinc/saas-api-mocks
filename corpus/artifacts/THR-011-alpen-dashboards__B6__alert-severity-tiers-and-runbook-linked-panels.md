## Goal

Make `repo:alpen-dashboards` useful to engineers who did not author the panels by standardizing alert severity tiers and linking every actionable panel to a runbook. The immediate scope is observability for `product:strata`, `product:strata-bridge`, and `product:mosaic`, with enough shared language that protocol and bridge failures can be triaged without translating team-local terminology in Slack.

The practical target is: when a dashboard panel is red, the on-call or reviewing engineer should know whether to wake someone, which subsystem probably owns it, and what the first three checks are. This is intentionally not “add more graphs.” We already have enough raw telemetry to confuse people. The missing layer is operational interpretation.

Driver: person:krsnapaudel. Reviewers should include person:prajwolrg, person:MdTeach, person:delbonis, person:Rajil1213, and person:ProofOfKeags. person:john-light should review only the sanitized external-facing screenshots and terminology that may later appear in docs.

## Non-goals

This does not replace repository-local debugging tools, tracing, or structured logs. It also does not attempt to define a company-wide incident process beyond dashboard semantics.

This does not require all repos to expose identical metric names immediately. `repo:alpen`, `repo:strata-bridge`, and `repo:mosaic` have different internal state machines, and forcing a premature naming migration would create churn. The design creates a translation layer at the dashboard and alert level first.

This does not make Grafana the source of truth for protocol correctness. For example, proof verification failures, bridge deposit accounting, and state transition validity still need code-level invariants and tests. The dashboard tells us where to look and how urgently.

This also does not commit us to publishing dashboards externally. Devrel can use sanitized screenshots later, but production runbooks may include internal endpoints, private labels, peer names, and operational assumptions that should not leave the company.

## Background

The current dashboard set started as Grafana cleanup: remove stale panels, group Prometheus queries, and make node/prover/bridge health visible. That helped the authors, but internal users still report that the dashboards are “useful only if you already know what broke.” This is especially visible across `product:strata` and `product:strata-bridge`.

Protocol engineers tend to describe failures in terms of chain state, proof jobs, DA commitments, block derivation, and finality lag. Bridge engineers tend to describe similar symptoms in terms of deposits, withdrawals, operator signing, outpoint tracking, and checkpoint inclusion. The underlying failure can be the same: the system stopped making forward progress, or the observed Bitcoin state no longer matches the expected rollup/bridge state. The dashboard currently exposes these as unrelated metrics.

We also have a review queue problem. Operational reliability work competes with feature release reviews, so dashboard fixes often land as one-off panels without runbooks. That compounds the issue: every alert without a runbook becomes an interrupt to the person who remembers the query.

The design change for this beat is to make severity and runbook linkage first-class dashboard requirements. A graph is not “done” unless it answers: what does this measure, how bad is this condition, who owns first response, and what should the responder do next?

## Proposed Design

### Severity Tiers

Define four severities for dashboard panels and alerts:

| Tier | Meaning | Response expectation |
|---|---|---|
| `S0` | User funds, bridge safety, or consensus-critical correctness may be at risk. | Page immediately. Stop release/deploy activity until understood. |
| `S1` | Core system forward progress is impaired or will become unsafe without intervention. | Same-day response, interrupt current work if no active owner. |
| `S2` | Degraded reliability, increased latency, partial feature impact, or early warning. | Triage during working hours, create/attach issue. |
| `S3` | Informational, capacity trend, flaky dependency, or non-actionable anomaly. | No page. Review during dashboard hygiene or planning. |

The tier should describe operational urgency, not engineering importance. A bug in proof generation can be `S0` if it risks accepting invalid state, `S1` if it halts proving, `S2` if it only increases proving latency under load, and `S3` if it is a trend we are watching.

Panel titles should include the tier prefix only for actionable panels, for example:

- `[S0] Bridge balance invariant mismatch`
- `[S1] Strata derivation stalled`
- `[S2] Proof job queue age high`
- `[S3] Bitcoin RPC latency p95`

Severity names are intentionally boring. person:delbonis raised that “critical/high/warn/info” maps poorly to protocol safety because “critical” gets overused. Numbered tiers make escalation easier and avoid importing inconsistent meanings from cloud monitoring defaults.

### Runbook-Linked Panels

Every `S0`, `S1`, and `S2` panel must include a runbook link in the panel description. `S3` panels should include a short description but do not require a runbook.

Runbooks live beside dashboard ownership metadata in `repo:alpen-dashboards`, using stable paths:

- `runbooks/strata/<name>.md`
- `runbooks/strata-bridge/<name>.md`
- `runbooks/mosaic/<name>.md`
- `runbooks/shared/<name>.md`

Each runbook should use this shape:

1. **Signal**: what alert/panel triggered and what metric expression backs it.
2. **Impact**: what user, operator, or protocol behavior may be affected.
3. **First checks**: three to five commands, dashboards, logs, or repo-specific checks.
4. **Likely owners**: team or handles for first escalation.
5. **False positives**: known benign cases.
6. **Resolution notes**: what to record after mitigation.

For cross-repo symptoms, prefer a shared runbook with repo-specific subsections. For example, “forward progress stalled” should cover `repo:alpen` derivation/proving and `repo:strata-bridge` checkpoint/deposit processing using one top-level concept.

### Metric Language Translation

Add dashboard-level labels for common failure classes:

- `progress`: block derivation, checkpoint movement, bridge event processing, proof completion.
- `safety`: invariant mismatch, invalid proof, unexpected state root, balance/accounting mismatch.
- `liveness`: peer connectivity, Bitcoin RPC availability, mempool relay, signer availability.
- `capacity`: queue depth, disk, CPU, memory, proof throughput.
- `operator-action`: requires key holder, deployer, sequencer/prover operator, or bridge operator action.

This gives bridge and protocol teams a shared vocabulary without renaming every metric immediately. For example, bridge “withdrawal finalization delay” and protocol “finality lag” can both be tagged as `progress` with different owners.

### Ownership Metadata

Each dashboard folder should declare owner hints:

- `primary_team`: `infra`, `protocol`, `bridge`, `research`, or `devrel`
- `first_response`: handles or team
- `reviewers`: expected reviewers for query/runbook changes
- `external_safe`: whether screenshots can be sanitized for docs

Initial ownership:

- `product:strata`: first response protocol, with person:prajwolrg, person:MdTeach, and person:delbonis as reviewers.
- `product:strata-bridge`: first response bridge, with person:Rajil1213 and person:ProofOfKeags as reviewers.
- `product:mosaic`: first response protocol/infra depending on deployment surface.
- Dashboard plumbing and Grafana provisioning: person:krsnapaudel.

person:john-light can request screenshots, but screenshots should be generated only from panels marked `external_safe: true` and with internal labels removed.

## Trade-offs

The main trade-off is upfront friction. Requiring runbooks before adding actionable panels slows down dashboard changes. I think this is the right cost because an alert without a runbook just transfers work from the author to the responder at the worst possible time.

Numbered severity tiers are less descriptive than words, but they reduce debate. The important thing is consistent response behavior, not perfect naming. We should expect some reclassification after incidents.

A dashboard translation layer may hide metric inconsistency for too long. That is acceptable for this phase. Once we see repeated mappings, we can promote stable names back into `repo:alpen`, `repo:strata-bridge`, and `repo:mosaic`.

There is also a risk that runbooks become stale. To counter that, any alert query change touching an `S0` to `S2` panel should require checking the linked runbook. Stale runbooks are worse than missing ones because they create false confidence.

## Rollout Plan

1. Add severity definitions and runbook template to `repo:alpen-dashboards`.
2. Convert the top ten operationally important panels across `product:strata`, `product:strata-bridge`, and `product:mosaic` to use severity prefixes and runbook links.
3. Have person:prajwolrg, person:MdTeach, and person:delbonis review protocol-facing tiers and first checks.
4. Have person:Rajil1213 and person:ProofOfKeags review bridge-facing tiers and ownership language.
5. Mark panels that person:john-light can use for sanitized docs screenshots, but do not block internal rollout on docs readiness.
6. Add a lightweight review rule: new `S0`, `S1`, or `S2` panels must include a runbook link before merge.
7. After two weeks of use, person:krsnapaudel should collect misclassified alerts and runbook gaps, then do one severity cleanup pass rather than accepting piecemeal naming drift.
