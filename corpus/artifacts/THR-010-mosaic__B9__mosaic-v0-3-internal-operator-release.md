## Highlights

Mosaic v0.3 is now cut for internal Strata operators. This release is intentionally narrower than the original feature target, but it is much more useful operationally: manifests have documented compatibility rules, provider behavior is visible in dashboards, and the bridge/operator path has fewer ambiguous failure modes.

The main product decision was to prioritize hardening over incentive-market completeness. That means we deferred one planned provider-scoring mode and reduced the scoring surface to the parts we can explain, observe, and support today. I think that was the right trade. Mosaic is now closer to something Strata operators can run and reason about, instead of a broader protocol sketch with weak operational feedback.

The release also forced better cross-repo discipline across `repo:mosaic`, `repo:mosaic-torrent`, `repo:alpen`, `repo:strata-p2p`, and `repo:alpen-dashboards`. Small transport and manifest changes remain expensive, but this cycle made the coupling more explicit. We should treat that as a design input, not just release friction.

## What Shipped

We shipped Mosaic v0.3 for internal operator use with manifest compatibility documented as a first-class contract. The compatibility notes cover what operators can expect across v0.3 manifests, which fields are stable, and where future versions may introduce stricter validation or new provider metadata. This should reduce the “works on my branch” class of issues during Strata integration testing.

On the bridge side, person:Rajil1213 and person:ProofOfKeags kept the implementation moving steadily while review came in bursts. The bridge path now has cleaner behavior around provider selection, retrieval attempts, and error reporting. The reduced scoring feature set is in place: we kept the modes that are directly useful for internal operators and deferred the one that needed more research validation before we could responsibly put it into the release.

person:krsnapaudel landed the dashboard panels in `repo:alpen-dashboards`. These came in after release-candidate testing had already started, so the integration window was tighter than ideal, but the result is important: operators can now see enough Mosaic behavior to debug basic availability and provider issues without reading logs across multiple services. The panels are not the final observability story, but they close a real gap.

person:Zk2u helped tighten the infra/research boundary around what the release can claim. We now have a clearer split between “observable and supported in v0.3” and “research-backed but not yet operator-ready.” That helped keep the release honest.

person:mukeshdroid and person:cyphersnake contributed review and research pressure on provider behavior and scoring assumptions. Some of that feedback did not become release code, but it shaped the deferrals and made the compatibility notes more precise.

## Coming Up

The next tranche of work is less about adding knobs and more about making the current system boring under load.

First, we need to finish the deferred provider-scoring mode only after we can specify the threat model, operator-visible behavior, and expected dashboard signals. If the scoring mode cannot be observed or explained during an incident, it should not ship as an operator-facing feature.

Second, we need to reduce cross-repo integration cost. Mosaic transport changes currently ripple through too many places before we know whether they are semantically right. We should isolate the manifest and provider-selection contracts with better test fixtures across `repo:mosaic`, `repo:mosaic-torrent`, and `repo:strata-p2p`.

Third, observability needs to move earlier in the release process. Dashboard work landing after RC testing begins is workable once, but it should not become the pattern. For the next release, the operator panels should be part of the release-candidate definition.

Finally, we should keep internal Strata operators close to the feedback loop. v0.3 is explicitly an internal operator release, so success means we learn quickly where the operational model is still unclear.

## Q&A Summary

The main question was why we shipped with a reduced provider-scoring feature set. The answer is that reliability and operator clarity were higher priority for this release. The deferred scoring mode still matters, but it was not ready to carry production-like assumptions.

There was also discussion about whether Mosaic is becoming too observability-driven before feature completeness. My answer is that for data availability, observability is part of the feature. If an operator cannot tell whether data is available, retrievable, and served by the expected providers, the protocol behavior is not yet usable.

Another question was whether manifest compatibility should be treated as a stable external API. For v0.3, it is stable for internal operator expectations, not a public long-term commitment. We should avoid accidental permanence, but we also cannot leave operators guessing.

## Shoutouts

person:Rajil1213 for steady bridge implementation work through shifting review input.

person:ProofOfKeags for keeping the bridge/operator path practical and focused.

person:krsnapaudel for getting the dashboard panels landed under a tight release window.

person:Zk2u for helping separate research intent from operator-supportable release claims.

person:mukeshdroid and person:cyphersnake for pressure-testing the provider-scoring assumptions.

person:uncomputable for driving the release scope toward a smaller but more reliable Mosaic v0.3.
