## TL;DR

During the narrow B7 audit of the shared Glock verifier v0, I found that the Starknet integration path and the reference verifier did not bind the same transcript label for one verifier challenge. The issue was introduced when we moved from the original “thin adapter” plan to a shared package design for `product:glock` across `repo:verifiable-garbling`, `repo:garbled-circuits`, and `repo:g16`.

The ambiguity did not produce a known exploit against deployed funds or production Strata state. It did mean the v0 shared verifier interface could admit two incompatible interpretations of the same Fiat-Shamir transcript. We reopened already-merged integration code, patched the label derivation, added cross-repo transcript vector tests, and accepted a non-blocking Starknet performance regression for follow-up.

Primary owners: person:AaronFeickert for audit and spec clarification, person:cyphersnake for shared verifier patch, person:storopoli for protocol integration review, person:Hakkush-07 for Starknet path validation, and person:Zk2u for CI/vector reproducibility.

## Impact

No production incident occurred. The affected code was pre-release verifier work for the Starknet shared Glock verifier collaboration.

Concrete impact:

- Affected surface: shared Glock verifier v0 transcript construction in the Starknet path.
- Affected repos: `repo:verifiable-garbling`, `repo:garbled-circuits`, `repo:g16`, and downstream references in `repo:zkaleido`.
- Affected products: `product:glock`, with possible downstream integration risk for `product:strata`.
- User impact: none externally observable.
- Security impact: medium pre-release severity. The ambiguity could have caused verifier/prover disagreement or, worse, incorrect assumptions during future protocol composition.
- Schedule impact: 2 working days of churn during 2026-01-07 to 2026-01-09.
- Performance impact: Starknet verifier path remained approximately 6.8% slower in step count after the fix, accepted as non-blocking with a tracked follow-up.

The issue was caught before an externally advertised interface freeze, but after the shared package API had already been merged and circulated for collaborator review.

## Timeline

All times UTC.

- 2026-01-05 09:20: person:AaronFeickert starts the B7 narrow audit focused on transcript binding, Starknet integration, and shared verifier interface stability.
- 2026-01-05 14:40: person:Hakkush-07 confirms the Starknet adapter is using the shared package rather than the earlier thin-adapter branch.
- 2026-01-06 10:15: person:storopoli flags that reviewer bandwidth is split with Strata work and asks that audit findings be reduced to blocking vs follow-up items.
- 2026-01-06 17:30: Initial transcript comparison between the reference verifier and Starknet path shows matching challenges for the happy-path fixture set.
- 2026-01-07 11:05: I add a negative fixture with reordered public inputs and observe divergent challenge derivation between the reference path and Starknet path.
- 2026-01-07 12:10: person:cyphersnake reproduces the divergence locally against `repo:verifiable-garbling` commit `8f3c2a1`.
- 2026-01-07 13:25: We identify the ambiguity: the shared verifier label `glock.verify.eval` was used for both the garbling evaluation challenge and the Starknet adapter’s folded public-input challenge.
- 2026-01-07 15:00: person:Hakkush-07 confirms the ambiguity exists in the Starknet path but not in the older thin-adapter prototype.
- 2026-01-08 09:10: person:cyphersnake opens PR `verifiable-garbling#214`, splitting the labels into `glock.verify.eval` and `glock.verify.public_input_fold`.
- 2026-01-08 11:45: person:storopoli requests explicit transcript vector tests rather than relying on inline comments and convention.
- 2026-01-08 16:20: person:Zk2u adds CI generation for transcript vectors across the Rust reference path and Starknet fixture path in PR `garbled-circuits#97`.
- 2026-01-09 09:35: person:Hakkush-07 reports a 6.8% Starknet step-count regression from the extra domain separation and fixture plumbing.
- 2026-01-09 12:15: We agree the performance regression is non-blocking because interface stability matters more for the external collaboration window.
- 2026-01-09 17:50: Fixes merge. Follow-up performance issue filed as `zkaleido#142`.

## Root Cause

The root cause was an underspecified transcript-label contract during the migration from a thin adapter to a shared package.

The original thin-adapter design kept protocol notation, implementation labels, and Starknet adapter labels close together. That made the transcript flow less reusable, but harder to misread. When we replaced it with a shared package, we correctly optimized for reuse across verifier implementations, but we did not promote transcript labels into a reviewed compatibility boundary.

Specifically:

- The research notation described verifier challenges by protocol role, not by implementation label.
- The shared package exposed helper functions that accepted generic label strings.
- The Starknet path reused an existing label because it was locally descriptive enough.
- The reference verifier and Starknet adapter had fixture coverage for successful verification, but not for transcript-label uniqueness or public-input reordering.
- Review focused on API shape, proof object layout, and benchmark deltas. It did not treat transcript labels as consensus-like data.

No single reviewer had the complete failure mode in view. The design shift made interface stability more important than benchmark polish, but our checklist still reflected the earlier prototype phase.

## What Went Well

The issue was found before release and before the interface was treated as frozen by external collaborators.

The narrow audit scope helped. By focusing B7 on soundness and integration rather than broad cleanup, we got to the transcript boundary quickly. person:Hakkush-07 and person:cyphersnake reproduced the issue within roughly one hour of the initial report, which kept the discussion concrete.

person:storopoli pushed for vector tests instead of comments. That was the right bar. The resulting fixtures now make it much harder for future Starknet, Rust, or downstream verifier paths to silently drift.

We also made a good tradeoff by accepting the short-term 6.8% Starknet regression. Performance matters, but locking a clear transcript contract mattered more at this point in the collaboration.

## What Went Poorly

We let the shared package design outrun the written protocol integration contract. The move away from a thin adapter was reasonable, but we did not update the review checklist to match the new abstraction boundary.

The initial fixture set was too happy-path oriented. It proved that one prover/verifier pair agreed, not that independent implementations were binding the same semantic transcript.

The late finding reopened merged code and consumed reviewer bandwidth during Strata work. That was avoidable. Transcript labels should have been audited before merge, not after.

We also had too much implicit context in discussion threads. The distinction between “protocol challenge name” and “implementation transcript label” was understood by some people, but not encoded where reviewers and collaborators had to confront it.

## Action Items

- person:AaronFeickert: Update the Glock verifier transcript section in `repo:Technical-Whitepaper` with canonical labels and challenge ordering. Due 2026-01-16.
- person:cyphersnake: Land the final shared verifier label API so labels are typed constants, not caller-provided strings. Due 2026-01-20.
- person:Hakkush-07: Add Starknet verifier tests for reordered public inputs, duplicate labels, and malformed transcript domains. Due 2026-01-20.
- person:storopoli: Add transcript-label review items to the protocol integration checklist for Glock and Strata verifier changes. Due 2026-01-17.
- person:Zk2u: Keep cross-repo transcript vector generation in CI and make vector drift fail loudly. Due 2026-01-19.
- person:prajwolrg: Review the Strata-facing verifier interface for any similar caller-supplied transcript labels. Due 2026-01-23.
- person:john-light: Delay any external wording that implies the shared verifier interface is frozen until the typed-label API and vector CI are merged. Due 2026-01-15.
