## Goal

Make the Mosaic announcement demo reproducible from a fresh checkout, with a trace that DevRel can cite directly instead of describing the protocol flow abstractly. The immediate deliverable for B3 is a repo walkthrough spanning `repo:mosaic` and `repo:mosaic-torrent`, with enough deterministic output that person:john-light and person:pramodkandel can use it in announcement material without depending on a live engineer-driven demo.

The walkthrough should show the happy-path protocol shape:

1. initialize local services and fixture data;
2. construct Mosaic commitments for a small test dataset;
3. distribute/retrieve chunks through the torrent layer;
4. produce the Glock-backed proof artifact or proof-shaped placeholder, depending on the current prover mode;
5. verify the trace against the expected transcript;
6. explain where the same objects later connect into Strata, without claiming Strata launch integration.

Success means a reader can clone the repos, run one command or a small sequence of documented commands, and get stable artifacts: logs, transcript JSON, proof metadata, and verification result. The demo does not need to be production-grade, but it must be honest about what is implemented, what is mocked, and what is still roadmap.

## Non-goals

This does not ship Mosaic as a Strata-integrated production component. We should not imply that Strata nodes consume Mosaic proofs or torrent payloads in the launch path.

This does not redesign the Mosaic protocol, Glock proof system, or data availability assumptions. Any protocol concerns discovered while cleaning examples should be filed separately and routed to person:AaronFeickert, person:mukeshdroid, person:delbonis, and person:cyphersnake as appropriate.

This does not require full benchmarking. We can include rough local timings if already available, but the announcement demo should avoid performance claims that depend on machine profile, network shape, or unfinished prover optimization.

This does not make the torrent layer adversarially robust. The example can run local peers and fixture seeds. We should label that as a functional trace, not a realistic network simulation.

## Background

The announcement needs to move before all implementation branches converge. That creates a documentation problem: the public narrative wants one coherent protocol story, while the repos currently expose several partial flows across `repo:mosaic`, `repo:mosaic-torrent`, `repo:g16`, and `repo:Technical-Whitepaper`.

Mosaic is easiest to explain when readers can see the objects move. A static protocol description loses the distinction between data commitment, chunk transport, proof generation, and verification. It also invites overclaiming around Strata, because “Bitcoin/ZK data layer” sounds like a finished product boundary unless the trace is explicit.

The B3 beat exists to close that gap. person:Hakkush-07 and person:cyphersnake are cleaning examples so DevRel can point to runnable traces. person:delbonis is the protocol review path for claims that touch Mosaic/Glock semantics. person:krsnapaudel is the late infra support path for reproducible environment issues, especially around local services and dashboard artifacts. person:john-light is driving the announcement readiness thread and needs wording that is correct enough for research review but legible to outside developers.

Current pain points:

- example scripts assume local services are already running;
- fresh checkout behavior differs from warmed developer machines;
- README flow skips fixture generation and peer startup;
- review is split across research and protocol, so ambiguous terminology stalls late;
- Strata is nearby in the roadmap, but not in launch scope.

## Proposed design

The demo should be structured as a trace-first walkthrough, not a tutorial that builds concepts from scratch. The repo should expose a canonical command:

```bash
just demo-trace
```

If `just` is not already used, the equivalent can be `make demo-trace` or `./scripts/demo-trace.sh`, but there should be exactly one blessed entrypoint in the README. The entrypoint should execute the full local happy path and write outputs to a stable directory, for example `./target/demo-trace/<timestamp-or-latest>/`.

The trace output should include:

- `manifest.json`: repo version, command arguments, fixture IDs, service ports, and whether the prover ran in real or stub mode;
- `transcript.json`: ordered protocol events, including commitment construction, chunk publication, retrieval, proof generation, and verification;
- `proof.json` or `proof.meta.json`: Glock proof artifact if available, otherwise a clearly named placeholder with `mode: "stub"`;
- `verification.txt`: one-line human-readable result plus the verifier command;
- `logs/`: service logs for torrent peer startup, fetch, and verifier execution.

