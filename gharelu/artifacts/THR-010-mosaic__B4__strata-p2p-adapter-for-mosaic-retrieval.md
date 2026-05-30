# Strata P2P Adapter for Mosaic Retrieval

## Goal

Define a narrow `strata-p2p` adapter for Mosaic blob retrieval that gives Strata nodes a predictable way to fetch Mosaic data without embedding Mosaic’s full transport stack into node deployment.

The immediate target is retrieval hardening, not a new gossip protocol. A Strata node should be able to request a Mosaic object by commitment or content identifier, receive authenticated chunks from peers, verify them locally, and expose enough metrics for operators to see whether retrieval is healthy. This should support bridge flows owned by person:Rajil1213 and person:ProofOfKeags while keeping deployment and observability tractable for person:krsnapaudel.

## Non-goals

This design does not specify the long-term Mosaic incentive market. It should not block future pricing, reputation, or provider-selection work, but it will not solve those problems here.

This design does not replace `mosaic-torrent` as the reference bulk distribution path. The adapter is a Strata-facing retrieval interface over `strata-p2p`, scoped to the data needed for bridge and protocol verification.

This design does not make Strata consensus depend on best-effort P2P availability. If Mosaic data is unavailable, the node must fail closed at the protocol boundary where the data is needed, not infer validity from network behavior.

This design does not introduce unauthenticated blob acceptance, optimistic DA assumptions, or bridge-side trust in specific operators.

## Background

The earlier plan was to integrate Mosaic retrieval through a generic gossip path. That looked attractive because it reused existing `strata-p2p` primitives and gave us a single network surface. In practice, person:krsnapaudel showed that this made deployment shape worse: nodes would need to join a wider gossip domain, carry transport state unrelated to their role, and expose metrics that were difficult to attribute to Mosaic specifically.

The bridge implementation has been moving steadily, but research review has arrived in bursts. person:uncomputable, person:mukeshdroid, and person:Zk2u have raised valid questions around adversarial withholding, invalid chunk serving, and recovery behavior under partial peer failure. Meanwhile, person:Rajil1213 and person:ProofOfKeags need a stable integration contract so bridge code can stop depending on transport details.

The core constraint is that Mosaic availability is not only a networking problem. The data being retrieved is tied to Bitcoin-facing bridge state and Strata proof verification. A retrieval path that is easy to deploy but hard to verify is not acceptable. Likewise, a theoretically general data-availability network that operators cannot observe will fail before the protocol assumptions matter.

## Proposed Design

We add a `mosaic_retrieval` protocol adapter inside `strata-p2p`, with implementation boundaries mirrored in `mosaic` and `strata`:

- `strata-p2p`: peer discovery, request routing, timeouts, backoff, per-peer accounting, metrics.
- `mosaic`: object model, chunk layout, commitments, inclusion proofs, validation helpers.
- `strata`: caller-side policy for when a Mosaic object is required and what failure means.
- `alpen-dashboards`: operational visibility over retrieval success, latency, peer health, and validation failures.

The adapter exposes a request/response API rather than gossip:

```text
GetObject(object_id, expected_commitment, options) -> VerifiedObject | RetrievalError
GetChunk(object_id, chunk_index, expected_chunk_commitment) -> VerifiedChunk | RetrievalError
AnnounceInventory(object_id, commitment, size, chunk_count)
```

`object_id` is a stable Mosaic identifier derived from the DA commitment domain. It must not be a mutable URL, peer-local key, or transport-specific hash. `expected_commitment` comes from the protocol context: bridge state, Strata block metadata, or another authenticated source. The adapter never treats a peer announcement as authoritative.

Chunks are verified independently. Each chunk response includes:

```text
object_id
chunk_index
chunk_bytes
chunk_commitment
object_commitment_proof
provider_peer_id
```

The caller verifies the chunk commitment and its inclusion under the expected object commitment before accepting bytes into the object assembler. The object assembler only returns `VerifiedObject` after all required chunks are present and the final object commitment matches. Invalid chunks are attributed to the serving peer and counted separately from timeouts.

