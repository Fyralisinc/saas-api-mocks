## Goal

Create a repo-backed bridge risk register for strategic diligence that answers investor technical questions with source-linked, reviewable claims instead of slide-only assertions.

The immediate target is the strategic round diligence window, but the more important goal is to make `strata-bridge` risk ownership legible across bridge, protocol, and infra. I want a document and supporting repo artifacts that let person:pramodkandel and person:john-light answer common diligence questions without re-litigating protocol details in every call, while still making clear where confidence is conditional on pending protocol or release work.

The register should cover the main Strata bridge risk surfaces:

- BTC deposit recognition and reorg handling
- bridge finality assumptions relative to Strata block finality
- operator / sequencer / federation assumptions, where applicable
- withdrawal path liveness and censorship risks
- proof generation and verification dependencies through `glock` / `zkaleido`
- emergency pause, recovery, and upgrade controls
- monitoring, alerting, and operational runbooks
- known open questions that should not be softened into claims

This artifact should become canonical diligence material for `product:strata-bridge`, with source links into `repo:strata-bridge`, `repo:alpen`, `repo:Technical-Whitepaper`, `repo:zkaleido`, and `repo:bitcoin-bosd` where possible.

## Non-goals

This is not a bridge redesign. We should not use the risk register to introduce new trust assumptions, new withdrawal mechanics, or new proving requirements.

This is also not investor-facing marketing copy. The register can feed external diligence answers, but its internal version should preserve caveats and unresolved risks. If a claim depends on protocol finality language that person:prajwolrg, person:MdTeach, or person:delbonis has not signed off on, the register should say that directly.

We are not trying to prove that every risk is solved. The useful output is a structured, versioned map of what is known, what mitigates it, who owns the next answer, and where the claim is implemented or specified.

We should not block the release train on perfect documentation. Small PRs that clarify concrete behavior in `strata-bridge` are preferred over large speculative rewrites.

## Background

During early strategic calls, bridge questions clustered around a few repeated themes: “What exactly secures deposits?”, “What happens under Bitcoin reorgs?”, “Who can stop withdrawals?”, “What is proven in ZK versus enforced socially?”, and “Which assumptions are inherited from Strata finality versus specific to the bridge?”

The problem was not only that some answers were incomplete. It was that our answers lived in different places: slide notes from person:pramodkandel, whitepaper language, scattered repo comments, and verbal context from person:prajwolrg and person:MdTeach. This made the diligence process brittle. Each new investor call increased pressure to simplify the story before the protocol wording was ready.

For `product:strata`, finality language is already subtle: Bitcoin L1 gives probabilistic settlement; Strata introduces its own execution and proof pipeline; bridge accounting has to decide when BTC deposits become usable and when withdrawals are considered irreversible from the system’s perspective. If the bridge doc says “final” without specifying whether that means Bitcoin confirmation depth, Strata state commitment, proof acceptance, or operational policy, we create avoidable diligence debt.

For `product:strata-bridge`, the highest-risk communication gap is the boundary between protocol guarantees and operational controls. Some mitigations are cryptographic, some are economic, some are monitoring-based, and some are “we currently have an emergency control while the system matures.” These need different labels. Treating them uniformly makes us sound more confident but less precise.

The relevant implementation beat is B3: I am turning questions from early calls into a repo-backed risk register and landing small clarifying PRs in `strata-bridge`. person:ProofOfKeags is helping on bridge-specific implementation details. person:prajwolrg is the main protocol reviewer for finality wording. person:krsnapaudel should review infra and monitoring claims once the first pass exists.

## Proposed Design

Create `docs/risk-register.md` in `repo:strata-bridge` as the canonical internal bridge risk register. The document should be structured as a table plus short explanatory sections, not prose-only. Each risk entry should have stable IDs so we can reference them from PRs, diligence notes, and follow-up issues.

Each entry should use this schema:

- `id`: stable identifier, e.g. `BR-001`
- `area`: deposit, withdrawal, proof, finality, ops, upgrade, monitoring
- `risk`: concise statement of the failure mode
- `current behavior`: what the implementation or spec currently does
- `mitigation`: cryptographic, protocol, operational, or documentation mitigation
- `residual risk`: what remains true even after mitigation
- `source`: repo links, spec sections, PRs, or issues
- `owner`: one directly responsible person
- `status`: open, mitigated, accepted, needs-review, blocked
- `external wording`: approved short phrasing for diligence collateral, if available

Initial entries should include at least the following.

