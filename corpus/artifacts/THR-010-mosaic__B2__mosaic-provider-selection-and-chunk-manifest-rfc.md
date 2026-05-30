# Mosaic Provider Selection and Chunk Manifest RFC

## Summary

This RFC proposes a concrete provider-selection and chunk-manifest format for Mosaic data availability, with the goal of making retrieval reliable under partial provider failure, adversarial seeding, and cross-repo integration constraints.

The core proposal is:

1. Every Mosaic object is represented by a signed chunk manifest.
2. The manifest commits to chunk boundaries, erasure-coding parameters, per-chunk hashes, provider advertisements, and retrieval policy hints.
3. Clients select providers using deterministic weighted sampling over provider commitments, local health scores, and anti-correlation rules.
4. Retrieval succeeds when the client obtains enough valid chunks to reconstruct the object, not when any single provider behaves honestly.
5. Observability is treated as part of the protocol surface: provider health, failed chunk proofs, sampling outcomes, and reconstruction paths must be exportable to `alpen-dashboards`.

This is scoped to `product:mosaic` and the Strata integration path. The affected repos are `repo:mosaic`, `repo:mosaic-torrent`, `repo:strata-p2p`, `repo:alpen`, and `repo:alpen-dashboards`.

The intended outcome is a DA layer that is boring to operate before we optimize the incentive market. That tradeoff is explicit. Reliability work has repeatedly displaced market design, but we should stop treating the two as fully separable. Provider selection creates the measurement substrate that later incentives will depend on.

## Motivation

Mosaic currently has too much implicit state in the transport layer. A bridge component can know that an object is “available” while the actual retrieval path depends on unstated assumptions about which providers are reachable, which chunks they have, and whether the seeding pattern has enough diversity.

That is not acceptable for Bitcoin/ZK protocol work. If Strata state transition data, withdrawal witness material, or bridge-related payloads depend on Mosaic availability, then “probably retrievable from somebody” is not a protocol condition. We need a manifest that makes availability claims precise enough for clients, operators, and later incentive mechanisms to reason about.

The immediate design pressure came from beat B2. I drafted the provider-selection model while person:Zk2u and person:mukeshdroid pushed on adversarial seeding and partial availability. The sampling target changed twice because the first version over-weighted advertised capacity and the second version under-modeled correlated failure. person:Rajil1213 correctly raised that bridge implementation planning was starting before the research comments had stabilized, which means this RFC needs to draw a firm line between what lands now and what remains future incentive-market work.

We also need to support internal operators. person:krsnapaudel and the infra side need clean metrics before feature completeness. If retrieval failures only show up as application-level timeouts in `repo:alpen`, we will not know whether the root cause is chunk absence, provider churn, peer routing, bad manifests, NAT behavior, or client-side selection.

The design should satisfy five properties:

1. A manifest is content-addressed and independently verifiable.
2. Provider choice is reproducible enough to debug but adaptive enough to avoid known-bad peers.
3. Clients can tolerate partial availability without making bridge code transport-aware.
4. Sampling resists obvious adversarial seeding patterns.
5. The implementation can cross repo boundaries without turning every small transport change into a protocol migration.

## Detailed design

### Object model

A Mosaic object is split into fixed-size source chunks and encoded into a larger set of recovery chunks.

A manifest commits to:

```text
object_id
version
payload_codec
source_length
chunk_size
source_chunk_count
recovery_chunk_count
reconstruction_threshold
chunk_hashes[]
erasure_scheme
provider_set[]
created_at_slot
expiry_hint
producer_signature
```

`object_id` is computed as a domain-separated hash of the canonical manifest body excluding provider-local volatile fields. The default hash should be the same digest primitive already used in the relevant Mosaic commitment path, rather than introducing a new one for convenience.

`chunk_hashes[]` commits to encoded chunks, not just source chunks. This lets clients verify every received chunk before attempting reconstruction and makes failed-provider evidence cheap to store.