The transcript schema should be deliberately small. Each event should have `seq`, `phase`, `object_id`, `input_refs`, `output_refs`, and `note`. We should avoid embedding large binary data or full witness material. For commitment events, store digest, chunk count, encoding parameters, and fixture name. For torrent events, store peer count, chunk IDs, and retrieval status. For proof events, store circuit/profile name, public input digest, proof mode, and verifier result.

The README walkthrough should have three layers.

First, a quick path:

```bash
git clone ...
cd mosaic
just demo-trace
cat target/demo-trace/latest/verification.txt
```

Second, an annotated trace section that maps the generated events to protocol concepts. This is where person:john-light can link announcement readers. It should say, in plain terms, that Mosaic commits to data, moves chunks through the local torrent example, and checks a proof/verifier boundary. It should not say that Bitcoin verifies this proof today unless the actual path is present.

Third, a “what is mocked” section. This needs to be blunt:

- local peer topology is synthetic;
- fixture data is toy-sized;
- prover may be running in stub mode until `repo:g16` integration is enabled in the example;
- Strata integration is roadmap and not exercised by this command;
- no mainnet Bitcoin transaction is produced.

For implementation, person:Hakkush-07 should own the transcript shape and README language. person:cyphersnake should own the `repo:mosaic-torrent` local peer startup and fresh-checkout behavior. person:delbonis should review protocol terminology and whether the trace misstates the proof boundary. person:krsnapaudel should verify that the command works in a clean Linux environment and can be mirrored into `repo:alpen-dashboards` if we want a screenshot or run artifact later. person:mukeshdroid and person:AaronFeickert should review any claims that touch security assumptions, especially if we summarize Glock as more than an implementation dependency.

The walkthrough should include a short Strata note, but keep it scoped: “The objects in this trace are the same class of commitments/proof metadata expected to be consumed by future Strata integration work; this demo does not exercise that integration.” That gives DevRel a bridge without changing launch scope.

## Trade-offs

A single canonical trace reduces ambiguity, but it will lag the fastest-moving implementation branches. That is acceptable for announcement readiness. We need one reproducible reference more than we need the demo to expose every new protocol option.

Using stub prover mode is risky because readers may confuse a proof-shaped artifact with a real proof. The mitigation is naming and metadata. The file must say `stub` when stubbed, the README must say why, and announcement copy must not turn the stub path into a security claim. If real Glock proving is available behind a flag, we can include `PROVER_MODE=real just demo-trace` as an advanced path.

Keeping the torrent topology local makes the demo less impressive, but more reliable. A flaky multi-host demo would make the announcement dependent on person:krsnapaudel or person:cyphersnake being online during review. Local peers are enough to show object flow.

Avoiding Strata execution may disappoint readers looking for end-to-end product integration. But it is more damaging to imply a launch path that is still outside scope. The trace should give a clean handoff to Strata roadmap language rather than pretending the integration is already live.

## Rollout plan

By 2026-06-03, person:Hakkush-07 and person:cyphersnake should agree on the demo command, output directory, and transcript schema. The first implementation can be minimal as long as it runs from a fresh checkout.

By 2026-06-05, the demo should run locally on at least two machines and produce stable `manifest.json`, `transcript.json`, and `verification.txt`. person:delbonis should review the event names and proof boundary language before DevRel sees it as source material.

By 2026-06-07, person:krsnapaudel should run the command in a clean environment and file infra issues for missing packages, port conflicts, or service startup assumptions. Any dependency that cannot be installed through the repo’s documented setup should be treated as a blocker for the announcement walkthrough.

By 2026-06-10, person:john-light and person:pramodkandel should receive the README section and one archived trace output. Research review from person:mukeshdroid, person:AaronFeickert, and person:Hakkush-07 should focus only on correctness of claims, not prose polish.

By 2026-06-12, freeze the demo path for announcement use. After that point, implementation can continue, but DevRel links should point to the frozen command and reviewed README language. Any later improvement should preserve `just demo-trace` behavior or explicitly version the walkthrough.
