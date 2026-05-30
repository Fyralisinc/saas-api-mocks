# Metrics Naming and Cardinality Rules for Strata and Bridge Services

## Summary

This RFC proposes a shared metrics naming and cardinality policy for `strata`, `strata-bridge`, `mosaic`, and the `alpen-dashboards` Grafana surfaces maintained by infra. The immediate trigger is the B3 observability work: person:krsnapaudel landed the first Rust-side exporters and Grafana JSON cleanup, while I threaded missing Strata block-processing counters through `alpen`. That work exposed a recurring problem: bridge and protocol services emit similar operational signals using different vocabulary, label shapes, and failure taxonomies.

The proposal is intentionally narrow:

1. Define a metric name schema for Rust services in Strata and bridge-adjacent components.
2. Define allowed label classes and explicit cardinality limits.
3. Standardize status, error, stage, and network labels.
4. Require dashboard JSON to consume stable metrics only, with local/staging parity checks before merge.
5. Establish review ownership across protocol, bridge, and infra for new metrics.

This does not attempt to redesign the telemetry stack, replace Prometheus/Grafana, or define tracing semantics. It only gives us enough shared structure that dashboards remain useful under load and do not become a second source of protocol vocabulary drift.

## Motivation

The dashboard cleanup started as a Grafana hygiene task, but it is now clearly cross-repo observability ownership. `strata`, `strata-bridge`, and `mosaic` each need operator-facing signals for block ingestion, batch construction, proof generation, bridge deposit/withdrawal handling, checkpoint submission, reorg handling, and RPC health. These systems fail in related ways, but today the metrics make those failures look unrelated.

Examples from the current implementation pattern:

- Protocol code may call a failed block-processing step `invalid_transition`, while bridge code reports a similar path as `deposit_sync_failed`.
- A counter may include a block hash, txid, peer id, or request path as a label because it was convenient during local debugging.
- Local dashboard JSON gets adjusted against a developer environment and later drifts from staging because the metric name or label set was never stabilized.
- Review of metric changes lands behind feature release queues, so dashboards lag the behavior they are supposed to explain.

The cost is not theoretical. During staging incidents, person:krsnapaudel and person:prajwolrg have to translate service-specific names before they can answer basic questions: are we stuck ingesting Bitcoin blocks, building Strata blocks, proving batches, or submitting bridge state? person:Rajil1213 and person:ProofOfKeags need bridge dashboards that map cleanly to protocol states without depending on tribal knowledge from protocol reviewers. person:john-light also needs externally explainable operational status eventually, and we cannot get there if the internal status language is inconsistent.

The B3 work made the next step obvious: before we add more exporters, we need a naming and cardinality contract.

## Detailed Design

### Metric Naming

All service metrics should follow this form:

```text
alpen_<component>_<domain>_<object>_<measurement>_<unit>
```

Where:

- `component` is one of `strata`, `bridge`, `mosaic`, `infra`.
- `domain` is the protocol or operational area: `bitcoin`, `block`, `batch`, `proof`, `checkpoint`, `deposit`, `withdrawal`, `rpc`, `p2p`, `db`, `mempool`.
- `object` is the thing being measured: `height`, `request`, `transition`, `queue`, `worker`, `submission`, `sync`.
- `measurement` is the measured property: `total`, `duration`, `current`, `last`, `lag`, `size`.
- `unit` is required for durations, byte sizes, heights, and counts where ambiguity is likely: `seconds`, `bytes`, `blocks`, `items`.

Examples:

```text
alpen_strata_block_processing_duration_seconds
alpen_strata_bitcoin_sync_lag_blocks
alpen_strata_proof_queue_size_items
alpen_bridge_deposit_events_total
alpen_bridge_withdrawal_submission_duration_seconds
alpen_mosaic_rpc_requests_total
```

Counter names must end in `_total`. Histograms must end in a unit, usually `_seconds` or `_bytes`. Gauges should use `_current`, `_last`, `lag`, or `size` to make their interpretation obvious.

We should avoid implementation-specific names unless the implementation is the operator-facing concept. For example, `alpen_strata_block_processing_duration_seconds` is better than `alpen_strata_executor_loop_duration_seconds`; however, `alpen_strata_db_compaction_duration_seconds` is acceptable because DB compaction is operationally meaningful.

### Component Boundaries

The `component` prefix should describe where the metric is emitted, not which team owns it.

- Metrics emitted by `strata` use `alpen_strata_*`.
- Metrics emitted by `strata-bridge` use `alpen_bridge_*`.
- Metrics emitted by `mosaic` use `alpen_mosaic_*`.
- Dashboard or exporter glue owned by infra uses `alpen_infra_*`.

This keeps Prometheus queries stable even when ownership changes. Bridge-owned code inside a protocol repo still emits according to runtime component.

### Label Policy

Labels are the main source of dashboard breakage and Prometheus pain. The default rule is: if a label value can grow with users, transactions, blocks, peers, requests, proofs, or time, it is not allowed.

Allowed low-cardinality labels:

```text
network
service
role
stage
status
reason
method
direction
source
```

Allowed values should be bounded and documented near the metric definition.

`network` must be one of:

```text
mainnet
testnet
signet
regtest
devnet
```

`status` must be one of:

```text
ok
error
timeout
canceled
skipped
```

`direction` must be one of:

```text
inbound
outbound
```

`stage` should represent stable protocol pipeline stages, not function names. For Strata block processing:

```text
bitcoin_ingest
block_validate
state_transition
batch_build
proof_request
checkpoint_submit
```

For bridge services:

```text
deposit_scan
deposit_verify
withdrawal_build
withdrawal_sign
withdrawal_submit
settlement_confirm
```

