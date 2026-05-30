## Highlights

Prague testnet support is now in place behind a guarded testnet profile across `product:strata` and `product:strata-bridge`. The main point of the week was not just getting Prague parameters wired in, but making sure we can expose them without turning every downstream repo into a one-off configuration fork. `person:prajwolrg` drove the thread end to end and kept the protocol-side scope tight while the endpoint names and infra rollout window were still moving.

The guarded profile gives us a clean boundary: Prague-specific constants, service endpoints, bridge assumptions, and public testing defaults are isolated from the current stable testnet path. That matters because we still have several surfaces that consume the same network facts differently: `repo:alpen`, `repo:strata-bridge`, `repo:strata-p2p`, `repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet`. The work this week reduced configuration drift rather than spreading another set of copied values across the stack.

The biggest constraint was sequencing. Bridge validation depended on infra stability landing late in the window, and public docs plus faucet readiness trailed the core protocol changes. `person:krsnapaudel` handled the staged rollout under a narrower-than-planned window because infra maintenance compressed the available test period. Endpoint naming also changed twice, which delayed the external testing note until the final names were settled.

## What shipped

Prague support shipped behind the guarded testnet profile. The profile now gives us a controlled way to run Prague-specific nodes, P2P settings, and bridge paths without making it the default for unrelated testnet users. `person:MdTeach` helped tighten the protocol assumptions around the profile so the change stays explicit and reviewable instead of relying on ambient environment behavior.

The staged rollout path is now usable. `person:krsnapaudel` coordinated the infra side so we could bring up the pieces in order, verify health, and avoid publishing unstable endpoint names. This included the practical cleanup around endpoint naming after the second change, which was annoying but necessary: external testers should see stable names once, not track our internal churn.

Bridge validation made it through the critical path. `person:Rajil1213` and `person:ProofOfKeags` worked through the bridge-facing pieces in `repo:strata-bridge`, including the places where validation depends on the final infra shape rather than just protocol constants. The result is not “bridge is done forever”; it is that the Prague profile now has enough validated bridge behavior to support controlled public testing.

The public testing note is drafted around final endpoint names. I held the external note until the endpoint names stopped changing, then aligned the language with the actual rollout state rather than overpromising. `person:john-light` owns getting that note out with the faucet and dashboard caveats clearly called out.

`repo:checkpoint-explorer`, `repo:alpen-dashboards`, and `repo:alpen-faucet` are not the pacing items for core protocol readiness, but they are pacing items for public tester experience. This week made that distinction explicit. We can run Prague internally; for public testers, faucet availability, dashboard labels, and explorer links need to be accurate enough that they do not create support load immediately.

## Coming up

Next step is to finish the public testing path, not just the protocol path. That means confirming final endpoint names in every public-facing place, checking faucet behavior against the Prague profile, and making sure dashboard and explorer references point at the right network.

We also need one more pass on cross-repo configuration drift. The guarded profile helps, but it does not automatically prove every consumer is reading the same source of truth. The remaining risk is silent mismatch: one repo using updated Prague parameters while another still points at an older endpoint or stale chain configuration.

For bridge work, the next useful milestone is repeatable validation under expected public-test conditions. We should test with the same endpoint names, faucet path, and docs that external testers will use. Otherwise we only prove that the internal setup works when operated by people who already know the system.

Public docs should stay deliberately narrow. The first Prague testing note should tell testers what is ready, what is guarded, and what failure reports we actually want. It should not imply production readiness or general-purpose network stability.

## Q&A summary

The main question was whether Prague support should be treated as generally available once the guarded profile landed. Answer: no. The profile is ready for controlled testnet exposure, but we are still gating public testing on endpoint stability, bridge validation confidence, and the faucet/docs path.

There was also discussion about why endpoint names were allowed to change this late. The short version is that infra maintenance compressed the rollout window and forced us to settle naming only after the staged deployment shape was clear. That cost us time, but publishing unstable names would have created worse downstream cleanup.

Another question was whether public docs should wait for faucet and dashboard polish. The answer is to publish only when the basic tester loop is coherent. We do not need polished dashboards to begin testing, but we do need testers to know where to connect, how to get funds, what bridge behavior is expected, and where to report failures.

On bridge readiness, the answer remains practical: Prague bridge validation is far enough along for controlled testing, but we should keep watching infra-dependent behavior closely. The bridge path is where late infra instability is most likely to show up as confusing user-facing failures.

## Shoutouts

Shoutout to `person:prajwolrg` for driving the thread across protocol, bridge, infra, and docs without letting the scope sprawl.

Shoutout to `person:krsnapaudel` for getting the staged rollout through a narrowed infra window and keeping endpoint naming grounded in the actual deployment.

Shoutout to `person:MdTeach` for tightening the protocol-side profile work and helping keep Prague behavior explicit.

Shoutout to `person:Rajil1213` and `person:ProofOfKeags` for pushing bridge validation forward despite depending on late infra stability.

Shoutout to `person:bewakes` for helping keep protocol review pressure on the right surfaces.

Shoutout to `person:john-light` for holding the external testing note until the final endpoint names were real, then turning the rollout state into tester-facing guidance.
