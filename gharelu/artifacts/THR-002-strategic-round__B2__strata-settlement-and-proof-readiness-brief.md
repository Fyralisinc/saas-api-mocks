**Goal**

Define the minimum technically correct settlement and proof-readiness story for `product:strata` that we can reuse across diligence, whitepaper updates, and release-facing protocol docs. The immediate goal is not to over-specify the final architecture, but to stop producing one-off explanations that mix current implementation, target design, and investor-facing optimism.

This brief should give person:pramodkandel and person:john-light a canonical internal source for how Strata execution commits to Bitcoin, what settlement means in the current system, what assumptions sit behind `product:glock`, and what proof-readiness claims we can defend during the strategic round window. It should also give person:MdTeach, person:AaronFeickert, person:Rajil1213, person:krsnapaudel, and person:delbonis a concrete review surface instead of forcing repeated synchronous explanations.

**Non-goals**

This document does not finalize the full Strata protocol spec, the bridge security model, or the complete `repo:Technical-Whitepaper` rewrite. It also does not claim production readiness for every proof path. In particular, it does not commit us to final verifier economics, final Bitcoin script covenant assumptions, or the complete withdrawal dispute flow for `product:strata-bridge`.

We should avoid using this as a fundraising narrative document directly. The output can inform external collateral, but the internal version should retain exact language around unimplemented paths, trusted components, latency variance, and missing measurements.

**Background**

The current diligence thread has exposed a recurring problem: “settlement” is being used to mean at least three different things.

First, there is Bitcoin data availability and ordering: Strata batches produce commitments that are eventually anchored to Bitcoin transactions. This gives us a Bitcoin-ordered history of state roots, but it is not by itself a full validity guarantee.

Second, there is execution validity: state transitions are correct if the execution trace is proven against the Strata state transition function and the proof verifies under the accepted proof system. Today, this is partially implemented and partially under active development across `repo:alpen`, `repo:zkaleido`, and `product:glock`.

Third, there is bridge finality: deposits and withdrawals depend on the bridge’s view of Strata state, Bitcoin confirmations, timeout windows, and fraud or validity proof availability depending on the path. `repo:strata-bridge` needs language that separates “Bitcoin has included the relevant transaction” from “the bridge can safely release funds.”

During B2, person:MdTeach and I drafted the execution and settlement brief, while person:AaronFeickert rewrote the ZK assumptions section to separate current implementation from target architecture. The main unresolved issues are inconsistent settlement terminology, missing proof latency numbers, and too much review load on person:AaronFeickert and person:delbonis while they are already committed to release work.

For diligence, the defensible position is: Strata is a Bitcoin-settled execution system where Bitcoin provides canonical ordering and long-term publication of commitments, while ZK proofs provide validity for state transitions once the proving path is complete and integrated. We should not imply that Bitcoin L1 directly verifies every Strata transition today unless the exact verifier path is implemented and benchmarked.

**Proposed Design**

We should standardize the settlement model around four named stages: `sequenced`, `anchored`, `proven`, and `bridge-final`.

`sequenced` means a Strata block or batch has been ordered by the Strata sequencing mechanism and is visible to nodes. This is the earliest execution-level status. It is useful for UX and mempool-like observability, but it has no Bitcoin settlement claim.

`anchored` means the batch commitment has been included in Bitcoin with a defined confirmation depth. The commitment should include enough data binding to identify the Strata chain context, batch height range, previous state root, post-state root, transaction data commitment, and proof reference if available. The exact encoding belongs in `repo:alpen`, but the diligence brief should state the invariant: Bitcoin anchors an ordered commitment chain, not arbitrary metadata.

`proven` means a validity proof has been generated for the relevant transition range and verified by the Strata verifier stack. For the current architecture, we should describe this as off-chain or protocol-level verification unless and until a Bitcoin-enforced verifier exists for the specific path. `product:glock` should be described as the proof aggregation and verification component under development, not a magic settlement layer. person:AaronFeickert should own the wording of cryptographic assumptions here, with review from person:MdTeach and person:delbonis.

