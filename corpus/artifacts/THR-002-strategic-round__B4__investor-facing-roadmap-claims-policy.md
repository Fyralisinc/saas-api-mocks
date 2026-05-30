# RFC: Investor-Facing Roadmap Claims Policy

## Summary

This RFC proposes a policy for how Alpen Labs states roadmap, timeline, readiness, and dependency claims in investor-facing materials. The immediate trigger is the Strategic Round Technical Diligence thread, especially the B4 review window from 2025-02-17 to 2025-03-02, where I consolidated the first diligence memo and we pulled back a planned “mainnet timeline” page after reviewers flagged that the external dates were firmer than our engineering confidence.

The policy is simple: investor-facing claims about `strata`, `strata-bridge`, `glock`, `bitcoin-dollar`, and related protocol milestones must be traceable to reviewed internal sources, must distinguish target dates from commitments, and must include confidence level and dependency state when the claim depends on unresolved protocol, bridge, proving, or infra work.

This is not intended to slow fundraising support. It is intended to prevent one-off collateral from becoming an accidental public commitment, and to reduce repeated review load on person:prajwolrg, person:MdTeach, person:Rajil1213, person:AaronFeickert, person:krsnapaudel, and person:delbonis during release-heavy periods.

## Motivation

During diligence, investor narrative repeatedly ran ahead of protocol confidence. This was not because anyone was trying to overstate the roadmap. It happened because fundraising artifacts need clarity, while protocol development is still negotiating uncertainty across multiple layers:

- Bitcoin-side constraints and covenant assumptions.
- Bridge safety model and operator/federation assumptions in `strata-bridge`.
- ZK proving cost, latency, recursion, and verifier integration work around `glock` and `zkaleido`.
- Sequencing and DA assumptions in `strata`.
- Operational readiness for releases, monitoring, incident response, and infra ownership.
- Product framing for `bitcoin-dollar`, where investor questions naturally push toward dates, launch shape, and market-facing commitments.

The B4 “mainnet timeline” page was a useful failure mode. Reviewers asked for precise dates. The page gave them what they wanted, but the dates implied a degree of certainty that engineering did not hold. person:prajwolrg and person:AaronFeickert correctly pushed back on protocol milestone firmness. person:Rajil1213 had bridge caveats that were not captured in the external-facing wording. person:krsnapaudel’s infra answers depended on rollout assumptions that were still in flux. I pulled the page because the risk was not a bad sentence; the risk was creating an externally reusable artifact that would outlive the context in which it was drafted.

We need a standard way to say what we know, what we are targeting, and what remains unresolved.

## Detailed Design

### 1. Scope

This policy applies to any document, memo, deck, data room note, FAQ, call follow-up, or diligence response that may be shared with investors, prospective strategic partners, or their technical reviewers.

Covered claim types:

- Launch dates, mainnet dates, testnet dates, audit dates, or release windows.
- Claims that a protocol component is “complete,” “ready,” “final,” “production-grade,” “audited,” or “validated.”
- Claims about bridge security, trust assumptions, peg safety, withdrawal guarantees, fraud recovery, or liveness.
- Claims about proving performance, verifier cost, recursion design, circuit stability, or Bitcoin verification feasibility.
- Claims that a repo or artifact is canonical, including `Technical-Whitepaper`, `alpen`, `strata-bridge`, `zkaleido`, and `bitcoin-bosd`.
- Claims about `bitcoin-dollar` launch sequencing or dependency on `strata` / `strata-bridge`.

Internal planning notes are out of scope unless they are copied into investor-facing collateral.

### 2. Claim Classes

Every investor-facing roadmap claim must fit one of four classes.

**Class A: Committed**

A committed claim is externally safe to repeat as a company position. It has an owner, reviewed source, and no known blocker that could materially change the statement.

Example:

> “The current bridge design separates deposit recognition from withdrawal finalization, and the withdrawal path remains subject to the security assumptions documented in the bridge spec.”

This kind of claim should cite or link the relevant internal source.

**Class B: Target**

A target claim describes an intended date or milestone but is not a commitment. It must use target language.

Allowed wording:

> “We are targeting a public testnet milestone in Q2, subject to bridge review and proving benchmark results.”

Disallowed wording:

> “Public testnet launches in Q2.”

If the target depends on review by person:prajwolrg, person:MdTeach, person:Rajil1213, person:AaronFeickert, or person:krsnapaudel, the dependency must be named in the internal review record, though not necessarily in the external sentence.

**Class C: Directional**

A directional claim explains the intended path without date precision.

Example:

> “Our sequencing plan is to harden the Strata node and bridge flows before presenting `bitcoin-dollar` as a user-facing product.”

Directional claims are preferred when protocol milestones are moving.

**Class D: Prohibited Until Resolved**

Some claims should not appear externally until explicitly upgraded.

Examples:

- “Mainnet by [specific date]” unless approved as Class A.
- “Trustless bridge” without a precise definition and reviewed caveats.
- “Bitcoin-native dollar launches after mainnet” if launch path depends on unresolved bridge or liquidity assumptions.
- “Verifier costs are solved” unless supported by current `glock` / `zkaleido` benchmarks.
- “Audit-ready” unless the audit scope, commit hash, and unresolved findings are known.