`reconstruction_threshold` is the number of valid encoded chunks required to reconstruct the object. The initial target should be conservative: enough redundancy to tolerate provider churn and partial seeding, but not enough to make every bridge publish operation materially heavier. I propose starting with a 2x expansion and requiring any `k` of `2k` chunks, then tuning after measurement. This is intentionally a deployment constant, not a consensus rule.

### Provider advertisement

Each provider entry contains:

```text
provider_id
provider_pubkey
transport_addrs[]
chunk_claim_bitmap_commitment
availability_window
stake_or_reputation_ref
operator_group_hint
provider_signature
```

`chunk_claim_bitmap_commitment` commits to the set of encoded chunks the provider claims to serve. The full bitmap may be fetched separately. This avoids bloating the manifest while still allowing clients to prove that a provider advertised a chunk and failed to serve it.

`operator_group_hint` is not a trust primitive. It is an anti-correlation input. If three providers are all operated by the same internal cluster or cloud account, the selector should avoid counting them as independent availability. person:cyphersnake and person:Zk2u should review whether this field is too easy to spoof for adversarial settings. My current view is that spoofability is acceptable because it only helps honest operators declare correlation; it is not relied on to slash or reward.

### Provider selection

Clients select providers in rounds.

For each object, the client derives a sampling seed:

```text
selection_seed = H("mosaic/provider-selection/v1" || object_id || client_epoch)
```

The client computes a candidate score for every provider:

```text
score = H(selection_seed || provider_id) / weight
```

Lower scores are selected first. `weight` is derived from:

1. Manifest-declared chunk coverage.
2. Recent successful retrieval rate.
3. Latency bucket.
4. Penalties for timeout, invalid chunk response, or unavailable advertised chunks.
5. Anti-correlation adjustment across `operator_group_hint`, address prefixes, and observed peer topology.

The first implementation should keep the weight function simple and inspectable. I propose:

```text
weight = coverage_weight * health_weight * diversity_weight
```

Where:

- `coverage_weight` is proportional to the number of useful chunks the provider claims.
- `health_weight` is bounded between `0.25` and `2.0`.
- `diversity_weight` is reduced when already-selected providers appear correlated.

This is not meant to be Sybil-resistant by itself. It is a retrieval policy, not an incentive protocol. Later market design can bind weights to stake, fees, signed service-level objectives, or reputation. For now, we need a selector that does not accidentally pick five mirrors of the same unavailable node.

### Retrieval flow

The client fetches the manifest by `object_id`, verifies the producer signature, then requests provider bitmaps from the selected candidates.

The client builds a retrieval plan:

1. Determine the set of chunks needed for reconstruction.
2. Select providers until expected chunk coverage exceeds the threshold by a safety margin.
3. Issue parallel chunk requests with bounded concurrency.
4. Verify each chunk against `chunk_hashes[]`.
5. Penalize providers for timeout, invalid chunk, or contradiction against their bitmap.
6. Reconstruct once threshold is reached.
7. Emit retrieval metrics and failure evidence.

Bridge code in `repo:mosaic` and `repo:alpen` should not need to understand all transport details. person:Rajil1213 and person:ProofOfKeags should get a narrow API:

```text
publish_object(bytes) -> object_id
fetch_object(object_id, policy) -> bytes | availability_error
inspect_availability(object_id) -> availability_report
```

The `availability_error` type must distinguish:

- manifest not found
- manifest signature invalid
- insufficient provider coverage
- provider timeout budget exhausted
- invalid chunk received
- reconstruction failed
- local policy rejected provider set

This matters because bridge retry behavior should differ across these cases.

### Manifest propagation

`repo:mosaic-torrent` should treat manifests as first-class objects, not sidecar metadata. A provider that seeds chunks without serving the manifest is not useful to a fresh client.

`repo:strata-p2p` integration should avoid embedding Mosaic-specific selection logic. It should provide peer discovery and transport primitives. The selector should live in Mosaic, with only stable request/response types crossing the boundary.

