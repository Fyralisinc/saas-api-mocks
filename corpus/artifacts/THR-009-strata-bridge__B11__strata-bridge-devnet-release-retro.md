## Wins

We got the bridge to a materially better place for devnet. The main win was not that `product:strata-bridge` reached the full original scope, because it did not. The win was that we hardened the bridge state machine enough to exercise real devnet flows against `product:strata`, expose contract assumptions, and turn a mostly paper design into something we could observe under infra conditions.

person:Rajil1213 drove the thread through a messy ownership surface that crossed bridge, protocol, infra, and research. person:ProofOfKeags helped keep the bridge implementation grounded in the operator/user path instead of only internal state transitions. person:prajwolrg and person:MdTeach were important on the protocol side because the bridge kept depending on Strata event semantics that were still moving. person:krsnapaudel added the infra feedback loop we needed: probes, release constraints, and devnet behavior caught issues that would not have shown up in local state machine tests.

The research review also paid off. person:uncomputable and person:AaronFeickert pushed us to make safety arguments explicit rather than burying them in implementation choices. That was uncomfortable late in the sprint, but it improved the final shape of the bridge work. We ended the cycle with better shared vocabulary around state transitions, event contracts, and the distinction between devnet coverage and production-ready bridge safety.

The devnet release shipped useful coverage. It gave us concrete traces for bridge state progression, failure handling, and event ingestion. It also forced the linked repos, especially `repo:strata-bridge`, `repo:bridge-sm-design-docs`, `repo:bitcoin-bosd`, `repo:bitcoind-async-client`, and `repo:alpen`, to line up around actual integration points instead of assumed interfaces.

## Challenges

The biggest challenge was ownership. Bridge work sat across bridge, protocol, infra, and research boundaries, but our planning treated it too much like a single-team implementation sprint. That created ambiguity around who could freeze event contracts, who owned state machine correctness, who owned operational readiness, and who had final say when safety documentation disagreed with code shape.

We also assumed stable Strata event contracts too early. The bridge state machine depended on protocol events being versioned enough to build against, but in practice the protocol surface was still evolving. That caused churn in implementation and made reviews harder because reviewers were sometimes evaluating bridge logic and protocol contract drift at the same time.

Design review started too late. By the time safety documentation became a focus, implementation had already encoded several assumptions. That made the audit review more expensive than it needed to be. Instead of documentation guiding the implementation, we had documentation catching up to it.

The release scope also compressed in a predictable way. We shipped useful devnet bridge coverage, but not the full original bridge scope. That was the right call by the end, but we should have named the scope reduction earlier. The team spent too long carrying the old target mentally while executing against a narrower reality.

## What we'd do differently

We would version the bridge state machine design, protocol event contracts, and infra probes before implementation sprints begin. Not perfectly, and not with heavyweight process, but enough that person:Rajil1213, person:prajwolrg, person:krsnapaudel, person:ProofOfKeags, and person:uncomputable are all working against the same contract.

We would make the bridge an explicit cross-functional project instead of letting it inherit ownership from whichever repo is hottest that week. Bridge correctness depends on protocol semantics, Bitcoin client behavior, infra observability, and safety review. The planning model should reflect that.

We would also separate “devnet bridge coverage” from “full bridge scope” in sprint language. Devnet coverage is valuable, but it should not pretend to be the whole bridge. Clearer naming would have reduced pressure and made the release criteria easier to defend.

## Action items

1. person:Rajil1213 will produce a versioned bridge state machine spec for the next implementation cycle, including transition invariants and failure states.

2. person:prajwolrg and person:MdTeach will define the Strata event contract version needed by the bridge before new bridge implementation work starts.

3. person:krsnapaudel will maintain a minimal infra probe checklist for bridge devnet releases, covering event ingestion, state progression, and failure visibility.

4. person:ProofOfKeags will map the bridge operator/user flows against the state machine so implementation coverage is tied to real bridge behavior.

5. person:uncomputable and person:AaronFeickert will review the safety argument before implementation freeze, not after code is already shaped.

6. Future bridge release plans must explicitly distinguish devnet coverage, audit-ready safety documentation, and full production bridge scope.
