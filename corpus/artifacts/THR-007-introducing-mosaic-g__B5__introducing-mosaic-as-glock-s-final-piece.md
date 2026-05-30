# RFC: Introducing Mosaic as Glock's Final Piece

## Summary

Mosaic should be announced as the final missing component that makes Glock usable as a practical proving path for Bitcoin/ZK protocol work, not as a standalone scalability product and not as a Strata launch commitment.

The proposed announcement structure separates three claims:

1. **What Mosaic proves today:** we can coordinate, distribute, and reconstruct large proving workloads across untrusted or semi-trusted workers using the Mosaic protocol and `mosaic-torrent` transport.
2. **Why this completes Glock:** Glock already gives us the proof system direction and circuit-level machinery; Mosaic supplies the missing distributed execution layer needed for realistic proof generation costs and latency.
3. **What Strata may consume later:** Strata can eventually use Mosaic-backed Glock proving as part of its rollup/proof pipeline, but the launch should not imply that Strata integration is complete, scheduled for a specific release, or already production-bound.

The announcement should be RFC-backed because the public narrative is currently ahead of the implementation surface. We need a document that gives john-light and pramodkandel a stable frame for the launch post while giving mukeshdroid, AaronFeickert, Hakkush-07, cyphersnake, delbonis, and krsnapaudel clear review anchors for correctness, demo reproducibility, and implementation status.

## Motivation

The current launch draft tries to do too much in one layer. It explains Mosaic, reintroduces Glock, gestures at Strata, motivates Bitcoin verification constraints, and implies a future proving architecture. That makes the narrative feel larger than what we can safely demonstrate during the announcement window.

The core problem is not that the claims are wrong. The problem is that they are mixed across time horizons.

Mosaic is valuable today because it turns a single-machine proving bottleneck into a distributed proving workflow. That is already meaningful for our research and engineering direction. For Bitcoin/ZK work, prover-side practicality matters even when verification remains constrained by Bitcoin script, covenant limitations, bridge assumptions, or client-side verification. A proof system that is theoretically suitable but operationally impossible to run is not a protocol component; it is an experiment.

Glock gives us the cryptographic substrate we want to talk about: efficient proof composition, circuit expressiveness, and a path toward proofs that can be consumed by higher-level Alpen systems. But Glock without a credible proving execution layer leaves an obvious question unanswered: who actually computes these proofs, at what cost, and with what coordination model?

Mosaic answers that question. It should be described as Glock's final piece because it completes the story from proof construction to proof production. That phrase is acceptable only if we define "final piece" narrowly: final piece for the current Glock proving architecture, not final piece for Strata, Alpen, or Bitcoin rollups in general.

The announcement also needs to preserve technical trust. Research terminology has been hard to translate cleanly. If we simplify too aggressively, we imply Mosaic is just a job queue or torrent wrapper. If we stay too close to the research vocabulary, the post becomes inaccessible and fails devrel goals. This RFC gives us a middle structure: state the protocol role precisely, then use launch copy to explain it.

## Detailed Design

The announcement should be organized around a three-layer model.

### 1. Glock: the proof system layer

The first section should briefly restate Glock as the proving stack Mosaic is completing. We should avoid re-litigating every proof-system detail from prior materials. Instead, the post should anchor readers on the practical problem:

- Glock defines the proof artifacts we care about.
- Glock circuits and proving runs are large enough that single-node proving is a poor default assumption.
- A useful Glock deployment needs a way to split work, distribute it, verify intermediate contributions, and reconstruct the final proof artifact.

AaronFeickert and mukeshdroid should review this section for terminology. The public copy should avoid implying new cryptographic claims beyond the existing Glock work. If we need to mention recursion, proof aggregation, polynomial commitments, or circuit partitioning, we should do so only where Mosaic actually depends on those concepts.

The preferred phrasing is: Mosaic completes the proving pipeline around Glock by making proof generation distributable. We should not say Mosaic makes Glock "decentralized" unless we define which part is decentralized and which trust assumptions remain.

### 2. Mosaic: the distributed proving layer

The second section should explain Mosaic as a protocol for decomposing and coordinating proving work.

The minimum technical claims we should support in the announcement are:

- A proving task can be represented as a set of work units with explicit dependencies.
- Workers can fetch assigned work and required data through the Mosaic transport path.
- The coordinator can track completion, reject malformed or inconsistent results where checks are available, and assemble the final output.
- `mosaic-torrent` is used for data movement where large artifacts make direct coordinator-mediated transfer inefficient.
- The implementation demonstrates the practical shape of distributed proving, even if production hardening remains in progress.

This section is where cyphersnake and Hakkush-07 should push on precision. If the protocol currently relies on an honest coordinator, authenticated worker set, bounded adversary model, or offline validation step, we should say that internally and avoid public wording that overstates adversarial robustness.

The announcement should use a diagram or visual artifact here. The current text-only draft forces the reader to mentally reconstruct the system. A simple visual should show:

