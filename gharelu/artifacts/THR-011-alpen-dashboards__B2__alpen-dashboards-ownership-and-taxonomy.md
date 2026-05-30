**Goal**

Define the ownership model and dashboard taxonomy for `repo:alpen-dashboards` so we can maintain a small, reliable observability surface across `product:strata`, `product:strata-bridge`, and `product:mosaic` without turning every release thread into a Grafana cleanup thread.

The immediate deliverable for this design pass is a stable folder and dashboard layout that separates four audiences:

1. Operators watching production health.
2. Protocol engineers debugging `product:strata` liveness.
3. Bridge engineers debugging `product:strata-bridge` safety and withdrawal flow.
4. Devrel using curated demo views for external or partner-facing walkthroughs.

I want the dashboards to become boring infrastructure. `person:krsnapaudel` owns the dashboard repository and deployment mechanics, but metric semantics must stay owned by the teams that emit and interpret them. `person:prajwolrg`, `person:MdTeach`, and `person:delbonis` should own protocol liveness definitions. `person:Rajil1213` and `person:ProofOfKeags` should own bridge safety definitions. `person:john-light` should own the devrel-facing view requirements, with infra keeping final say on whether a panel belongs in production operator views.

**Non-goals**

This document does not define every metric we will eventually emit. It defines the dashboard taxonomy and ownership boundary.

This is also not a full incident response redesign. Alert routing, paging policy, and runbook depth remain separate work, though the operator dashboards should become the entry point for those flows.

We are not trying to make one dashboard that satisfies everyone. The previous mixed views failed because they combined protocol internals, bridge drilldowns, and demo status into a single folder. That made panels hard to delete, hard to review, and too noisy during an actual issue.

We are not replacing Grafana or Prometheus in this pass. If we later need traces, structured event indexing, or proof-generation flamegraphs, those can land as separate observability products.

**Background**

The dashboard work started as Grafana cleanup in `repo:alpen-dashboards`, but it has grown into cross-repo observability ownership because the same failure modes appear in different products under different names.

For example, the protocol team tends to describe failures around chain progress, block import, DA availability, state transition validity, prover latency, and reorg handling. The bridge team tends to describe failures around deposit recognition, Bitcoin confirmation depth, checkpoint inclusion, withdrawal readiness, signing progress, and finalization safety. Operationally, several of these are the same class of question: is the system advancing, is it stuck, is it unsafe, or is it merely slow?

The current dashboards blur those distinctions. Some panels are operator-critical but buried next to developer diagnostics. Some bridge panels assume metrics that are not emitted consistently from `repo:strata-bridge`. Some devrel panels are useful for demos but misleading as health indicators because they compress several states into a single green/red widget.

The design tension from this beat is real: `person:prajwolrg` is right that fewer panels make dashboards more usable during incidents, while `person:Rajil1213` is right that bridge debugging needs drilldowns that would be too specific for the top-level operator view. The taxonomy should resolve this by separating entry-point health from domain-specific investigation.

**Proposed Design**

We will organize `repo:alpen-dashboards` around dashboard intent, not repository name. The top-level Grafana folders should be:

- `00 Operator Health`
- `10 Protocol Liveness`
- `20 Bridge Safety`
- `30 Mosaic`
- `40 Devrel Demo`
- `90 Experimental`

The numeric prefixes are intentional. They keep navigation stable and make review diffs easier when Grafana exports folder metadata.

`00 Operator Health` is the only folder expected to be used during first response. It should contain a small number of dashboards with low-cardinality, production-safe panels. The primary dashboard should answer:

- Are Bitcoin inputs being observed at expected depth?
- Is `product:strata` advancing its canonical chain view?
- Are batches or checkpoints being produced and accepted?
- Is proof generation within expected latency bounds?
- Is bridge deposit and withdrawal processing moving?
- Are RPC, indexer, and database dependencies healthy?
- Are error rates above baseline?

Panels here should be aggressively curated. If a panel requires knowing the internals of the state transition function, bridge claim format, or prover queue implementation, it does not belong in the operator entry point.

`10 Protocol Liveness` is owned semantically by protocol. This folder should contain dashboards for Strata node progress, block execution, fork choice, proof jobs, checkpoint construction, DA reads/writes, mempool behavior where relevant, and state sync. `person:prajwolrg` should review panel count and naming. `person:MdTeach` and `person:delbonis` should validate that liveness panels map to actual protocol invariants rather than implementation noise.

