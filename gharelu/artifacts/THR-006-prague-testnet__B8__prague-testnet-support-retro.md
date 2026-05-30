## Wins

We got Prague testnet support over the line across the core stack. The biggest win is that `product:strata` node support landed close to plan, despite the number of moving parts across `repo:alpen`, `repo:strata-p2p`, and the downstream public surfaces. person:prajwolrg drove the milestone through the final integration window, with person:MdTeach and person:bewakes helping keep the protocol-side changes moving instead of letting the work fragment into repo-local fixes.

Infra also stabilized enough for the rest of us to validate against something real. person:krsnapaudel carried a lot of the late coordination here, especially where testnet profile assumptions had to be made concrete. That unblocked bridge and explorer work that would otherwise have stayed theoretical.

On the bridge side, person:Rajil1213 and person:ProofOfKeags pushed through validation of `product:strata-bridge` against the live-ish Prague environment. This exposed issues we were unlikely to catch with isolated config review: mismatched chain/testnet parameters, assumptions about service availability, and places where bridge verification depended on infra state being stable longer than our test loops expected.

We also closed the loop on visibility. `repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet` were not as early as we wanted, but they did become part of the milestone rather than being treated as separate launch chores. person:john-light helped surface the public-facing gaps clearly enough that we could distinguish “core support exists” from “external users can actually use this testnet.”

## Challenges

The main challenge was cross-repo configuration drift. Prague parameters were represented in too many places, and ownership of the canonical testnet profile was unclear. `repo:alpen`, `repo:strata-bridge`, `repo:strata-p2p`, explorer indexing, dashboards, and faucet setup each had some local interpretation of “Prague-ready.” The deltas were often small, but small config mismatches are exactly what make testnet support feel flaky.

Bridge validation depended on late infra stability. That is partly inherent: bridge behavior has to be tested against real services and real indexing paths. But we spent too much of the buffer proving the environment rather than proving the bridge. When infra moved, bridge validation restarted. When explorer indexing lagged, we lost observability. This compressed the final week and made it harder for person:Rajil1213 and person:ProofOfKeags to separate bridge defects from environment defects.

Public polish trailed protocol readiness. We had working core support before we had a clean public checklist, faucet readiness, explorer confidence, and docs that matched the actual deployed profile. That meant the milestone was technically close but operationally noisy. The retro beat from 2026-02-26 to 2026-03-04 captured this accurately: bridge verification and explorer indexing consumed the buffer that had been intended for public polish.

## What we'd do differently

We should define one owner for the testnet profile before implementation starts. That does not mean one person owns every repo, but it does mean one source of truth exists for chain parameters, service endpoints, bootnodes, bridge settings, explorer assumptions, faucet limits, and dashboard labels. For this milestone, ownership emerged through pressure. Next time it should be explicit.

We should treat bridge validation as a first-class milestone dependency, not a final integration task. The bridge is not just a consumer of the testnet; it is one of the best tests of whether the testnet is coherent. person:Rajil1213 and person:ProofOfKeags needed stable infra earlier, and infra needed earlier signal from bridge validation.

We should put public-facing readiness on the same board as core support. Docs, faucet, explorer indexing, and dashboards are not marketing extras for a public testnet. They are part of whether the testnet can be used and debugged. person:john-light should be pulled in earlier with a concrete readiness checklist tied to the deployed profile, not asked to reconcile docs after parameters have already drifted.

## Action items

- Assign a single Prague-style testnet profile owner for each future testnet milestone. Default proposal: protocol driver owns the profile, infra owns deployment realization, bridge signs off on bridge-specific parameters.

- Add a shared config manifest consumed or checked by `repo:alpen`, `repo:strata-bridge`, `repo:strata-p2p`, `repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet`.

- Create a cross-repo config drift check in CI before the next public testnet cut.

- Move bridge validation earlier in the schedule, with person:Rajil1213 and person:ProofOfKeags validating against a stable staging profile before final infra hardening.

- Require explorer indexing, faucet readiness, dashboards, and public docs to be green before calling testnet support complete.

- Keep person:prajwolrg as the driver for final protocol signoff, but split release readiness into explicit protocol, infra, bridge, and public-surface checklists.
