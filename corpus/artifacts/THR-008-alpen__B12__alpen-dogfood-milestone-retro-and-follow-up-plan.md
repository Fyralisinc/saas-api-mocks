## Wins

We got `alpen` from “internal node work in motion” to a dogfoodable Strata node surface with enough structure to split follow-up work cleanly. The biggest win was not a single feature, but forcing the node, checkpoint explorer, and bridge integration paths to meet in one milestone instead of evolving as separate assumptions.

person:prajwolrg drove the thread across the full arc and kept the milestone grounded in shipped behavior rather than aspirational protocol shape. person:MdTeach, person:delbonis, person:storopoli, and person:bewakes helped turn protocol internals into node-facing pieces that could survive integration pressure. person:Rajil1213 brought the bridge consumer view early enough that we caught output stability issues before they became downstream API contracts. person:krsnapaudel’s infra work mattered more late in the arc, when CI and repeatable dogfood runs finally became part of the definition of done rather than a cleanup task.

We also made progress on repo boundaries. Work across `alpen`, `strata-common`, `bitcoind-async-client`, and `checkpoint-explorer` exposed exactly where type ownership was unclear. That was uncomfortable, but useful. By the end, we had a better map of which types are protocol domain objects, which are node API surfaces, and which should be consumer-facing serialization contracts.

The checkpoint explorer path was a concrete forcing function. It needed stable outputs before protocol internals felt fully settled, which helped prevent us from treating the node as an implementation detail. Similarly, bridge integration forced us to account for real consumers instead of only local correctness.

## Challenges

The original milestone was over-scoped. We implicitly asked `alpen` to be both the node implementation and the integration surface for bridge, explorer, and dogfood workflows. That blurred priorities: sometimes we optimized for internal protocol ergonomics, sometimes for downstream stability, and sometimes for whatever was blocking the next manual run.

Cross-repo type ownership remained fragile throughout the year. We repeatedly hit cases where a type lived where it was convenient for the first implementation, not where it belonged long term. This created churn for downstream consumers and made reviews harder because a small node change could imply compatibility decisions in `strata-common` or explorer code.

CI reliability lagged feature development until late in the arc. That meant person:krsnapaudel and others had to spend too much time distinguishing actual regressions from flaky or under-specified automation. We paid for that with slower review cycles and lower confidence during integration-heavy weeks.

Dogfood runbooks also depended on too few people. person:prajwolrg, person:MdTeach, person:delbonis, person:storopoli, person:krsnapaudel, and person:Rajil1213 could move the system forward because they carried the working context, but that is not a scalable operating model. The runbooks were useful, but not yet durable enough for broader team rotation.

## What we'd do differently

We should have separated the milestone into three explicit tracks earlier: node reliability, checkpoint explorer outputs, and bridge integration. Those became the final follow-up buckets anyway; naming them earlier would have reduced priority ambiguity.

We should also have treated cross-repo type ownership as design work, not incidental cleanup. A short ownership matrix for `alpen`, `strata-common`, `bitcoind-async-client`, and `checkpoint-explorer` would have prevented several rounds of “this compiles locally but destabilizes a consumer” work.

For CI, we waited too long to make reliability a milestone blocker. Feature velocity looked higher in the short term, but the integration cost came due later. Next time, dogfood CI should be part of the first usable slice, even if it starts narrow.

Finally, we should have written consumer contracts before finalizing some internal shapes. Bridge and explorer did not need every protocol detail to be settled; they needed stable, documented outputs and clear compatibility expectations.

## Action items

1. person:prajwolrg to split remaining work into tracked follow-ups for node reliability, checkpoint explorer, and bridge integration.

2. person:MdTeach and person:delbonis to draft a cross-repo type ownership note covering `alpen`, `strata-common`, `bitcoind-async-client`, and `checkpoint-explorer`.

3. person:storopoli and person:bewakes to identify protocol types currently leaking into node or consumer-facing APIs without an explicit stability decision.

4. person:krsnapaudel to define the minimum dogfood CI gate and mark unreliable checks separately from product regressions.

5. person:Rajil1213 to document bridge output requirements that must remain stable across the next integration cycle.

6. person:prajwolrg to turn the current dogfood runbook into a rotation-ready document with prerequisites, failure modes, and escalation paths.