### 3. Required Metadata for Claims

Each investor-facing roadmap artifact must include an internal claim register. This can be a short table at the bottom of the draft or a linked issue. It does not need to be shared externally.

Required fields:

- Claim text.
- Claim class: A, B, C, or D.
- Product or repo: for example `strata`, `strata-bridge`, `glock`, `bitcoin-dollar`, `Technical-Whitepaper`.
- Owner.
- Reviewer.
- Source of truth.
- Expiration date or review-by date.
- Known dependencies.

Example internal row:

| Claim | Class | Area | Owner | Reviewer | Source | Expires |
| --- | --- | --- | --- | --- | --- | --- |
| “We are targeting a public testnet milestone in Q2, subject to bridge review and proving benchmark results.” | B | `strata`, `strata-bridge`, `glock` | person:john-light | person:prajwolrg, person:Rajil1213 | diligence memo + bridge review notes | 2025-03-15 |

Claims expire because technical confidence changes. A sentence that was safe on 2025-02-20 may be stale by 2025-03-10.

### 4. Review Routing

Review should be narrow and based on claim area.

- `strata` protocol milestones: person:prajwolrg, person:MdTeach, or person:delbonis.
- Bridge security, peg, withdrawal, and operator assumptions: person:Rajil1213, with person:AaronFeickert or person:uncomputable where the claim touches research assumptions.
- ZK proving, verifier, circuit, and benchmark claims: person:AaronFeickert, person:mukeshdroid, or person:Hakkush-07 depending on ownership.
- Infra, deployment, monitoring, and reliability claims: person:krsnapaudel or person:arminsabouri.
- Devrel wording and investor context: person:john-light or person:pramodkandel.

The default should be one accountable reviewer per claim area, not a blanket review request to every senior person. During release windows, person:pramodkandel and I should reduce reviewer load by translating investor questions into precise claim diffs rather than asking broad questions like “is this roadmap okay?”

### 5. Canonical Artifacts

Several fundraising artifacts should become canonical docs instead of one-off collateral. This policy defines three canonical surfaces:

1. **Protocol roadmap note**: internally maintained, externally excerptable, owned by protocol.
2. **Bridge assumptions note**: owned by bridge, with explicit safety and liveness caveats.
3. **Diligence FAQ**: owned by devrel, but claim-registered and sourced from the first two notes.

The `Technical-Whitepaper` should not be treated as the only source of truth for live roadmap claims. It is useful for architecture and thesis, but live readiness claims must point to current repo state, review notes, or release plans.

### 6. Standard Wording Rules

Use:

- “targeting” for non-committed dates.
- “currently expect” only with an expiration date.
- “depends on” when an unresolved dependency can change timing.
- “prototype,” “internal testnet,” “public testnet,” “release candidate,” and “mainnet” as distinct stages.

Avoid:

- “will launch” unless Class A.
- “done” unless tied to a commit, spec, or release.
- “trustless” without a formal threat model.
- “soon,” “near-term,” and “imminent.”
- Calendar precision beyond our real confidence.

For Bitcoin/ZK protocol work, precision without confidence is worse than ambiguity. A rough but caveated answer is more credible than a date we later walk back.

## Drawbacks

This policy adds process at the point where fundraising already has high context-switch cost. person:pramodkandel and I will need to maintain claim registers, and reviewers will need to say “Class B, not Class A” instead of just editing prose.

There is also a risk that investor materials become too caveated. Technical diligence needs confidence, not only uncertainty. The policy should not force us to understate progress. If `strata-bridge` has a reviewed design decision, or `glock` benchmarks support a claim, we should say so plainly.

Finally, claim expiration creates maintenance work. But stale collateral is already creating hidden work. The difference is that this policy makes the work visible before it becomes an external expectation.

## Alternatives Considered

**Keep review informal.**

This is what we did in B4. It works for isolated memos but breaks when the same content is reused across decks, calls, and data room notes. Informal review also routes too much work to senior reviewers at the last minute.

**Ban dates from investor materials.**

This would avoid overcommitment but is not realistic. Investors will ask about sequencing, runway, and launch windows. Refusing to give any timing makes us look less organized than we are.

**Centralize all external technical claims through protocol.**

This would maximize technical control but overload person:prajwolrg, person:MdTeach, and person:delbonis. It would also make bridge and infra claims less precise because the relevant ownership lives elsewhere.

**Create a full public roadmap.**

A public roadmap may be useful later, but it is premature while protocol milestones are still moving. The immediate need is a disciplined internal policy for investor-facing claims, not a public commitment surface.

## Open Questions

1. Who owns the first canonical protocol roadmap note: person:prajwolrg, person:MdTeach, or person:delbonis?

2. Should the bridge assumptions note live in `strata-bridge`, `Technical-Whitepaper`, or a separate internal diligence repo?

3. What confidence threshold upgrades a target mainnet window from Class B to Class A?

4. Do we want standard investor-facing language for `bitcoin-dollar`, or should every mention route through a separate product review until the protocol dependencies are firmer?

5. Should person:krsnapaudel maintain an infra readiness checklist that can be cited by the diligence FAQ?

6. How do we handle claims made verbally on investor calls? My proposal is that any new technical claim made live should be captured in the follow-up notes and classified before it is repeated.