`reason` is permitted only when the enum is small and explicit. Acceptable examples:

```text
reorg
invalid_proof
rpc_unavailable
db_unavailable
insufficient_confirmations
fee_policy
```

Disallowed labels include:

```text
block_hash
txid
address
outpoint
deposit_id
withdrawal_id
peer_id
request_id
error_message
file_path
function
url
```

If we need those values, they belong in logs or traces, not Prometheus labels. We can attach an exemplar later if we want correlation, but that is outside this RFC.

### Cardinality Budget

Each metric should have a documented expected series count. The initial limits are:

- Normal metric: <= 50 active series per service instance.
- Pipeline metric with `stage`: <= 100 active series per service instance.
- RPC metric with `method`: <= 200 active series per service instance, only if method names are bounded.
- Anything above that requires explicit review from person:krsnapaudel and one repo owner from the emitting service.

This budget is per metric, not per dashboard panel. A metric that is harmless locally can still be unacceptable in staging if it expands across networks, services, roles, methods, and reasons.

### Rust Implementation Pattern

Each Rust service should define metric names and label value enums close to the subsystem that emits them, but the names must be centralized enough to review. I propose a `metrics.rs` module per service crate with:

- metric declaration
- label enum definitions
- doc comment including type, unit, and cardinality estimate
- registration function called at service startup

For example:

```rust
/// Histogram: duration of Strata block processing stages.
/// Labels: network, stage, status.
/// Expected series: networks(2) * stages(6) * statuses(3) = 36.
pub const STRATA_BLOCK_PROCESSING_DURATION_SECONDS: &str =
    "alpen_strata_block_processing_duration_seconds";
```

The code should not format metric names dynamically. Label values should come from enums or small conversion functions, not arbitrary strings passed from error paths.

person:MdTeach and person:prajwolrg should align the protocol-side block processing counters around this structure. person:delbonis should review whether the stage names match the protocol pipeline vocabulary we want operators to learn. person:Rajil1213 and person:ProofOfKeags should do the same for bridge deposit and withdrawal stages.

### Dashboard Consumption Rules

Dashboards in `alpen-dashboards` should only depend on metrics that have passed service review and are emitted in staging. Grafana JSON changes should include:

- the PromQL query
- the expected service/component
- whether the metric exists in local, staging, or both
- a screenshot or rendered panel check when practical

A dashboard panel should not paper over unstable names with broad regex unless the regex is intentionally part of the design. For example, querying `alpen_strata_.*_total` for a release-critical panel is not acceptable. It makes missing metrics look like empty states instead of broken observability.

Local dashboard changes should be checked against staging before merge when the panel is intended for operator use. This addresses the current drift where local dashboards work against a developer exporter but fail against staging service names.

### Review Rules

Any PR adding or renaming metrics in `alpen`, `strata-bridge`, or `mosaic` should request review from:

- one owner of the emitting service
- person:krsnapaudel for dashboard/infra impact if the metric is operator-facing
- bridge reviewer for bridge lifecycle metrics
- protocol reviewer for Strata state transition, proof, batch, or checkpoint metrics

This is not meant to expand every review queue. Small internal metrics that are not consumed by dashboards can merge with normal service review if they obey this RFC. But metrics used by staging dashboards should be treated as API surface.

## Drawbacks

This introduces process overhead. Engineers adding a quick counter now need to think about name shape, label bounds, and dashboard impact. During release pressure, that can feel slower than emitting whatever answers the immediate debugging question.

The schema may also be too rigid in places. Protocol concepts evolve, especially around proof, checkpoint, and bridge settlement flows. A stage name that looks stable today may be wrong after a design change from research or protocol review.

There is also a risk that we under-label metrics and push too much diagnosis into logs. The intent is not to make metrics vague. The intent is to keep metrics aggregatable, then use logs/traces for high-cardinality detail.

## Alternatives Considered

One alternative is to leave naming to each repo and fix dashboards with translation queries. This is the lowest-friction path short term, but it makes `alpen-dashboards` the place where protocol vocabulary gets reconciled. That is backwards: services should emit stable operational concepts, and dashboards should compose them.

Another option is adopting OpenTelemetry semantic conventions wholesale. I think that is premature for this slice of work. OTel is useful, especially for traces, but our immediate pain is Prometheus metric cardinality and inconsistent Bitcoin/ZK protocol naming. We can still keep names compatible with future OTel mapping.

A third option is to create a shared metrics crate across all repos. That may become useful, but it is too heavy for B3. Cross-repo dependency management would slow down the exporter work person:krsnapaudel already has in flight. Per-service `metrics.rs` modules plus review rules are enough for now.

## Open Questions

Should `mosaic` use `alpen_mosaic_*` for all emitted metrics, or should some bridge-facing Mosaic metrics use `alpen_bridge_*` when they describe bridge lifecycle state?

Do we want a formal registry file in `alpen-dashboards` listing all stable metrics, owners, and dashboard consumers? This would help person:krsnapaudel, but it may become stale unless CI checks it.

Which `reason` values should be shared between bridge and protocol? `reorg`, `rpc_unavailable`, and `db_unavailable` clearly apply to both. `invalid_proof` and `insufficient_confirmations` may need domain-specific interpretation.

Should release-critical dashboards block merges when staging lacks a metric, or should that remain a manual review expectation for now?

Can we add a lightweight CI check that rejects disallowed label names like `txid`, `block_hash`, `address`, and `error_message` in metric declarations?

Who owns final approval for vocabulary that crosses protocol and bridge boundaries: person:prajwolrg, person:Rajil1213, or a small review set including both?
