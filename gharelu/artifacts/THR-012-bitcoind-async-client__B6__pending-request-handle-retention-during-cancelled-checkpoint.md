## TL;DR

During B6 soak testing of `bitcoind-async-client`, we found that cancelled checkpoint polling calls could leave pending request handles retained in the async client’s in-flight request map. The issue did not corrupt Strata state or cause incorrect checkpoint decisions, but it caused steady memory growth in long-lived node processes when callers repeatedly timed out or dropped futures while bitcoind was slow, restarting, or temporarily unavailable.

The bug was introduced by the original request multiplexing path in `bitcoind-async-client` and became visible after Strata node checkpoint polling moved to shorter caller-side deadlines. Regtest unit mocks did not reproduce it because they completed or failed RPC calls deterministically and did not model bitcoind restart windows where the transport remained open while responses were delayed.

Fixed by making request cleanup cancellation-safe, adding a regression test for dropped callers, and extending soak coverage to include bitcoind restart loops.

## Impact

No mainnet funds, bridge funds, or production Strata checkpoints were affected. This was caught before release.

Observed impact in B6 soak jobs:

- Long-lived regtest Strata node RSS grew from ~410 MiB to ~1.9 GiB over 18 hours under repeated bitcoind restart and checkpoint polling timeout conditions.
- `bitcoind-async-client` retained 146,312 pending request entries in the worst soak run before the job was stopped.
- Checkpoint polling latency became noisier after ~9 hours because the retained map increased lock hold time around request dispatch and cleanup.
- CI capacity was affected: 4 soak jobs consumed shared runners for ~31 total runner-hours before we narrowed the reproduction.
- The initial hardening release target slipped by 3 working days because we chose to fix cancellation semantics before cutting the crate release.

## Timeline

All times UTC.

- 2025-09-22 09:10 - person:bewakes starts B6 long-lived regtest soak jobs against `strata` using the hardened `bitcoind-async-client` branch.
- 2025-09-22 14:35 - First 6-hour soak completes without correctness failures, but RSS slope is visible: ~410 MiB to ~780 MiB.
- 2025-09-23 08:20 - person:krsnapaudel asks for a longer run with bitcoind restart injection because the memory graph does not flatten after request volume stabilizes.
- 2025-09-23 18:50 - 12-hour restart-loop soak reaches ~1.4 GiB RSS. No failed checkpoint assertions.
- 2025-09-24 10:15 - person:bewakes adds pending-request gauge logging to the soak branch. Pending entries monotonically increase when checkpoint polling futures time out.
- 2025-09-24 13:40 - person:delbonis confirms the Strata caller path drops the RPC future after the checkpoint poll timeout instead of awaiting transport completion.
- 2025-09-25 09:05 - person:prajwolrg reviews the checkpoint polling path and confirms protocol state remains conservative: timeout means “no new checkpoint,” not acceptance of stale data.
- 2025-09-25 16:30 - person:MdTeach reproduces locally by pausing bitcoind RPC responses while issuing 1,000 short-deadline calls.
- 2025-09-26 11:00 - person:krsnapaudel identifies cleanup tied to response receive rather than caller drop. Cancelled calls no longer had a live receiver, but the request entry stayed in the client map until transport shutdown.
- 2025-09-27 15:25 - Fix branch opened as PR `bitcoind-async-client#42`, adding cancellation-safe pending request cleanup.
- 2025-09-30 12:10 - person:storopoli asks for a runtime-specific test because cancellation behavior differed between the soak harness and local reproduction.
- 2025-10-02 17:45 - Regression test added for dropped callers under Tokio timeout and explicit future drop.
- 2025-10-04 20:20 - 24-hour soak passes with pending requests returning to zero after restart windows. RSS stabilizes between ~430 MiB and ~510 MiB.
- 2025-10-07 10:30 - Fix merged in `bitcoind-async-client` commit `8f4c2d1` via PR `#42`.
- 2025-10-09 16:00 - Strata integration updated in `alpen` PR `#1187` to expose the pending-request gauge and align checkpoint polling timeout labels.
- 2025-10-24 13:00 - B6 audit notes closed with soak artifacts and dashboard screenshots linked from the release checklist.

## Root Cause

The async client maintained an internal pending-request map keyed by JSON-RPC request id. Dispatch inserted a response sender into the map, and the read loop removed it when a matching response arrived. That model assumed every request either received a response or the transport failed.

Checkpoint polling violated that assumption. The Strata caller wrapped polling in a timeout and dropped the future when bitcoind was slow or mid-restart. Dropping the caller also dropped the response receiver, but the client-side pending entry remained because cleanup only happened in the read loop. During bitcoind restart windows, responses could be delayed long enough for many caller futures to be cancelled while the underlying client task and transport stayed alive. The pending map therefore retained dead senders indefinitely.

Regtest mocks hid the bug because they returned immediate success or immediate error. They did not model a production-like sequence where bitcoind accepts a connection, stalls during restart or RPC warmup, and later resumes while upstream callers have already abandoned their futures.

## What Went Well

The issue was caught by soak testing before release. The failure mode was resource growth, not consensus or bridge correctness, and the checkpoint polling path already treated timeout conservatively.

The extra pending-request gauge made the problem obvious. Before that, we only had process RSS and allocator noise. Once person:bewakes added request-map cardinality logging, the link between caller cancellation and retained handles was direct.

Cross-team review helped. person:krsnapaudel had the infra context for the client internals, while person:prajwolrg and person:delbonis verified the protocol caller behavior and confirmed the safe state transition on timeout.

## What Went Poorly

We relied too much on deterministic regtest mocks for async lifecycle behavior. The mocks tested JSON-RPC values but not transport timing, bitcoind restart behavior, or cancellation pressure.

Ownership was split awkwardly. The crate was infra-owned, but the riskiest callers were protocol-owned. That made it easy for cancellation behavior to be treated as a caller detail in one place and a transport detail in another.

Observability landed late. We should have had pending request count, timeout count, and transport reconnect count before starting the long soak jobs. Without those, the first day of investigation was mostly narrowing memory symptoms.

The soak harness consumed shared CI capacity while we were also trying to clear core Strata milestone reviews.

## Action Items

- person:krsnapaudel: Make cancellation safety an explicit contract in `bitcoind-async-client` docs, including caller timeout behavior and cleanup guarantees.
- person:bewakes: Keep the bitcoind restart-loop soak as a scheduled pre-release job, capped at one shared runner, with artifacts retained for 14 days.
- person:MdTeach: Add a mock transport mode that can stall accepted requests and resume later, instead of only returning immediate success or error.
- person:delbonis: Audit Strata protocol RPC callers for dropped-future timeout patterns and list which ones rely on client-side cleanup.
- person:prajwolrg: Add release checklist criteria requiring pending request count to return to baseline after timeout and reconnect tests.
- person:storopoli: Review runtime-specific cancellation tests and ensure they cover both timeout cancellation and explicit future drop.
- person:alexhui01: Wire pending-request, timeout, and reconnect metrics into the Strata node dashboard before the next hardening release.
- person:krsnapaudel and person:bewakes: Document the B6 reproduction command, expected memory bounds, and known CI cost so future soak runs are comparable.