1. A Glock proving task entering the Mosaic coordinator.
2. Work units being announced or assigned.
3. Workers fetching proving data through `mosaic-torrent`.
4. Partial results returning to the coordinator.
5. Final Glock proof artifact being reconstructed.

The visual should not include Strata in the main flow. If Strata appears, it should be in a separate "future consumer" box, visually downstream and explicitly out of launch scope.

### 3. Strata: the future consumer layer

The third section should be a scoped forward-looking section, not a product claim.

We can say:

- Strata will need practical proof generation for any architecture that relies on recurring validity proofs or compressed execution claims.
- Mosaic-backed Glock proving is one candidate path for satisfying that need.
- The current Mosaic announcement does not ship Strata integration.
- Integration work must still define proof interfaces, artifact formats, operational SLOs, failure handling, and verifier-side constraints.

This is where the earlier drafts were weakest. They used Strata examples to make Mosaic feel more concrete, but those examples were speculative enough that they create review risk. The public post can still explain why Strata motivates this work, but it should not present Strata as the thing being launched.

delbonis should review the boundary between Glock and Strata claims. krsnapaudel should review any operational statements about demo reproducibility, dashboards, or infra status. If we reference `alpen-dashboards`, the claim should be limited to observability for the demo or internal runs unless production monitoring is actually wired.

### Demo and reproducibility requirements

The announcement should not go out with only conceptual claims. We need a reproducible demo path that supports the central narrative.

For launch readiness, the demo should include:

- A fixed commit or tag for `mosaic`.
- A fixed commit or tag for `mosaic-torrent`.
- A documented proving workload small enough to run during review but large enough to show distribution is real.
- A worker count configuration that can be reproduced by devrel without direct protocol-engineer intervention.
- Logs or dashboard views showing work-unit distribution, transfer, completion, and reconstruction.
- A known list of failure modes that should not be demonstrated publicly.

krsnapaudel should own the infra checklist with support from pramodkandel for devrel reproducibility. john-light should not have to infer whether a screenshot represents a real run, a synthetic mock, or an aspirational architecture. Each artifact should be labeled internally before it is used in public material.

### Review flow

The RFC-backed announcement should use the following review order:

1. john-light produces the revised structure from this RFC.
2. AaronFeickert, mukeshdroid, and Hakkush-07 review cryptographic terminology.
3. cyphersnake reviews implementation accuracy for Mosaic and `mosaic-torrent`.
4. delbonis reviews the Glock/Strata boundary.
5. krsnapaudel reviews demo reproducibility and infra statements.
6. pramodkandel reviews the final version for external readability and launch assets.

The goal is not consensus on phrasing from everyone. The goal is to prevent category errors: presenting research as implementation, presenting demo infra as production infra, or presenting Strata integration as shipped.

## Drawbacks

This structure is less exciting than the original launch framing. "Mosaic completes Glock's proving pipeline" is a narrower claim than "Mosaic unlocks Strata" or "Mosaic brings decentralized proving to Bitcoin." The narrower claim may produce less immediate attention.

It also creates more upfront review work. A crisp announcement can usually be written by devrel and lightly checked by engineering. This one needs real review because the boundary between proof-system work, distributed systems work, and future Strata integration is easy to blur.

There is also a risk that saying "not Strata yet" weakens the perceived importance of Mosaic. I think that risk is smaller than the risk of overclaiming. If the post is technically grounded, readers who understand ZK systems will see why distributed proving matters.

## Alternatives Considered

One alternative is to announce Mosaic as an independent distributed proving network. I do not recommend this. It would force us to answer questions about permissioning, incentives, adversarial workers, slashing, market design, and production availability. Those are not the claims we are ready to defend.

Another alternative is to make the announcement primarily about Strata. That would give the post a stronger product hook, but it would misrepresent the current launch scope. Strata integration examples are still speculative, and using them as the main narrative would invite confusion later.

A third alternative is to publish a research note instead of a launch post. That would be safer technically, but it would underserve the goal of explaining why Mosaic matters now. We need something accessible enough for external protocol engineers while still precise enough that the research team is comfortable signing off.

The recommended path is a launch post backed by this RFC: public-facing, but scoped around what Mosaic actually demonstrates.

## Open Questions

Do we have a fixed demo workload that cyphersnake and krsnapaudel are comfortable treating as representative, or do we need to describe the demo as illustrative only?

Which Glock terms are mandatory for correctness, and which can be replaced with simpler language? AaronFeickert, mukeshdroid, and Hakkush-07 should decide this before final copy review.

Can we show real dashboard or log artifacts from `alpen-dashboards`, or should the visual layer be an architecture diagram only?

What exact sentence do we want for the Strata boundary? My proposed version is: "Mosaic is not Strata integration; it is proving infrastructure that Strata can consume once the integration interface is specified."

Do we want to call Mosaic "Glock's final piece" in the title, or keep that phrase in the body where we can define it more carefully? My preference is to keep it in the title only if the subtitle immediately scopes it to distributed proof generation.