`BR-001: Bitcoin reorg after deposit recognition`
The register should state the configured confirmation depth, how deposit indexing handles reorgs, and whether credited bridge balances can be invalidated before Strata finalization. If the code does not yet make this obvious, I will land a clarifying PR in `strata-bridge` with comments and tests around the reorg boundary. The owner is person:Rajil1213, with protocol wording review from person:prajwolrg.

`BR-002: Ambiguous finality language`
This captures the current tension that bridge answers depend on protocol finality wording. The mitigation is not code; it is aligned terminology across `repo:Technical-Whitepaper`, `repo:alpen`, and `repo:strata-bridge`. person:prajwolrg and person:MdTeach should sign off before any external claim says “final” without qualification.

`BR-003: Withdrawal liveness under operator failure`
The register should separate user safety from user liveness. If funds cannot be stolen but withdrawals can be delayed under operator outage, that is a liveness risk, not a custody break. The entry should link to withdrawal path code and any runbook owned by infra / ops. person:krsnapaudel and person:sapinb should review operational assumptions.

`BR-004: Proof pipeline dependency`
Bridge security claims that depend on `product:glock` or `repo:zkaleido` should explicitly say what is proven, what is verified on-chain or by Strata nodes, and what remains outside the circuit. person:AaronFeickert should review the statement for cryptographic accuracy; person:delbonis should review integration wording.

`BR-005: Emergency controls and upgrade authority`
If the current bridge has pause, admin, signer rotation, or upgrade controls, the register should document who can trigger them, under what policy, and what monitoring creates accountability. This is a diligence-sensitive area where vague wording is worse than a clearly bounded trust assumption.

`BR-006: Monitoring gaps`
This entry should list what infra can currently detect: Bitcoin reorgs beyond threshold, deposit indexer lag, proof generation lag, withdrawal queue growth, bridge balance mismatch, and RPC divergence. Anything not yet monitored should be marked open rather than implied.

Small clarifying PRs should accompany the register where the code is harder to understand than necessary. These should be narrow: naming constants, adding comments around confirmation thresholds, improving test names, linking code to doc IDs, and adding assertions for existing behavior. We should avoid broad refactors during release work.

For diligence collateral, person:pramodkandel and person:john-light can maintain an external-answer companion document, but each answer should cite a risk ID. If a risk has `status: open` or `needs-review`, the external wording should remain conservative.

## Trade-offs

The main trade-off is speed versus precision. A risk register will slow down some investor-facing answers because it prevents us from smoothing over unresolved protocol details. That is acceptable. The current failure mode is worse: a clean narrative that senior engineers later have to unwind.

A repo-backed document also creates review load for people already committed to release work. To reduce that load, I will keep the first pass focused on the bridge questions already asked in diligence, not an exhaustive threat model. Reviewers should be asked for targeted sign-off: person:prajwolrg on finality wording, person:MdTeach on protocol consistency, person:krsnapaudel on infra claims, person:AaronFeickert on proof/security wording, and person:ProofOfKeags on bridge implementation details.

There is a risk that investors read internal caveats as weakness if copied directly. That is why internal risk entries and external wording are separate fields. We can be precise internally while still giving concise diligence answers externally.

There is also a documentation drift risk. The mitigation is to make risk IDs part of PR discipline. If a bridge PR changes deposit recognition, withdrawal handling, proof verification, or admin controls, it should update the relevant risk entry or add a new one.

## Rollout Plan

Week 1 of B3: person:Rajil1213 creates the initial `docs/risk-register.md` in `repo:strata-bridge` with the six seed risks above. Each entry should include provisional source links or explicit `source: missing` markers. person:ProofOfKeags reviews bridge implementation accuracy before wider circulation.

Week 1: land small clarifying PRs for any bridge behavior that is already implemented but hard to verify from code. Priority is deposit confirmation / reorg behavior, withdrawal state transitions, and config names that appear in diligence answers.

Week 2: route targeted review. person:prajwolrg reviews `BR-002` and any finality-dependent wording. person:MdTeach reviews consistency with Strata protocol language. person:delbonis reviews proof integration wording. person:krsnapaudel reviews monitoring and infra claims. person:AaronFeickert reviews cryptographic claims that touch `glock`, `zkaleido`, or proof soundness assumptions.

Week 2: person:pramodkandel and person:john-light convert approved `external wording` fields into diligence-ready answers. Anything still marked `open`, `needs-review`, or `blocked` should remain qualified in external collateral.

After B3: require risk-register updates for bridge PRs that alter trust assumptions, finality boundaries, admin controls, proof dependencies, or operational recovery. The register should be treated as canonical internal diligence material until superseded by a fuller bridge threat model.