Peer selection is intentionally simple in the first version. The adapter maintains a local inventory table populated by `AnnounceInventory` messages and successful responses. For each object, it fans out chunk requests across up to `N` peers, with per-peer concurrency limits. Defaults should be conservative: small enough to avoid operator surprises, large enough that one slow peer does not stall retrieval. person:krsnapaudel should own the deployment defaults with review from person:Rajil1213 and person:ProofOfKeags.

We should distinguish four failure classes:

1. `NotFound`: no peer claims the object.
2. `Unavailable`: peers claim the object but do not serve enough valid chunks.
3. `InvalidData`: a peer served bytes that fail commitment verification.
4. `LocalPolicyRejected`: the object is valid but the Strata caller refuses it due to height, epoch, bridge context, or replay policy.

Only `InvalidData` should affect peer scoring immediately. `Unavailable` should affect short-term routing but not permanently mark peers malicious. `NotFound` is an availability signal for operators and potentially for future incentive-market work.

Metrics are part of the adapter contract, not an afterthought. At minimum:

```text
mosaic_retrieval_requests_total{result}
mosaic_retrieval_duration_seconds{result}
mosaic_retrieval_chunks_total{result}
mosaic_retrieval_peer_failures_total{peer,result}
mosaic_retrieval_inventory_entries
mosaic_retrieval_object_bytes_total
```

`alpen-dashboards` should show retrieval success rate, p50/p95 latency, invalid-data count by peer, and objects currently stuck in partial retrieval. This is the main reason to prefer the adapter over generic gossip: operators need to know whether Mosaic retrieval is failing, not merely whether P2P traffic exists.

Security review should focus on bounded resource use and validation ordering. We must verify cheap metadata before allocating large buffers, cap object and chunk sizes, avoid unbounded peer inventory growth, and make partial object state evictable. person:cyphersnake and person:mukeshdroid should review adversarial serving behavior. person:Zk2u should review whether the commitment and proof shape is compatible with the proving pipeline assumptions.

## Trade-offs

The adapter is less general than a generic gossip integration. That is intentional. We lose some flexibility around opportunistic propagation, but gain a smaller deployment surface and clearer ownership.

Request/response retrieval is more vulnerable to peer churn than full replication gossip. The mitigation is parallel chunk retrieval and explicit observability, not pretending the transport is a DA layer by itself.

Keeping validation in `mosaic` and routing in `strata-p2p` creates cross-repo integration cost. The alternative, duplicating commitment verification in Strata, is worse. We should pay the integration cost once and keep cryptographic validation close to the Mosaic object model.

This design also delays incentive-market work. That is a real cost. The current reliability failures are more immediate: without a boring retrieval path, we cannot measure which incentives would matter. The adapter should preserve enough failure telemetry for person:uncomputable to use later in market design.

## Rollout Plan

1. person:krsnapaudel defines the `strata-p2p` adapter trait and metrics names behind a feature flag. No bridge caller uses it yet.

2. person:Rajil1213 and person:ProofOfKeags wire a bridge retrieval call site to the trait using a mock provider, so the integration contract is exercised without network dependency.

3. person:uncomputable, person:mukeshdroid, and person:Zk2u review the object identifier, commitment proof, and validation API boundaries before we stabilize message formats.

4. Implement real peer inventory and chunk request/response in `strata-p2p`, with size caps, timeout defaults, and invalid-peer accounting.

5. Add dashboard panels in `alpen-dashboards` before enabling the adapter in shared environments. If operators cannot see partial retrieval and invalid-data failures, the rollout is not complete.

6. Run a controlled devnet with at least one withholding peer, one slow peer, and one invalid-data peer. Success means valid objects are retrieved, invalid chunks are rejected, and metrics identify the failure mode.

7. Enable the adapter for Mosaic retrieval in Strata test deployments, keeping `mosaic-torrent` as the fallback bulk path. After two stable weeks, revisit whether any retrieval telemetry should feed into the incentive-market design.