`bridge-final` means the bridge can safely act on a state transition according to the bridge protocol. For deposits, this requires Bitcoin confirmation of the deposit transaction plus Strata recognition. For withdrawals, this requires a proven Strata state transition, satisfaction of the withdrawal delay or challenge window where applicable, and bridge operator or verifier acceptance depending on the path. person:Rajil1213 should own the mapping from these terms into `repo:strata-bridge`.

The proof-readiness section should separate three layers.

The first layer is circuit coverage. We need a table listing which state transition components are currently circuitized: transaction decoding, signature checks, state read/write constraints, fee accounting, block header transition, bridge message inclusion, and batch commitment consistency. Each row should be marked `implemented`, `in progress`, `stubbed`, or `target`.

The second layer is proving performance. We need benchmark ranges, not single-point claims. At minimum: trace size, batch size, proving hardware, prover wall-clock time, memory pressure, proof size, verification time, and aggregation overhead. Until person:krsnapaudel and person:MdTeach have reproducible numbers from CI or a pinned benchmark host, external claims should say “benchmarking in progress” rather than quote estimates from local machines.

The third layer is verifier integration. We need to say where proofs are checked today, where they are expected to be checked at release, and what would be required for stronger Bitcoin-native enforcement. This is where the whitepaper currently blurs implementation and target architecture. person:AaronFeickert’s rewrite should become the canonical source for assumptions: curve choices, Fiat-Shamir model, trusted setup status if any, recursion assumptions, hash function assumptions, and aggregation soundness.

For terminology, I propose the following hard rule: use “Bitcoin anchored” when referring to inclusion of Strata commitments in Bitcoin; use “validity proven” when referring to ZK proof verification; use “settled” only when the document explicitly defines which stage it means. In external-facing docs, “Bitcoin-settled” must be accompanied by one sentence explaining that Bitcoin orders and anchors commitments while ZK proofs establish execution validity.

**Trade-offs**

The main trade-off is precision versus narrative simplicity. Investors want a compact phrase like “Bitcoin-settled ZK rollup.” Internally, that phrase hides the difference between Bitcoin ordering, proof verification, and bridge safety. If we force every artifact to carry the four-stage model, the narrative becomes less punchy but much harder to misinterpret.

Another trade-off is acknowledging incomplete proof integration. Saying “proof-readiness” instead of “proof-complete” may feel weaker, but it keeps us aligned with the actual state of `repo:zkaleido` and `product:glock`. Overclaiming here creates downstream cost for person:john-light and person:pramodkandel because every diligence conversation then requires correction.

There is also a review-bandwidth trade-off. The most qualified reviewers are already overloaded. We should use structured review ownership: person:AaronFeickert for ZK assumptions, person:Rajil1213 for bridge semantics, person:krsnapaudel for benchmark reproducibility, person:delbonis for protocol consistency, and person:MdTeach for execution details. That avoids routing every sentence through the same two people.

**Rollout Plan**

First, person:MdTeach and I will convert this brief into a canonical markdown document in `repo:Technical-Whitepaper` with the four settlement stages and the proof-readiness table. The initial version should avoid benchmark numbers unless they are reproducible.

Second, person:AaronFeickert will replace the current ZK assumptions section with implementation-versus-target language. Any external claim about proof verification, recursion, or aggregation should link back to that section.

Third, person:Rajil1213 will review the bridge-final terminology against `repo:strata-bridge`, especially withdrawal safety and deposit recognition. If the bridge has different internal states, we should map them explicitly instead of forcing shared names into code.

Fourth, person:krsnapaudel will define a minimal benchmark harness for proof latency reporting. The output should include machine profile, commit hash, batch parameters, proving time, memory, proof size, and verification time. Until this exists, person:pramodkandel and person:john-light should not quote latency numbers externally.

Fifth, person:delbonis will do a protocol consistency pass across `repo:alpen`, `repo:bitcoin-bosd`, and the whitepaper language. The expected result is not a full spec freeze, but removal of contradictory settlement claims.

Finally, person:pramodkandel and person:john-light can derive the diligence-facing version from the canonical doc. The external version should preserve the staged model, use “Bitcoin anchored” and “validity proven” deliberately, and avoid implying that target verifier architecture is already live.
