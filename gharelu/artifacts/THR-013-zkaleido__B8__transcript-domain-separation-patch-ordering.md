## TL;DR

During the B8 audit window, person:cyphersnake found two ambiguous transcript labels used across `zkaleido` and the `g16` adapter. The labels were not immediately exploitable in the reviewed Glock/Strata proving paths, but they weakened our domain-separation guarantees and made future composition unsafe. We fixed the issue in `zkaleido` first, then updated `g16`, but the patch ordering was wrong for the active release branch: `g16` temporarily depended on a `zkaleido` transcript API that had not landed in the release branch yet.

Impact was limited to release delay and integration churn. No production proofs were affected. The Glock release branch was blocked for ~2.5 working days while we aligned `zkaleido`, `g16`, and downstream Strata integration commits.

## Impact

The immediate impact was a blocked release branch for Glock and Strata integration work from 2025-07-18 14:20 UTC through 2025-07-22 09:05 UTC. CI stayed red on the release branch because the `g16` adapter referenced renamed transcript domain labels before the matching `zkaleido` patch was available on that branch.

Concrete effects:

- 1 release branch blocked: `release/glock-strata-2025-07`
- 3 repos touched: `zkaleido`, `g16`, `alpen`
- 5 PRs involved:
  - `zkaleido#184`: introduce explicit transcript domain labels
  - `zkaleido#186`: backport transcript label constants to release branch
  - `g16#97`: update adapter challenge labels
  - `g16#99`: pin compatible `zkaleido` revision
  - `alpen#412`: bump proving backend dependency set
- ~2.5 working days of integration delay
- 0 known invalid proofs accepted
- 0 production deployments affected

The security impact was preventive. The ambiguous labels could have allowed two logically distinct Fiat-Shamir challenge points to share a label string in future protocol extensions. The reviewed path still included different surrounding transcript material, so this was not assessed as a live break. However, relying on surrounding material instead of explicit domain labels is not a property we want.

## Timeline

All timestamps UTC.

2025-07-14 09:10 - person:cyphersnake starts focused audit of transcript domain separation for `zkaleido` and `g16`.

2025-07-15 16:40 - person:cyphersnake identifies that `beta` and `gamma` challenge derivations are labeled generically in `zkaleido::transcript`, with adapter-side reuse in `g16`.

2025-07-16 10:25 - person:AaronFeickert, person:ceyhunsen, and person:mukeshdroid review the finding. We classify it as security-hardening, release-blocking for the next Glock/Strata integration, but not an emergency production patch.

2025-07-16 15:05 - person:cyphersnake opens `zkaleido#184`, replacing ambiguous labels with protocol-scoped labels: `g16.beta.v1`, `g16.gamma.v1`, and separating accumulator challenge labels.

2025-07-17 08:55 - person:AaronFeickert reviews `zkaleido#184`; person:ceyhunsen requests an API shape that avoids downstream string construction.

2025-07-17 13:30 - `zkaleido#184` merges to `main` at commit `8f4c2b1`.

2025-07-17 15:10 - person:cyphersnake opens `g16#97`, updating the adapter to call the new transcript label constants.

2025-07-18 09:45 - `g16#97` merges to `main` at commit `2ad91e7`.

2025-07-18 14:20 - person:prajwolrg reports that the Glock/Strata release branch fails to build because `g16` now expects `zkaleido::transcript::labels`, which is not present on `release/glock-strata-2025-07`.

2025-07-18 15:00 - We realize the fix landed in dependency order on `main`, but not in release-branch order. The release branch had picked up `g16#97` through a dependency bump before receiving a `zkaleido` backport.

2025-07-21 07:35 - person:Zk2u adds a temporary pin in the integration environment to restore reproducible builds while the backport is prepared.

2025-07-21 11:15 - person:cyphersnake opens `zkaleido#186`, backporting only the transcript label constants and label migration without unrelated `main` changes.

2025-07-21 16:50 - `zkaleido#186` merges to release branch at commit `51bb73a`.

2025-07-22 08:10 - person:storopoli opens `g16#99`, pinning `g16` to the compatible `zkaleido` release revision and adding a compatibility check in CI.

2025-07-22 09:05 - person:prajwolrg confirms `alpen#412` is green with the aligned dependency set.

## Root Cause

The root cause was not the audit finding itself. The audit worked as intended.

The root cause was that our cross-repo release process treated `zkaleido` and `g16` as independently mergeable units, while the transcript label change was a coupled API and security change. We merged in the correct conceptual order on `main`: first provider, then adapter. But the release branch consumed the adapter update before consuming the provider update.

Two conditions made this easy to miss:

First, the transcript API exposed raw or semi-raw label behavior to downstream adapters. That made a security fix look like a small naming cleanup, but it was actually a protocol boundary change.

Second, CI tested each repo against its default dependency configuration. We did not have a release-branch matrix that tested the exact Glock/Strata dependency set before dependency bumps were merged downstream.

## What Went Well

person:cyphersnake’s audit found the ambiguous labels before they became part of a deployed interface we would have had to support long term.

The review loop between person:AaronFeickert, person:ceyhunsen, and person:mukeshdroid kept the fix narrow. We avoided rewriting the transcript abstraction during a release window.

person:Zk2u stabilized integration quickly by pinning the previous compatible dependency set, which gave us room to prepare a clean backport instead of forcing a larger merge from `main`.

The final fix improved the API: downstream code now consumes named transcript label constants instead of constructing equivalent strings locally.

## What Went Poorly

We did not identify the change as cross-repo release-blocking at PR creation time. That meant `zkaleido#184` and `g16#97` were reviewed as normal repo-local changes.

The release branch accepted a downstream adapter bump without verifying that the matching provider commit was present. This is exactly the kind of dependency skew our current CI does not catch early enough.

Benchmark instability in nearby proving backend work also made reviewers cautious about touching more than necessary. That caution was reasonable, but it meant we deferred compatibility automation until after the branch had already broken.

The backport had to be prepared manually. We did not have a documented patch train for security-hardening changes spanning `zkaleido`, `g16`, and `alpen`.

## Action Items

- person:cyphersnake: Add a `SECURITY_BOUNDARY.md` note to `zkaleido` documenting transcript domain labels, label versioning, and when label changes require coordinated adapter updates. Due 2025-08-08.

- person:AaronFeickert: Add a release checklist item requiring cross-repo patch ordering for protocol-facing API changes. Due 2025-08-06.

- person:storopoli: Add CI in `g16` that tests against both `zkaleido main` and the active release branch pin. Due 2025-08-12.

- person:Zk2u: Add an integration dependency lock report for Glock/Strata release branches so dependency bumps show exact repo commits before merge. Due 2025-08-15.

- person:prajwolrg: Update `alpen` release branch policy so proving backend dependency bumps require green CI on the full pinned set, not only repo-local tests. Due 2025-08-13.

- person:ceyhunsen: Replace remaining adapter-side transcript string literals with typed constants or constructors in `g16`. Due 2025-08-20.

- person:mukeshdroid: Add two negative transcript tests showing that `beta`, `gamma`, and accumulator challenges cannot collide under shared labels. Due 2025-08-20.