For compatibility, manifest `version = 1` should be strict. Unknown required fields fail validation. Unknown optional fields are preserved when relayed but ignored by older clients.

### Observability

Every retrieval attempt should produce structured events:

```text
object_id
manifest_version
selected_provider_ids[]
provider_scores[]
requested_chunks[]
received_chunks[]
invalid_chunks[]
timeouts[]
reconstruction_success
latency_ms
bytes_transferred
error_kind
```

`repo:alpen-dashboards` should expose:

- object availability by age
- provider success rate
- invalid chunk rate
- chunk coverage distribution
- reconstruction failure rate
- selection diversity score
- bridge-facing fetch error breakdown

person:krsnapaudel should own the dashboard shape with input from person:sapinb on operator needs. The important constraint is that we should be able to answer: “Was this object unavailable, or did our client choose badly?”

## Drawbacks

The manifest becomes a protocol object with real compatibility cost. Once bridge code and Strata integration depend on it, changing fields is expensive.

Provider scoring introduces local policy variance. Two clients may choose different providers and see different outcomes. That is acceptable for retrieval, but it complicates debugging unless we log the selection seed, weights, and candidate set.

The anti-correlation fields are heuristic. They improve honest operation and measurement, but they do not solve Sybil resistance. We should not oversell this as adversarially complete.

The 2x erasure expansion increases storage and bandwidth. This is the cost of making partial availability non-fatal. We can tune it down later if measured provider reliability is high enough.

This also delays incentive-market work. I think that is the correct sequencing, but it should be acknowledged. A market built on weak availability measurements will reward the wrong behavior.

## Alternatives considered

### Single-provider pinning

We could assign each object to one primary provider and rely on retries or operator discipline. This is simpler, but it creates brittle bridge dependencies and makes adversarial withholding too easy. It also gives us poor data for later incentives.

### Fully random provider selection

Uniform random selection avoids complex scoring, but it ignores known-bad providers and correlated infrastructure. person:mukeshdroid’s partial availability cases showed that random choice can look fine in aggregate while failing specific objects repeatedly.

### Stake-weighted selection now

We could make provider selection depend on stake or paid commitments immediately. I do not think we are ready. The mechanism design is not settled, and person:uncomputable, person:Zk2u, and person:mukeshdroid are still working through sampling assumptions. We should leave `stake_or_reputation_ref` as a forward-compatible hook.

### Put selection into `strata-p2p`

This would centralize network behavior, but it leaks Mosaic availability semantics into a lower-level transport repo. Small selector changes would then become cross-repo transport changes. Given the existing integration cost, that is the wrong boundary.

### No manifest, only DHT-style discovery

A DHT-only approach makes discovery dynamic but weakens verifiability. Clients still need to know which chunks exist, how to verify them, and how many are required. The manifest is the compact object that binds these facts.

## Open questions

1. What is the initial erasure coding parameter set for testnet: 2x expansion with `k-of-2k`, or a more storage-efficient target?
2. Should provider bitmap commitments use a Merkle tree, KZG commitment, or plain hash over a compressed bitmap for v1?
3. Do we require provider signatures over chunk claims at publish time, or can claims be attached asynchronously?
4. How much local health history should affect selection before it becomes hard to reproduce failures?
5. Should `operator_group_hint` be included in signed provider metadata, or treated as unsigned operator configuration?
6. What is the minimum dashboard surface person:krsnapaudel needs before bridge integration can treat Mosaic as operationally usable?
7. Who owns invalid-chunk evidence format for later incentives: bridge research, Mosaic protocol, or infra?
8. Can person:Rajil1213 and person:ProofOfKeags proceed against the narrow bridge API while person:Zk2u and person:mukeshdroid continue pressure-testing the adversarial sampling model?

My proposed next step is to land the v1 manifest schema and retrieval event schema first, then implement provider selection behind a feature flag in `repo:mosaic`. This gives bridge implementation a stable API while keeping the sampling function adjustable during the rest of the B2 design window.