A key rule: protocol liveness panels should distinguish “no new work exists” from “work exists but we are not advancing.” For example, a flat block-height panel is insufficient unless paired with upstream Bitcoin tip, expected batch interval, or pending work queue depth. The dashboard should make stuckness observable, not just display counters.

`20 Bridge Safety` is owned semantically by bridge. This folder should provide the drilldowns `person:Rajil1213` asked for without polluting the operator view. Bridge dashboards should separate:

- Bitcoin deposit observation.
- Confirmation-depth tracking.
- Deposit inclusion into Strata.
- Withdrawal request creation.
- Withdrawal proof or claim readiness.
- Signing or authorization progress.
- Bitcoin transaction broadcast and confirmation.
- Safety holds, mismatched state, and manual intervention states.

The most important bridge taxonomy decision is to avoid a generic “bridge healthy” panel unless it decomposes into safety and liveness. A bridge can be live but unsafe, safe but delayed, or blocked by upstream Bitcoin conditions. The dashboard should reflect those states explicitly.

`30 Mosaic` should hold product-specific dashboards for `product:mosaic`. These should follow the same pattern: operator-critical summaries bubble up into `00 Operator Health`, while implementation-specific panels remain in the Mosaic folder.

`40 Devrel Demo` is curated for `person:john-light` and devrel workflows. These dashboards can simplify terminology and combine signals for readability, but they must not be used as production health sources. Panels here should be labeled as demo views in dashboard descriptions. If a devrel panel becomes operationally useful, we promote the underlying metric into operator or domain folders rather than linking operators to the demo dashboard.

`90 Experimental` is the staging area. Any new dashboard or high-churn panel starts here. Promotion requires an owner, a short description, stable metric names, and at least one reviewer from the emitting domain. Experimental dashboards should be deleted or promoted within two release cycles.

Metric naming should converge around shared concepts:

- `observed_height` for upstream chain observation.
- `processed_height` for locally processed chain state.
- `finalized_height` for state treated as irreversible under our assumptions.
- `pending_items` for known work not yet processed.
- `stalled_seconds` for elapsed time since last successful progress.
- `safety_hold_total` for explicit hold states.
- `invalid_transition_total` or domain-specific equivalents for rejected state changes.

We should not force every repo to use identical metric names immediately. Instead, `repo:alpen-dashboards` can normalize presentation through panel titles and legends while the emitting repos migrate over time.

**Trade-offs**

This design creates more folders than a minimal Grafana cleanup would, but it reduces semantic conflict. Operator health stays small, while bridge and protocol retain enough depth to debug real failures.

The main cost is ownership discipline. If every team adds panels directly to `00 Operator Health`, the design collapses back into the current mixed state. To prevent that, `person:krsnapaudel` should require infra review for operator dashboards and domain review for protocol or bridge dashboards.

Another trade-off is temporary duplication. A proof latency signal may appear in both operator health and protocol liveness. That is acceptable if the operator panel is summary-level and the protocol panel has breakdowns by job type, queue, prover instance, or failure reason.

The metric-language convergence will be slow because `repo:alpen`, `repo:strata-bridge`, and `repo:mosaic` do not emit metrics uniformly today. The taxonomy gives us a migration path without blocking dashboard cleanup on instrumentation completeness.

**Rollout Plan**

First, `person:krsnapaudel` creates the folder structure in `repo:alpen-dashboards` and moves existing dashboards without changing panel behavior. This should be a low-risk PR focused on layout.

Second, `person:prajwolrg`, `person:MdTeach`, and `person:delbonis` review `10 Protocol Liveness` and mark panels as keep, merge, rename, or delete. The target is fewer panels with clearer invariants.

Third, `person:Rajil1213` and `person:ProofOfKeags` define the bridge drilldown skeleton in `20 Bridge Safety`, including placeholder panels where metrics are not yet emitted consistently from `repo:strata-bridge`.

Fourth, `person:john-light` identifies the minimum devrel demo dashboard set. These dashboards move to `40 Devrel Demo` and get explicit descriptions saying they are not production health views.

Fifth, infra adds review rules: operator dashboard changes require `person:krsnapaudel` or infra approval; protocol dashboards require protocol approval; bridge dashboards require bridge approval.

Finally, we run one release cycle with the new taxonomy and collect gaps from actual use. After that, stale experimental dashboards are deleted, promoted panels get owners, and missing bridge/protocol metrics become repo-specific instrumentation issues rather than dashboard cleanup tasks.
