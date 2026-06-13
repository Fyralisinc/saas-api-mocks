# Model Layer vs Alpen Full-Corpus Report

> Historical eval artifact: this report came from a Fyralis model-layer run on
> 2026-06-11, before the 2026-06-13 Alpen model-layer enrichment pass. Keep it
> as the run result/reference for that evaluation. For the current answer key,
> use `ALPEN_LABS_MODEL_LAYER_GROUND_TRUTH.md`.

Generated: 2026-06-11T12:14:01.242855+00:00
Fyralis DB: `alpen_model_layer_run`
Tenant: `00000000-0000-0000-0000-00000000a1f0`
Mock run: `f0f075be-4ae8-4256-abce-0d13f6ce0a6a`
Elapsed seconds: `5573.8`

## Scope And Method

The run pointed Fyralis at all 25 local spammer mocks and used Fyralis production planners, fetchers, handlers, Think worker path, post-commit worker, and topology optimizer. Records were inserted through `services.ingest.ingestion.core.ingest()` with T1 enqueue enabled. Inline embeddings were intentionally skipped (`embedder=None`) so the model-layer evaluation was not bottlenecked by ingestion-time Ollama embedding generation.

Think/model-layer processing was phase-balanced rather than every single observation. The full corpus was ingested, then the harness selected `125` observations across Alpen phases and all sources for live Codex-backed Think.

Important ground-truth note: `ALPEN_COMPANY_STATE_REPORT.md` contains a top remediation block that states the current fixed corpus state as of 2026-06-11, while lower month-by-month tables are explicitly marked as a pre-fix snapshot frozen around December 2025. This report treats the remediation block plus the live `mock_orgs` seeded corpus as the current ground truth where those sections disagree.

## Preflight Evidence

- Fyralis branch: `main` (confirmed before run).
- Codex LLM path: existing Codex auth was valid; a structured sanity call returned `{'answer': 'ok'}`.
- Codec fix: `_ensure_vector_codec` present in `services/reasoning/retrieval/pathways.py`.
- Infra: Postgres :5432, Ollama :11434, Kafka :9092, and moto-S3 :5001 were reachable.
- Spammer rate limiter: `RateLimiter.take()` patched to return immediately for this sweep.

## Live Alpen Ground Truth

| Metric | Live value |
| --- | --- |
| people | 42 |
| drive_files | 323 |
| jira_issues | 476 |
| jira_bugs | 187 |
| aws_events | 4365 |
| github_commits | 6460 |
| github_prs | 4022 |
| slack_messages | 13499 |
| notion_pages | 2205 |
| fireflies_transcripts | 236 |

The key current-state expectations are: 42 people, 323 Drive files, 187 Jira bugs / 476 Jira issues, 4,365 AWS events, GitHub power-law activity, and H1 2026 artifacts including Mosaic.

## Seed Summary

| Fyralis table | Rows |
| --- | --- |
| actors | 42 |
| actor_identity_mappings | 381 |
| entity_aliases | 94 |
| provider_installations | 4 |
| gmail_installations | 1 |
| google_calendar_installations | 1 |
| google_drive_installations | 1 |
| jira_installations | 1 |
| mercury_installations | 1 |
| quickbooks_installations | 1 |
| grafana_installations | 1 |
| telegram_installations | 1 |
| signal_installations | 1 |
| brex_installations | 1 |
| deel_installations | 1 |
| ramp_installations | 1 |
| gusto_installations | 1 |
| carta_installations | 1 |
| linkedin_installations | 1 |
| fireflies_installations | 1 |
| aws_installations | 1 |
| miro_installations | 1 |
| figma_installations | 1 |
| hibob_installations | 1 |
| ashby_installations | 1 |

## Ingestion Results

| Source | Shards | Pages | Fetched | Inserted | Deduped | Errors | First errors |
| --- | --- | --- | --- | --- | --- | --- | --- |
| slack | 16 | 20 | 5186 | 5186 | 0 | 0 |  |
| discord | 5 | 10 | 859 | 859 | 0 | 0 |  |
| github | 192 | 8420 | 12685 | 12685 | 0 | 0 |  |
| gmail | 5 | 13 | 944 | 944 | 0 | 0 |  |
| google_calendar | 11 | 20 | 2927 | 2927 | 0 | 0 |  |
| notion | 4 | 4457 | 4410 | 4410 | 0 | 0 |  |
| google_drive | 1 | 4 | 323 | 323 | 0 | 0 |  |
| jira | 1 | 5 | 1325 | 1325 | 0 | 0 |  |
| quickbooks | 4 | 26 | 2344 | 2344 | 0 | 0 |  |
| grafana | 1 | 8 | 798 | 798 | 0 | 0 |  |
| mercury | 3 | 14 | 1187 | 1187 | 0 | 0 |  |
| ashby | 5 | 7 | 340 | 340 | 0 | 0 |  |
| brex | 2 | 2 | 1221 | 1221 | 0 | 0 |  |
| deel | 42 | 42 | 599 | 599 | 0 | 0 |  |
| hibob | 3 | 3 | 168 | 139 | 29 | 0 |  |
| figma | 8 | 8 | 194 | 194 | 0 | 0 |  |
| miro | 6 | 8 | 233 | 233 | 0 | 0 |  |
| ramp | 4 | 15 | 1335 | 1335 | 0 | 0 |  |
| gusto | 2 | 2 | 54 | 54 | 0 | 0 |  |
| carta | 4 | 4 | 103 | 103 | 0 | 0 |  |
| linkedin | 3 | 3 | 49 | 49 | 0 | 0 |  |
| fireflies | 1 | 5 | 236 | 236 | 0 | 0 |  |
| aws | 1 | 88 | 4365 | 4365 | 0 | 0 |  |
| telegram | 14 | 35 | 2895 | 2895 | 0 | 0 |  |
| signal | 12 | 32 | 2715 | 2715 | 0 | 0 |  |

Total Fyralis observations: `47747`

| Source channel | Observations |
| --- | --- |
| ashby:object | 340 |
| aws:event | 4365 |
| brex:transaction | 1221 |
| carta:object | 103 |
| deel:payment | 599 |
| discord:message | 859 |
| figma:event | 194 |
| fireflies:transcript | 236 |
| github:webhook | 12685 |
| gmail: | 944 |
| google_calendar:event | 2927 |
| google_drive:file | 323 |
| grafana:annotation | 798 |
| gusto:object | 54 |
| hibob:object | 139 |
| internal:state_change | 281 |
| jira:issue | 1325 |
| linkedin:object | 49 |
| mercury:transaction | 1187 |
| miro:item | 233 |
| notion:object | 4410 |
| quickbooks:object | 2344 |
| ramp:transaction | 1335 |
| signal:message | 2715 |
| slack:message | 5186 |
| telegram:message | 2895 |

## Pipeline Results

```json
{
  "post_commit": {
    "dead_lettered": 0,
    "failed": 0,
    "iterations": 1,
    "processed": 153
  },
  "think": {
    "pending_signal_t1": 0,
    "processed_attempts": 126,
    "status": "completed"
  },
  "topology": {
    "completed": 125,
    "failed": 0,
    "iterations": 2,
    "metrics": {
      "affordance_decays": 0,
      "affordance_reinforces": 255,
      "canonical_demote_candidates": 0,
      "canonical_merge_candidates": 0,
      "canonical_promote_candidates": 1,
      "canonical_split_candidates": 0,
      "canonical_validation_enqueued": 1.0,
      "missing_anchors": 0.0,
      "negative_memory_inserts": 1049,
      "noisy_paths": 1049.0,
      "objective_alignment_score": 0.0,
      "quality_failure_modes": 0.0,
      "question_policy_updates": 0,
      "region_refreshes": 0,
      "shortcut_creates_or_bumps": 295,
      "shortcut_decays": 0,
      "shortcut_missing_model_skips": 23.0,
      "structural_edges_written": 91.0,
      "structural_missing_model_skips": 23.0,
      "structural_models_written": 281.0,
      "trigger_recognized": 0.0,
      "useful_nodes": 278.0,
      "useful_paths": 40.0
    },
    "processed": 125,
    "status": "drained"
  }
}
```

Trigger queue summary:

| Kind | Subkind | Total | Completed |
| --- | --- | --- | --- |
| T1 | event_arrival | 47466 | 125 |
| T2 | belief_updated | 17 | 0 |
| T3 | missing_transition | 4 | 0 |
| T4 | latent_relationship_candidate | 207 | 0 |

Think run statuses:

| Status | Count |
| --- | --- |
| failed | 1 |
| success | 125 |

## Model Layer Inventory

Total models: `209`

| Proposition kind | Count |
| --- | --- |
| belief | 169 |
| observation | 19 |
| prediction | 17 |
| norm | 4 |

Representative models:

| Model | Kind | Status | Confidence | Natural |
| --- | --- | --- | --- | --- |
| 019eb65b-7ae7-7000-bf16-d4059424ae61 | belief | active | 0.76 | Prajwol Gyawali endorsed the decision to keep prover changes separate, calling it a good call. |
| 019eb65b-df5a-7000-95d7-3d0d4fa8354d | belief | active | 0.61 | may need a clean specification pass before proceeding. |
| 019eb65c-48cf-7000-8857-1ba7943fde56 | belief | active | 0.62 | madan-oss self-reported that their dinner proof is mostly handwavy tonight, indicating a localized concern about the proof's rigor or readiness. |
| 019eb65c-492e-7000-992c-252c9a9c90c0 | belief | active | 0.62 | the proof is mostly handwavy tonight. |
| 019eb65c-49c3-7000-a69a-6882ece4ac09 | belief | active | 0.62 | Composite situation: madan-oss self-reported that their dinner proof is mostly handwavy tonight, indicating a localized concern about the proof's rigor or readiness. \| the proof is mostly handwavy tonight |
| 019eb65c-ab1c-7000-8ffa-a08bdaf93258 | belief | active | 0.66 | Armin Sabouri reported a keyboard/input issue today: spaces are being omitted intermittently. |
| 019eb65c-abcb-7000-bfd4-8f33034db408 | belief | active | 0.66 | Actor reported that their keyboard is intermittently omitting spaces today. |
| 019eb65c-ac69-7000-a486-cf709937a4af | belief | active | 0.66 | Composite situation: Armin Sabouri reported a keyboard/input issue today: spaces are being omitted intermittently. \| Actor reported that their keyboard is intermittently omitting spaces today. |
| 019eb65d-0e43-7000-bae0-324f9375bcc1 | belief | active | 0.62 | Purushotam Sangroula reported that his apartment wifi was behaving unreliably, using the phrase "doing rpc cosplay.". |
| 019eb65d-0f20-7000-969a-04d6e7d50373 | belief | active | 0.62 | reported that their apartment wifi was behaving unreliably in a way likened to RPC issues. |
| 019eb65d-0fe3-7000-837c-30d288d187e7 | belief | active | 0.62 | Composite situation: Purushotam Sangroula reported that his apartment wifi was behaving unreliably, using the phrase "doing rpc cosplay." \| reported that their apartment wifi was behaving unreliably in a way likened to... |
| 019eb65d-7f1c-7000-af68-dcfd2ba48c4c | belief | active | 0.74 | manishbista28 finds the Vitalik post interesting, but is specifically concerned that edge cases matter for evaluating it here. |
| 019eb65d-cfc5-7000-9b15-0e5962b3e8d6 | belief | active | 0.74 | Mukesh treats the ZK news as positive, but says the soundness details still determine whether it matters. |
| 019eb65e-c8e0-7000-a4a8-f9c5a3925dd4 | belief | active | 0.76 | Trey Del Bonis reported that the release notes are light on edge cases, indicating a documentation coverage concern rather than a verified release blocker. |
| 019eb65f-28bc-7000-9b3d-22e5ebcc7ed6 | belief | active | 0.74 | voidash signaled that the bridge design is interesting. |
| 019eb65f-29aa-7000-8af8-60c8ccbf4adc | belief | active | 0.74 | but its exit path needs closer scrutiny. |
| 019eb65f-2a75-7000-895a-18bd4a800b90 | belief | active | 0.74 | the exit path needs scrutiny despite the bridge design being interesting. |
| 019eb65f-2b5c-7000-8159-c361d79d9b3e | belief | active | 0.74 | Composite situation: voidash signaled that the bridge design is interesting, but its exit path needs closer scrutiny. \| the exit path needs scrutiny despite the bridge design being interesting |
| 019eb65f-775d-7000-8c0d-fe7f468c03c1 | belief | active | 0.78 | The external sender reports hiking a bit and experiencing a knee-related edge case, indicating some physical discomfort or limitation during the hike. |
| 019eb65f-e85e-7000-9f66-fc1ca50138c1 | belief | active | 0.80 | Fixture #14 in the shared verifier suite still fails when the curve order is non-prime. |
| 019eb65f-e97b-7000-9fe2-d337a41e44dc | belief | active | 0.80 | still trips when the curve order is non-prime. |
| 019eb65f-ea70-7000-b99a-0ee294eec712 | belief | active | 0.80 | Composite situation: Fixture #14 in the shared verifier suite still fails when the curve order is non-prime. \| still trips when the curve order is non-prime |
| 019eb65f-eb8d-7000-b4d6-55c8bade0a4f | belief | active | 0.74 | A follow-up was opened for the fixture #14 non-prime curve order failure. |
| 019eb65f-eca5-7000-9375-aa53eb318475 | belief | active | 0.74 | a follow-up has been opened. |
| 019eb660-6fb4-7000-bc2a-71bdf02773f9 | belief | active | 0.82 | Jose reported that the Strata bridge reference implementation has been open-sourced. |
| 019eb660-7121-7000-bc9e-ab3539c78274 | belief | active | 0.78 | walkthrough for the Strata bridge reference implementation are in the thread. |
| 019eb660-c4cf-7000-be26-ff5af1112930 | belief | active | 0.82 | mdqst closed PR #959, titled 'fix: gas price handling when from_rpc is true', without merging. |
| 019eb660-c624-7000-a958-eafc8d9f0a74 | belief | active | 0.82 | PR #959 in alpenlabs/alpen was closed without merging. |
| 019eb660-c759-7000-a462-73a9d409b2b9 | belief | active | 0.82 | Composite situation: mdqst closed PR #959, titled 'fix: gas price handling when from_rpc is true', without merging. \| PR #959 in alpenlabs/alpen was closed without merging |
| 019eb661-2f23-7000-8c7a-505905223230 | belief | active | 0.85 | krsnapaudel merged PR #1095, titled 'fix: what ASM and CSM mean', into the main branch of alpenlabs/alpen. |
| 019eb661-a091-7000-bfc3-a7f80066316d | belief | active | 0.74 | irnb approved PR #1873 in alpenlabs/alpen while asking whether a clone operation could be avoided. |
| 019eb661-a122-7000-9322-d1311439e667 | belief | active | 0.74 | An approved review still raised an implementation concern about whether cloning can be avoided. |
| 019eb661-a177-7000-87ef-63e213afd12c | belief | active | 0.74 | Composite situation: irnb approved PR #1873 in alpenlabs/alpen while asking whether a clone operation could be avoided. \| An approved review still raised an implementation concern about whether cloning can be avoided. |
| 019eb662-6d7d-7000-8ae6-a6efe30a98e1 | belief | active | 0.85 | Jose Storopoli opened GitHub issue #8, titled 'Use `bitcoin-addresses` + `bitcoin-primitives` instead of `bitcoin`', in alpenlabs/bitcoin-bosd. |
| 019eb662-d449-7000-9feb-b4aacacfbdf3 | belief | active | 0.81 | Jose Storopoli pushed 1 commit to an unknown branch in alpenlabs/bitcoin-bosd at 2025-01-09T21:39:57+00:00. |
| 019eb663-bf97-7000-ad6f-d931c938a1e5 | belief | active | 0.72 | Keags approved PR #32 in alpenlabs/faucet-api but raised a merge-blocking test coverage concern: the PR needs a negative test before merge. |
| 019eb663-c0f7-7000-a9ef-b4bc984ee99e | belief | active | 0.72 | Keags approved the review while saying the PR needs a negative test before merge. |
| 019eb663-c24b-7000-b79f-f373cb93ae7f | belief | active | 0.72 | Composite situation: Keags approved PR #32 in alpenlabs/faucet-api but raised a merge-blocking test coverage concern: the PR needs a negative test before merge. \| Keags approved the review while saying the PR needs a n... |
| 019eb664-20c9-7000-95b9-60770a594e25 | belief | active | 0.85 | prajwolrg merged PR #5, titled 'Moho recursive proof', into the main branch of alpenlabs/moho. |
| 019eb664-772d-7000-8fb4-ba4b3ce4178c | belief | active | 0.85 | Trey Del Bonis pushed 1 commit to an unknown branch in alpenlabs/moho at 2025-08-04T17:59:54+00:00. |
| 019eb664-d72f-7000-a498-c3edb935e953 | belief | active | 0.85 | Rajil1213 merged PR #249, titled 'docs: remove outdated system diagram', into the main branch of alpenlabs/strata-bridge. |
| 019eb665-3d43-7000-9195-0213edea0382 | belief | active | 0.80 | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T20:14:42Z. |
| 019eb665-b755-7000-857c-6becdfac0e07 | observation | active | 0.82 | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T19:57:56Z. |
| 019eb667-d540-7000-ad8c-8ce4240826fe | belief | active | 0.72 | sistemd approved PR #249 in alpenlabs/strata-bridge. |
| 019eb667-d6cc-7000-815f-ec9cb13a5735 | belief | active | 0.72 | but the approval still carried a test coverage request: the PR needs one negative test. |
| 019eb667-d83e-7000-92b1-6f3b40f1dc35 | belief | active | 0.72 | sistemd approved the review while still requesting one negative test. |
| 019eb667-d9b1-7000-946f-8838cc0da1c4 | belief | active | 0.72 | Composite situation: sistemd approved PR #249 in alpenlabs/strata-bridge, but the approval still carried a test coverage request: the PR needs one negative test. \| sistemd approved the review while still requesting one... |
| 019eb668-707f-7000-865d-4ca31d5cae2c | belief | active | 0.72 | Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge. |
| 019eb668-7265-7000-82c7-56d445ab1b4f | belief | active | 0.72 | Prajwol Gyawali approved the review while indicating the change is acceptable after the boundary check is moved up. |
| 019eb668-7376-7000-ade2-04f25e40401c | belief | active | 0.72 | Composite situation: Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge, with the approval tied to the boundary check moving up. \| Prajwol Gyawali approved the review while indicating the change is acceptable... |
| 019eb668-e04b-7000-acff-dbe21ab10274 | belief | active | 0.86 | Abishkar confirmed receipt of the signed PA for the Alpen seed process. |
| 019eb668-e0c9-7000-aea9-e28b9cc057b0 | belief | active | 0.86 | Abishkar stated that wire instructions for the Alpen seed process are on file. |
| 019eb668-e137-7000-81db-975ffdfb0e4b | belief | active | 0.86 | wire instructions are on file. |
| 019eb668-e1aa-7000-bc4b-aad6808aaffd | belief | active | 0.85 | Composite situation: Abishkar stated that wire instructions for the Alpen seed process are on file. \| wire instructions are on file |
| 019eb669-bb2d-7000-8528-a2f51532fd7e | belief | active | 0.82 | Abishkar stated that wire instructions are on file for Alpen seed. |
| 019eb66a-f978-7000-a1e3-69c859585655 | belief | active | 0.86 | Receipt of the signed PA has been confirmed. |
| 019eb66a-fa16-7000-a873-0c0d731eee95 | belief | active | 0.86 | Composite situation: Abishkar confirmed receipt of the signed PA for Alpen seed and said wire instructions are on file. \| Receipt of the signed PA has been confirmed and wire instructions are on file. |
| 019eb66b-7d02-7000-ac8e-c9a90c352b4b | belief | active | 0.78 | Jay reported that the Alpen seed wire was initiated on the morning of 2024-04-09. The provided reference was ALP-SEED-GEOMETRY. |
| 019eb66b-7f19-7000-86b0-389fb1615730 | belief | active | 0.78 | Jay reported that the wire was initiated on 2024-04-09 with reference ALP-SEED-GEOMETRY. |
| 019eb66b-8107-7000-936b-d8c5c8734e7f | belief | active | 0.78 | Composite situation: Jay reported that the Alpen seed wire was initiated on the morning of 2024-04-09. The provided reference was ALP-SEED-GEOMETRY. \| Jay reported that the wire was initiated on 2024-04-09 with referen... |
| 019eb66b-8306-7000-b43b-e901cb41ab43 | prediction | active | 0.66 | Jay expected the Alpen seed wire to land the next day. |
| 019eb66b-8535-7000-af75-0ce77fdd7df4 | prediction | active | 0.68 | The Alpen seed wire should land on 2024-04-10. |
| 019eb66b-8713-7000-9b92-27a233815ef1 | belief | active | 0.72 | Jay said the signed PA for the Alpen seed transaction was on its way separately. This indicates the wire. |
| 019eb66b-8919-7000-9c06-0115aba152da | belief | active | 0.72 | paperwork were proceeding through separate channels. |
| 019eb66b-8b13-7000-b607-b577b986d180 | belief | active | 0.72 | Jay said the signed PA was being sent separately. |
| 019eb66c-0bd9-7000-9830-de93601a9605 | belief | active | 0.82 | Micky reported that the Alpen seed wire with reference ALP-SEED-RIBBIT was initiated on the morning of 2024-04-09. |
| 019eb66c-0c94-7000-8f03-f23c00371389 | prediction | active | 0.66 | A signed PA for the Alpen seed transaction will arrive separately. |
| 019eb66c-0d06-7000-8c9c-2c56cc2b87bb | belief | active | 0.66 | Composite situation: Micky said the signed PA for the Alpen seed transaction was on its way separately. \| A signed PA for the Alpen seed transaction will arrive separately. |
| 019eb66c-7d55-7000-ab13-0088f87806f2 | belief | active | 0.86 | Abishkar confirmed that the signed PA for the Alpen seed was received. |
| 019eb66d-003a-7000-8cd7-ff0a51844763 | belief | active | 0.85 | Alyse reports that the Alpen seed wire was initiated on the morning of 2024-04-09. The wire reference is ALP-SEED-STILLMARK. |
| 019eb66d-00db-7000-a4ef-1f63e15e570f | prediction | active | 0.65 | Alyse expects the Alpen seed wire to land on 2024-04-10. |
| 019eb66d-015f-7000-802a-d51746d81d93 | belief | active | 0.65 | Composite situation: Alyse expects the Alpen seed wire to land on 2024-04-10. \| The Alpen seed wire should land on 2024-04-10. |
| 019eb66d-029f-7000-b8b6-ec91679111d1 | belief | active | 0.65 | Alyse says the signed PA is being sent separately from the wire confirmation. |
| 019eb66d-0457-7000-86e4-7fcdd135ac80 | prediction | active | 0.65 | A signed PA will be sent separately from the wire confirmation message. |
| 019eb66d-fcf0-7000-a50a-c95fa34302e3 | belief | active | 0.82 | Alpen reports that Strata testnet hardening is continuing. |
| 019eb66d-fefd-7000-8444-ae2a2288eb12 | belief | active | 0.82 | Strata is continuing testnet hardening. |
| 019eb66e-013b-7000-83be-5b22a47ccaac | belief | active | 0.82 | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track. \| Strata is continuing testnet hardening, with bridge throughput and prove... |
| 019eb66e-0334-7000-995f-769869bc6696 | belief | active | 0.76 | Alpen says hiring is still in flow. |
| 019eb66e-058d-7000-ad0c-59dd573d78f0 | prediction | active | 0.76 | that specific names will be shared in the next quarterly update. |
| 019eb66e-0716-7000-8365-efa999d1870a | prediction | active | 0.76 | specific names are expected to be shared in the next quarterly update. |

## Per-Phase Comparison

### Founding And Stealth (2024-02-01 to 2024-04-09)

Expected ground truth:
- Alpen Labs was founded February 1, 2024 by four cofounders.
- The mission centered on a Bitcoin financial system / Bitcoin-native ZK infrastructure.
- The company operated in stealth while shaping Strata and the ZK/BitVM thesis.

Correctly represented:
- The mission centered on a Bitcoin financial system / Bitcoin-native ZK infrastructure.

Deviations: none detected by stale/current-state pattern checks.

Missed:
- Alpen Labs was founded February 1, 2024 by four cofounders.
- The company operated in stealth while shaping Strata and the ZK/BitVM thesis.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb670-0db3-7000-97f6-6befaf3fb0d7 | belief | 0.69 | found | Simanta says he is spinning up Alpen Labs with three MIT cofounders to build Bitcoin-native finance infrastructure with ZK. |
| 019eb670-0e7b-7000-92a0-58552b269687 | belief | 0.69 | found, founded | Composite situation: Simanta reports that Alpen Labs is being spun up by him and three MIT cofounders, focused on Bitcoin-native finance infrastructure with ZK. \| Simanta says he is spinning up Alpen Labs with three MI... |
| 019eb681-5c3d-7000-b0ae-7be5f86c01ee | belief | 0.90 | found | QuickBooks reports Invoice #INV-dep-09a7024436e1 for Starknet Foundation as paid in full: $250,000.00 amount, $0.00 remaining balance, 1 line. |
| 019eb681-e307-7000-a276-70a27d730123 | belief | 0.90 | found | QuickBooks recorded payment #P-dep-09a7024436e1 from Starknet Foundation for $250,000.00 with one line. |
| 019eb68d-638d-7000-ab18-4a3d3596ee3d | belief | 0.90 | found | The Gusto object records Chandan Sharma Subedi as an active employee with Cofounder and Go-To-Market role information. |
| 019eb68d-e7af-7000-8054-f996fc80db57 | belief | 0.90 | found | Abishkar Chhetri is an active employee with Cofounder. |
| 019eb68d-e890-7000-a26c-947ed683e171 | belief | 0.90 | found | Composite situation: Authoritative Gusto object states that Abishkar Chhetri is an active employee, with Cofounder and Go-To-Market listed as role/context. \| Abishkar Chhetri is an active employee with Cofounder and Go... |
| 019eb68e-5096-7000-9191-d00112ba215b | belief | 0.90 | found | Authoritative Gusto employee object reports Pramod Kandel as Cofounder in Engineering with active status. |
| 019eb68e-cd12-7000-a5bb-68b38d3ba966 | belief | 0.90 | found | Gusto reports Simanta Gautam as an active employee, with title Cofounder and department Operations. |

### Seed And Public Launch (2024-04-09 to 2025-01-09)

Expected ground truth:
- Alpen launched publicly around April 9, 2024.
- The seed round was $10.6M led by Ribbit Capital.
- Strata emerged as the core Bitcoin rollup / verification system.

Correctly represented: none found by keyword/evidence matching.

Deviations:
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: `product:strata-bridge` across the December 2025 rollout period. The source text is truncated after "through ear".
  Reason: stale flags: december 2025
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: Composite situation: A Notion RFC titled "Prague Testnet Support Scope and Rollout Criteria" defines Prague testnet support scope for `product:strata` and `product:strata-bridge` across the December 2025 rollout period...
  Reason: stale flags: december 2025

Missed:
- Alpen launched publicly around April 9, 2024.
- The seed round was $10.6M led by Ribbit Capital.
- Strata emerged as the core Bitcoin rollup / verification system.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb660-6fb4-7000-bc2a-71bdf02773f9 | belief | 0.82 | strata | Jose reported that the Strata bridge reference implementation has been open-sourced. |
| 019eb660-7121-7000-bc9e-ab3539c78274 | belief | 0.78 | strata | walkthrough for the Strata bridge reference implementation are in the thread. |
| 019eb664-d72f-7000-a498-c3edb935e953 | belief | 0.85 | strata | Rajil1213 merged PR #249, titled 'docs: remove outdated system diagram', into the main branch of alpenlabs/strata-bridge. |
| 019eb665-3d43-7000-9195-0213edea0382 | belief | 0.80 | strata | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T20:14:42Z. |
| 019eb665-b755-7000-857c-6becdfac0e07 | observation | 0.82 | strata | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T19:57:56Z. |
| 019eb667-d540-7000-ad8c-8ce4240826fe | belief | 0.72 | strata | sistemd approved PR #249 in alpenlabs/strata-bridge. |
| 019eb667-d6cc-7000-815f-ec9cb13a5735 | belief | 0.72 | strata | but the approval still carried a test coverage request: the PR needs one negative test. |
| 019eb667-d83e-7000-92b1-6f3b40f1dc35 | belief | 0.72 | strata | sistemd approved the review while still requesting one negative test. |
| 019eb667-d9b1-7000-946f-8838cc0da1c4 | belief | 0.72 | strata | Composite situation: sistemd approved PR #249 in alpenlabs/strata-bridge, but the approval still carried a test coverage request: the PR needs one negative test. \| sistemd approved the review while still requesting one... |
| 019eb668-707f-7000-865d-4ca31d5cae2c | belief | 0.72 | strata | Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge. |
| 019eb668-7265-7000-82c7-56d445ab1b4f | belief | 0.72 | strata | Prajwol Gyawali approved the review while indicating the change is acceptable after the boundary check is moved up. |
| 019eb668-7376-7000-ade2-04f25e40401c | belief | 0.72 | strata | Composite situation: Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge, with the approval tied to the boundary check moving up. \| Prajwol Gyawali approved the review while indicating the change is acceptable... |
| 019eb668-e04b-7000-acff-dbe21ab10274 | belief | 0.86 | seed | Abishkar confirmed receipt of the signed PA for the Alpen seed process. |
| 019eb668-e0c9-7000-aea9-e28b9cc057b0 | belief | 0.86 | seed | Abishkar stated that wire instructions for the Alpen seed process are on file. |
| 019eb668-e137-7000-81db-975ffdfb0e4b | belief | 0.86 | seed | wire instructions are on file. |
| 019eb668-e1aa-7000-bc4b-aad6808aaffd | belief | 0.85 | seed | Composite situation: Abishkar stated that wire instructions for the Alpen seed process are on file. \| wire instructions are on file |
| 019eb669-bb2d-7000-8528-a2f51532fd7e | belief | 0.82 | seed | Abishkar stated that wire instructions are on file for Alpen seed. |
| 019eb66a-f978-7000-a1e3-69c859585655 | belief | 0.86 | seed | Receipt of the signed PA has been confirmed. |
| 019eb66a-fa16-7000-a873-0c0d731eee95 | belief | 0.86 | seed | Composite situation: Abishkar confirmed receipt of the signed PA for Alpen seed and said wire instructions are on file. \| Receipt of the signed PA has been confirmed and wire instructions are on file. |
| 019eb66b-7d02-7000-ac8e-c9a90c352b4b | belief | 0.78 | seed | Jay reported that the Alpen seed wire was initiated on the morning of 2024-04-09. The provided reference was ALP-SEED-GEOMETRY. |
| 019eb66b-7f19-7000-86b0-389fb1615730 | belief | 0.78 | seed | Jay reported that the wire was initiated on 2024-04-09 with reference ALP-SEED-GEOMETRY. |
| 019eb66b-8107-7000-936b-d8c5c8734e7f | belief | 0.78 | seed | Composite situation: Jay reported that the Alpen seed wire was initiated on the morning of 2024-04-09. The provided reference was ALP-SEED-GEOMETRY. \| Jay reported that the wire was initiated on 2024-04-09 with referen... |
| 019eb66b-8306-7000-b43b-e901cb41ab43 | prediction | 0.66 | seed | Jay expected the Alpen seed wire to land the next day. |
| 019eb66b-8535-7000-af75-0ce77fdd7df4 | prediction | 0.68 | seed | The Alpen seed wire should land on 2024-04-10. |
| 019eb66b-8713-7000-9b92-27a233815ef1 | belief | 0.72 | seed | Jay said the signed PA for the Alpen seed transaction was on its way separately. This indicates the wire. |

### Strategic Round (2025-01-09 to 2025-08-04)

Expected ground truth:
- A $8.5M strategic round was announced January 9, 2025 with DBA and Cyber Fund.
- The company moved from research posture toward testnet and bridge execution.
- Hiring, finance, and operational systems expanded materially.

Correctly represented:
- A $8.5M strategic round was announced January 9, 2025 with DBA and Cyber Fund.

Deviations:
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: `product:strata-bridge` across the December 2025 rollout period. The source text is truncated after "through ear".
  Reason: stale flags: december 2025
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: Composite situation: A Notion RFC titled "Prague Testnet Support Scope and Rollout Criteria" defines Prague testnet support scope for `product:strata` and `product:strata-bridge` across the December 2025 rollout period...
  Reason: stale flags: december 2025

Missed:
- The company moved from research posture toward testnet and bridge execution.
- Hiring, finance, and operational systems expanded materially.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb65f-28bc-7000-9b3d-22e5ebcc7ed6 | belief | 0.74 | bridge | voidash signaled that the bridge design is interesting. |
| 019eb65f-29aa-7000-8af8-60c8ccbf4adc | belief | 0.74 | bridge | but its exit path needs closer scrutiny. |
| 019eb65f-2a75-7000-895a-18bd4a800b90 | belief | 0.74 | bridge | the exit path needs scrutiny despite the bridge design being interesting. |
| 019eb65f-2b5c-7000-8159-c361d79d9b3e | belief | 0.74 | bridge | Composite situation: voidash signaled that the bridge design is interesting, but its exit path needs closer scrutiny. \| the exit path needs scrutiny despite the bridge design being interesting |
| 019eb660-6fb4-7000-bc2a-71bdf02773f9 | belief | 0.82 | bridge | Jose reported that the Strata bridge reference implementation has been open-sourced. |
| 019eb660-7121-7000-bc9e-ab3539c78274 | belief | 0.78 | bridge | walkthrough for the Strata bridge reference implementation are in the thread. |
| 019eb664-d72f-7000-a498-c3edb935e953 | belief | 0.85 | bridge | Rajil1213 merged PR #249, titled 'docs: remove outdated system diagram', into the main branch of alpenlabs/strata-bridge. |
| 019eb665-3d43-7000-9195-0213edea0382 | belief | 0.80 | bridge | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T20:14:42Z. |
| 019eb665-b755-7000-857c-6becdfac0e07 | observation | 0.82 | bridge | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T19:57:56Z. |
| 019eb667-d540-7000-ad8c-8ce4240826fe | belief | 0.72 | bridge | sistemd approved PR #249 in alpenlabs/strata-bridge. |
| 019eb667-d6cc-7000-815f-ec9cb13a5735 | belief | 0.72 | bridge | but the approval still carried a test coverage request: the PR needs one negative test. |
| 019eb667-d83e-7000-92b1-6f3b40f1dc35 | belief | 0.72 | bridge | sistemd approved the review while still requesting one negative test. |
| 019eb667-d9b1-7000-946f-8838cc0da1c4 | belief | 0.72 | bridge | Composite situation: sistemd approved PR #249 in alpenlabs/strata-bridge, but the approval still carried a test coverage request: the PR needs one negative test. \| sistemd approved the review while still requesting one... |
| 019eb668-707f-7000-865d-4ca31d5cae2c | belief | 0.72 | bridge | Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge. |
| 019eb668-7265-7000-82c7-56d445ab1b4f | belief | 0.72 | bridge | Prajwol Gyawali approved the review while indicating the change is acceptable after the boundary check is moved up. |
| 019eb668-7376-7000-ade2-04f25e40401c | belief | 0.72 | bridge | Composite situation: Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge, with the approval tied to the boundary check moving up. \| Prajwol Gyawali approved the review while indicating the change is acceptable... |
| 019eb66e-013b-7000-83be-5b22a47ccaac | belief | 0.82 | bridge | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track. \| Strata is continuing testnet hardening, with bridge throughput and prove... |
| 019eb66e-90b6-7000-a755-fe421d6f5be4 | belief | 0.78 | bridge | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track as of the June 2026 investor update. \| testnet hardening is continuing, wit... |
| 019eb66f-8269-7000-84d8-38f00647703a | belief | 0.82 | bridge | The signal says Strata and Starknet are the first two chains adopting Glock for trust-minimised BTC bridging. |
| 019eb670-0f2e-7000-88a0-d4794ea60499 | norm | 0.62 | bridge | Because Simanta reports newly in-flight Alpen Labs formation and bridge fundraising work, create a commitment to track that workstream. |
| 019eb673-4160-7000-ac5a-3c814b2572af | belief | 0.85 | strategic | Pramod Kandel scheduled 'Strategic Round Technical Diligence — kickoff' for 2025-01-09 09:00 UTC with six attendees from Alpen Labs. |
| 019eb673-4327-7000-aef0-6f1fed67007e | prediction | 0.53 | strategic | Future plan to verify: pramodkandel@alpenlabs.io scheduled 'Strategic Round Technical Diligence — kickoff' at 2025-01-09 09:00 UTC with 6 attendee(s): pramodkandel@alpenlabs.io, john-light@alpenlabs.i... |
| 019eb678-ae32-7000-a126-3cbce4015055 | belief | 0.68 | strategic | review path. |
| 019eb678-b108-7000-b642-27b7046683b0 | belief | 0.68 | strategic | review path for a strategic round technical diligence room covering product areas including product:strata. |
| 019eb678-b1d7-7000-ae0e-4fc633124484 | belief | 0.68 | strategic | Composite situation: A Notion RFC exists for the strategic round technical diligence room. It defines scope, ownership, and review path, with product:strata explicitly included in the covered product areas. \| A Notion... |

### Public Testnet (2025-08-04 to 2025-08-19)

Expected ground truth:
- Alpen public testnet went live on August 4, 2025.
- The model should understand testnet as a major company phase, not only a Jira/GitHub event.

Correctly represented:
- Alpen public testnet went live on August 4, 2025.

Deviations:
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: `product:strata-bridge` across the December 2025 rollout period. The source text is truncated after "through ear".
  Reason: stale flags: december 2025
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: Composite situation: A Notion RFC titled "Prague Testnet Support Scope and Rollout Criteria" defines Prague testnet support scope for `product:strata` and `product:strata-bridge` across the December 2025 rollout period...
  Reason: stale flags: december 2025

Missed:
- The model should understand testnet as a major company phase, not only a Jira/GitHub event.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb66d-fcf0-7000-a50a-c95fa34302e3 | belief | 0.82 | testnet | Alpen reports that Strata testnet hardening is continuing. |
| 019eb66d-fefd-7000-8444-ae2a2288eb12 | belief | 0.82 | testnet | Strata is continuing testnet hardening. |
| 019eb66e-013b-7000-83be-5b22a47ccaac | belief | 0.82 | testnet | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track. \| Strata is continuing testnet hardening, with bridge throughput and prove... |
| 019eb66e-8ede-7000-b80f-6ebbac9c3bfd | belief | 0.78 | testnet | testnet hardening is continuing. |
| 019eb66e-90b6-7000-a755-fe421d6f5be4 | belief | 0.78 | testnet | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track as of the June 2026 investor update. \| testnet hardening is continuing, wit... |
| 019eb672-2508-7000-bb06-7c657c9c0852 | observation | 0.82 | testnet, public testnet | Prajwol Gyawali scheduled an Alpen Public Testnet Launch kickoff meeting for 2025-08-04 09:00 UTC with four Alpen Labs attendees. |
| 019eb672-27d8-7000-8039-62273aa2505a | norm | 0.56 | testnet, public testnet | Because the kickoff meeting indicates a new in-flight launch workstream and no matching commitment exists, create a commitment to track the Alpen Public Testnet Launch kickoff. |
| 019eb672-2a03-7000-a661-48adb0f146ae | prediction | 0.53 | testnet, public testnet | Future plan to verify: prajwolrg@alpenlabs.io scheduled 'Alpen Public Testnet Launch — kickoff' at 2025-08-04 09:00 UTC with 4 attendee(s): prajwolrg@alpenlabs.io, krsnapaudel@alpenlabs.io, rajil1213@... |
| 019eb672-c2b0-7000-b2cf-fe998408bc21 | observation | 0.85 | testnet | Prajwol Gyawali scheduled a Prague Testnet Support kickoff meeting for 2025-12-04 09:00 UTC with five attendees. |
| 019eb672-c369-7000-b482-0abe7eba3ef1 | norm | 0.58 | testnet | The Prague Testnet Support kickoff warrants creating a commitment record owned by Prajwol Gyawali because no matching commitment exists in the provided Acts context. |
| 019eb672-c3f6-7000-8163-c38ab2504a46 | prediction | 0.53 | testnet | Future plan to verify: prajwolrg@alpenlabs.io scheduled 'Prague Testnet Support — kickoff' at 2025-12-04 09:00 UTC with 5 attendee(s): prajwolrg@alpenlabs.io, mdteach@alpenlabs.io, krsnapaudel@alpenla... |
| 019eb679-1447-7000-ac12-36431f0e0dbd | belief | 0.68 | testnet | `product:strata-bridge` across the December 2025 rollout period. The source text is truncated after "through ear". |
| 019eb679-1645-7000-bddf-3b865fefa0f5 | belief | 0.68 | testnet | so the exact end of the rollout window is not captured here. |
| 019eb679-1715-7000-8f5f-e87956e6479c | belief | 0.68 | testnet | Composite situation: A Notion RFC titled "Prague Testnet Support Scope and Rollout Criteria" defines Prague testnet support scope for `product:strata` and `product:strata-bridge` across the December 2025 rollout period... |
| 019eb67b-117e-7000-9bd2-2d73b9acb3d8 | observation | 0.82 | testnet, public testnet | Jira recorded STR-7721 as the Alpen Public Testnet Launch epic in To Do with Medium priority on 2025-08-04 at 09:00 UTC. |
| 019eb67c-6f2d-7000-8d9b-7e2e4eb5dbb8 | observation | 0.84 | testnet | Jira issue STR-3074 records Prague Testnet Support as an Epic in To Do status with Medium priority. |

### Glock Release (2025-08-19 to 2025-10-15)

Expected ground truth:
- Glock was publicly released August 19, 2025.
- Glock is a ZK/prover-related component and later supports shared prover work.

Correctly represented: none found by keyword/evidence matching.

Deviations: none detected by stale/current-state pattern checks.

Missed:
- Glock was publicly released August 19, 2025.
- Glock is a ZK/prover-related component and later supports shared prover work.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb65b-7ae7-7000-bf16-d4059424ae61 | belief | 0.76 | prover | Prajwol Gyawali endorsed the decision to keep prover changes separate, calling it a good call. |
| 019eb65d-cfc5-7000-9b15-0e5962b3e8d6 | belief | 0.74 | zk | Mukesh treats the ZK news as positive, but says the soundness details still determine whether it matters. |
| 019eb65e-c8e0-7000-a4a8-f9c5a3925dd4 | belief | 0.76 | release | Trey Del Bonis reported that the release notes are light on edge cases, indicating a documentation coverage concern rather than a verified release blocker. |
| 019eb66e-013b-7000-83be-5b22a47ccaac | belief | 0.82 | prover | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track. \| Strata is continuing testnet hardening, with bridge throughput and prove... |
| 019eb66e-90b6-7000-a755-fe421d6f5be4 | belief | 0.78 | prover | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track as of the June 2026 investor update. \| testnet hardening is continuing, wit... |
| 019eb66f-8269-7000-84d8-38f00647703a | belief | 0.82 | glock | The signal says Strata and Starknet are the first two chains adopting Glock for trust-minimised BTC bridging. |
| 019eb670-0db3-7000-97f6-6befaf3fb0d7 | belief | 0.69 | zk | Simanta says he is spinning up Alpen Labs with three MIT cofounders to build Bitcoin-native finance infrastructure with ZK. |
| 019eb670-0e7b-7000-92a0-58552b269687 | belief | 0.69 | zk | Composite situation: Simanta reports that Alpen Labs is being spun up by him and three MIT cofounders, focused on Bitcoin-native finance infrastructure with ZK. \| Simanta says he is spinning up Alpen Labs with three MI... |
| 019eb670-992c-7000-80a5-bcce318bfa61 | belief | 0.82 | glock, release | Aaron Feickert scheduled 'Glock Public Release — kickoff' for 2025-08-19 09:00 UTC with four listed Alpen Labs attendees. |
| 019eb670-9b3c-7000-b2a5-37111e53f002 | prediction | 0.53 | glock, release | Future plan to verify: aaronfeickert@alpenlabs.io scheduled 'Glock Public Release — kickoff' at 2025-08-19 09:00 UTC with 4 attendee(s): aaronfeickert@alpenlabs.io, mukeshdroid@alpenlabs.io, hakkush-0... |
| 019eb671-0c8c-7000-8567-58aba3cb677e | observation | 0.82 | glock | Aaron Feickert scheduled 'Starknet Shared Glock Verifier Collaboration — kickoff' for 2025-10-15 09:00 UTC with five Alpen Labs attendees. |
| 019eb671-0e64-7000-ba08-151015da3e7e | prediction | 0.53 | glock | Future plan to verify: aaronfeickert@alpenlabs.io scheduled 'Starknet Shared Glock Verifier Collaboration — kickoff' at 2025-10-15 09:00 UTC with 5 attendee(s): aaronfeickert@alpenlabs.io, hakkush-07@... |
| 019eb674-2fe2-7000-995d-7e3eb1c86e46 | belief | 0.82 | zk | zk2u@alpenlabs.io scheduled 'Zk2u <> Hakkush-07 1:1' for 2025-08-19 11:00 UTC with attendees zk2u@alpenlabs.io and hakkush-07@alpenlabs.io. |
| 019eb674-30a9-7000-8bb7-2eae381a0d04 | prediction | 0.53 | zk | Future plan to verify: zk2u@alpenlabs.io scheduled 'Zk2u <> Hakkush-07 1:1' at 2025-08-19 11:00 UTC with 2 attendee(s): zk2u@alpenlabs.io, hakkush-07@alpenlabs.io |
| 019eb674-a67b-7000-9d90-90729f7c960d | observation | 0.82 | zk | zk2u@alpenlabs.io scheduled 'Zk2u <> AaronFeickert 1:1' for 2025-08-19 11:00 UTC with attendees zk2u@alpenlabs.io and aaronfeickert@alpenlabs.io. |
| 019eb675-668e-7000-88a7-08d9467e474a | observation | 0.82 | zk | A Notion page titled '1:1 — Zk2u & AaronFeickert (2025-08-19)' was observed in database 51b3ea30-689d-4c18-9aff-f9a5bedd5cae. |
| 019eb677-436c-7000-b362-0668e95b904a | belief | 0.82 | zk | In the 2025-08-19 1:1 with Zk2u, Hakkush-07's stated top-of-mind topic was edge cases in algebra. |
| 019eb677-44d4-7000-b272-977c77957819 | belief | 0.82 | zk | Hakkush-07 had edge cases in algebra top of mind during the 2025-08-19 1:1 with Zk2u. |
| 019eb677-45c0-7000-adf8-3a8882ceebdb | belief | 0.82 | zk | Composite situation: In the 2025-08-19 1:1 with Zk2u, Hakkush-07's stated top-of-mind topic was edge cases in algebra. \| Hakkush-07 had edge cases in algebra top of mind during the 2025-08-19 1:1 with Zk2u. |
| 019eb677-46be-7000-aecb-67932e8f98cd | belief | 0.80 | zk | The 2025-08-19 Zk2u and Hakkush-07 1:1 note records an unchecked action item: “looks correct after the transcript change.” |
| 019eb677-c504-7000-b7c6-9d62ae6a0a32 | belief | 0.78 | zk | AaronFeickert 1:1, proof soundness was AaronFeickert's top-of-mind topic. |
| 019eb677-c723-7000-a620-d52f16f47c77 | belief | 0.78 | zk | AaronFeickert had proof soundness top of mind. |
| 019eb677-c7d5-7000-bb3a-a9fad3432db2 | belief | 0.78 | zk | the open action item was to spell out why the relevant proof cannot rewind. |
| 019eb677-c88e-7000-87e9-9f4a9bce2b5a | belief | 0.78 | zk | Composite situation: In the 2025-08-19 Zk2u and AaronFeickert 1:1, proof soundness was AaronFeickert's top-of-mind topic, with an open action item to explain why the proof cannot rewind. \| AaronFeickert had proof sound... |
| 019eb678-45d3-7000-9376-3be86ce5499c | belief | 0.84 | zk | Zk2u 1:1 note, reproducibility was listed as what was top of mind for Zk2u. |

### Starknet Shared Glock (2025-10-15 to 2025-12-04)

Expected ground truth:
- Alpen received / executed a Starknet Foundation grant around October 15, 2025.
- The grant amount was $250K and tied to shared Glock / prover work.

Correctly represented: none found by keyword/evidence matching.

Deviations: none detected by stale/current-state pattern checks.

Missed:
- Alpen received / executed a Starknet Foundation grant around October 15, 2025.
- The grant amount was $250K and tied to shared Glock / prover work.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb66f-7f1d-7000-b1c6-75f4e88542ba | belief | 0.84 | starknet | Simanta reports that the Starknet x Alpen joint announcement is live. |
| 019eb66f-80df-7000-a4fa-a01f2c2a8d9c | belief | 0.84 | starknet | The joint announcement is live as of the signal time. |
| 019eb66f-817c-7000-a12b-1bba774f8593 | belief | 0.84 | starknet | Composite situation: Simanta reports that the Starknet x Alpen joint announcement is live. \| The joint announcement is live as of the signal time. |
| 019eb66f-8269-7000-84d8-38f00647703a | belief | 0.82 | starknet | The signal says Strata and Starknet are the first two chains adopting Glock for trust-minimised BTC bridging. |
| 019eb671-0c8c-7000-8567-58aba3cb677e | observation | 0.82 | starknet, shared glock | Aaron Feickert scheduled 'Starknet Shared Glock Verifier Collaboration — kickoff' for 2025-10-15 09:00 UTC with five Alpen Labs attendees. |
| 019eb671-0e64-7000-ba08-151015da3e7e | prediction | 0.53 | starknet, shared glock | Future plan to verify: aaronfeickert@alpenlabs.io scheduled 'Starknet Shared Glock Verifier Collaboration — kickoff' at 2025-10-15 09:00 UTC with 5 attendee(s): aaronfeickert@alpenlabs.io, hakkush-07@... |
| 019eb67c-0965-7000-bf48-18f76ef21cec | observation | 0.82 | starknet, shared glock | Jira recorded STR-5134 as the Epic 'Starknet Shared Glock Verifier Collaboration' in To Do status with Medium priority. |
| 019eb681-5c3d-7000-b0ae-7be5f86c01ee | belief | 0.90 | starknet, 250 | QuickBooks reports Invoice #INV-dep-09a7024436e1 for Starknet Foundation as paid in full: $250,000.00 amount, $0.00 remaining balance, 1 line. |
| 019eb681-e307-7000-a276-70a27d730123 | belief | 0.90 | starknet, 250 | QuickBooks recorded payment #P-dep-09a7024436e1 from Starknet Foundation for $250,000.00 with one line. |
| 019eb68e-cd12-7000-a5bb-68b38d3ba966 | belief | 0.90 | 250 | Gusto reports Simanta Gautam as an active employee, with title Cofounder and department Operations. |

### Prague Testnet (2025-12-04 to 2026-05-07)

Expected ground truth:
- Prague testnet work started December 4, 2025.
- The phase centers on bridge/testnet reliability and Strata execution through early 2026.
- The model should not freeze company state at December 2025.

Correctly represented:
- Prague testnet work started December 4, 2025.
- The phase centers on bridge/testnet reliability and Strata execution through early 2026.

Deviations:
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: `product:strata-bridge` across the December 2025 rollout period. The source text is truncated after "through ear".
  Reason: stale flags: december 2025
- Expected: Use current full-corpus Alpen state through 2026-06-11.
  Observed: Composite situation: A Notion RFC titled "Prague Testnet Support Scope and Rollout Criteria" defines Prague testnet support scope for `product:strata` and `product:strata-bridge` across the December 2025 rollout period...
  Reason: stale flags: december 2025

Missed:
- The model should not freeze company state at December 2025.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb65f-28bc-7000-9b3d-22e5ebcc7ed6 | belief | 0.74 | bridge | voidash signaled that the bridge design is interesting. |
| 019eb65f-29aa-7000-8af8-60c8ccbf4adc | belief | 0.74 | bridge | but its exit path needs closer scrutiny. |
| 019eb65f-2a75-7000-895a-18bd4a800b90 | belief | 0.74 | bridge | the exit path needs scrutiny despite the bridge design being interesting. |
| 019eb65f-2b5c-7000-8159-c361d79d9b3e | belief | 0.74 | bridge | Composite situation: voidash signaled that the bridge design is interesting, but its exit path needs closer scrutiny. \| the exit path needs scrutiny despite the bridge design being interesting |
| 019eb660-6fb4-7000-bc2a-71bdf02773f9 | belief | 0.82 | bridge, strata | Jose reported that the Strata bridge reference implementation has been open-sourced. |
| 019eb660-7121-7000-bc9e-ab3539c78274 | belief | 0.78 | bridge, strata | walkthrough for the Strata bridge reference implementation are in the thread. |
| 019eb664-d72f-7000-a498-c3edb935e953 | belief | 0.85 | bridge, strata | Rajil1213 merged PR #249, titled 'docs: remove outdated system diagram', into the main branch of alpenlabs/strata-bridge. |
| 019eb665-3d43-7000-9195-0213edea0382 | belief | 0.80 | bridge, strata | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T20:14:42Z. |
| 019eb665-b755-7000-857c-6becdfac0e07 | observation | 0.82 | bridge, strata | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T19:57:56Z. |
| 019eb667-d540-7000-ad8c-8ce4240826fe | belief | 0.72 | bridge, strata | sistemd approved PR #249 in alpenlabs/strata-bridge. |
| 019eb667-d6cc-7000-815f-ec9cb13a5735 | belief | 0.72 | bridge, strata | but the approval still carried a test coverage request: the PR needs one negative test. |
| 019eb667-d83e-7000-92b1-6f3b40f1dc35 | belief | 0.72 | bridge, strata | sistemd approved the review while still requesting one negative test. |
| 019eb667-d9b1-7000-946f-8838cc0da1c4 | belief | 0.72 | bridge, strata | Composite situation: sistemd approved PR #249 in alpenlabs/strata-bridge, but the approval still carried a test coverage request: the PR needs one negative test. \| sistemd approved the review while still requesting one... |
| 019eb668-707f-7000-865d-4ca31d5cae2c | belief | 0.72 | bridge, strata | Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge. |
| 019eb668-7265-7000-82c7-56d445ab1b4f | belief | 0.72 | bridge, strata | Prajwol Gyawali approved the review while indicating the change is acceptable after the boundary check is moved up. |
| 019eb668-7376-7000-ade2-04f25e40401c | belief | 0.72 | bridge, strata | Composite situation: Prajwol Gyawali approved PR #576 in alpenlabs/strata-bridge, with the approval tied to the boundary check moving up. \| Prajwol Gyawali approved the review while indicating the change is acceptable... |
| 019eb66d-fcf0-7000-a50a-c95fa34302e3 | belief | 0.82 | testnet, strata | Alpen reports that Strata testnet hardening is continuing. |
| 019eb66d-fefd-7000-8444-ae2a2288eb12 | belief | 0.82 | testnet, strata | Strata is continuing testnet hardening. |
| 019eb66e-013b-7000-83be-5b22a47ccaac | belief | 0.82 | testnet, bridge, strata | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track. \| Strata is continuing testnet hardening, with bridge throughput and prove... |
| 019eb66e-8ede-7000-b80f-6ebbac9c3bfd | belief | 0.78 | testnet, strata | testnet hardening is continuing. |
| 019eb66e-90b6-7000-a755-fe421d6f5be4 | belief | 0.78 | testnet, bridge, strata | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track as of the June 2026 investor update. \| testnet hardening is continuing, wit... |
| 019eb66f-8269-7000-84d8-38f00647703a | belief | 0.82 | bridge, strata | The signal says Strata and Starknet are the first two chains adopting Glock for trust-minimised BTC bridging. |
| 019eb670-0f2e-7000-88a0-d4794ea60499 | norm | 0.62 | bridge | Because Simanta reports newly in-flight Alpen Labs formation and bridge fundraising work, create a commitment to track that workstream. |
| 019eb672-2508-7000-bb06-7c657c9c0852 | observation | 0.82 | testnet | Prajwol Gyawali scheduled an Alpen Public Testnet Launch kickoff meeting for 2025-08-04 09:00 UTC with four Alpen Labs attendees. |
| 019eb672-27d8-7000-8039-62273aa2505a | norm | 0.56 | testnet | Because the kickoff meeting indicates a new in-flight launch workstream and no matching commitment exists, create a commitment to track the Alpen Public Testnet Launch kickoff. |

### Mosaic Launch And Current State (2026-05-07 to 2026-06-11)

Expected ground truth:
- Mosaic launch occurred May 7, 2026.
- Current run state as of June 11, 2026 has 42 people, 323 Drive files, 187 Jira bugs / 476 Jira issues, and 4,365 AWS events.
- H1 2026 artifacts are present; lower tables in ALPEN_COMPANY_STATE_REPORT are marked pre-fix snapshots.

Correctly represented:
- Current run state as of June 11, 2026 has 42 people, 323 Drive files, 187 Jira bugs / 476 Jira issues, and 4,365 AWS events.
- H1 2026 artifacts are present; lower tables in ALPEN_COMPANY_STATE_REPORT are marked pre-fix snapshots.

Deviations: none detected by stale/current-state pattern checks.

Missed:
- Mosaic launch occurred May 7, 2026.

Matched model-layer beliefs:
| Model | Kind | Confidence | Keyword hits | Observed belief |
| --- | --- | --- | --- | --- |
| 019eb65c-ab1c-7000-8ffa-a08bdaf93258 | belief | 0.66 | 42 | Armin Sabouri reported a keyboard/input issue today: spaces are being omitted intermittently. |
| 019eb65c-abcb-7000-bfd4-8f33034db408 | belief | 0.66 | 42 | Actor reported that their keyboard is intermittently omitting spaces today. |
| 019eb65d-0fe3-7000-837c-30d288d187e7 | belief | 0.62 | 42 | Composite situation: Purushotam Sangroula reported that his apartment wifi was behaving unreliably, using the phrase "doing rpc cosplay." \| reported that their apartment wifi was behaving unreliably in a way likened to... |
| 019eb65f-ea70-7000-b99a-0ee294eec712 | belief | 0.80 | 42 | Composite situation: Fixture #14 in the shared verifier suite still fails when the curve order is non-prime. \| still trips when the curve order is non-prime |
| 019eb665-3d43-7000-9195-0213edea0382 | belief | 0.80 | 42 | Christian Lewe / uncomputable pushed 1 commit to an unknown branch in alpenlabs/strata-bridge at 2025-12-04T20:14:42Z. |
| 019eb667-d9b1-7000-946f-8838cc0da1c4 | belief | 0.72 | 42 | Composite situation: sistemd approved PR #249 in alpenlabs/strata-bridge, but the approval still carried a test coverage request: the PR needs one negative test. \| sistemd approved the review while still requesting one... |
| 019eb66b-7f19-7000-86b0-389fb1615730 | belief | 0.78 | 42 | Jay reported that the wire was initiated on 2024-04-09 with reference ALP-SEED-GEOMETRY. |
| 019eb66b-8713-7000-9b92-27a233815ef1 | belief | 0.72 | 42 | Jay said the signed PA for the Alpen seed transaction was on its way separately. This indicates the wire. |
| 019eb66d-029f-7000-b8b6-ec91679111d1 | belief | 0.65 | 42 | Alyse says the signed PA is being sent separately from the wire confirmation. |
| 019eb66e-90b6-7000-a755-fe421d6f5be4 | belief | 0.78 | 2026 | Composite situation: Alpen reports that Strata testnet hardening is continuing and that bridge throughput and prover time targets are on track as of the June 2026 investor update. \| testnet hardening is continuing, wit... |
| 019eb66e-9477-7000-91f7-216fc39e740b | belief | 0.76 | 2026 | Alpen reports runway comfortably above 18 months at current burn in the June 2026 investor update. |
| 019eb66f-817c-7000-a12b-1bba774f8593 | belief | 0.84 | 42 | Composite situation: Simanta reports that the Starknet x Alpen joint announcement is live. \| The joint announcement is live as of the signal time. |
| 019eb66f-8269-7000-84d8-38f00647703a | belief | 0.82 | 42 | The signal says Strata and Starknet are the first two chains adopting Glock for trust-minimised BTC bridging. |
| 019eb671-6e88-7000-bc52-aae21655e1e5 | belief | 0.82 | 2026 | chhetri22@alpenlabs.io scheduled 'chhetri22 <> chandansharmasubedi 1:1' for 2026-06-09 15:00 UTC with chhetri22@alpenlabs.io and chandansharmasubedi@alpenlabs.io as attendees. |
| 019eb671-7162-7000-aed2-ce02794a7642 | prediction | 0.53 | 2026 | Future plan to verify: chhetri22@alpenlabs.io scheduled 'chhetri22 <> chandansharmasubedi 1:1' at 2026-06-09 15:00 UTC with 2 attendee(s): chhetri22@alpenlabs.io, chandansharmasubedi@alpenlabs.io |
| 019eb672-c369-7000-b482-0abe7eba3ef1 | norm | 0.58 | 2026 | The Prague Testnet Support kickoff warrants creating a commitment record owned by Prajwol Gyawali because no matching commitment exists in the provided Acts context. |
| 019eb673-c089-7000-b2f0-3d3e9a145e90 | belief | 0.85 | 2026 | Rajil Bajracharya scheduled 'Rajil1213 <> uncomputable 1:1' at 2026-06-09 15:00 UTC with rajil1213@alpenlabs.io and uncomputable@alpenlabs.io as attendees. |
| 019eb673-c2d2-7000-81fb-18095c0be461 | prediction | 0.53 | 2026 | Future plan to verify: rajil1213@alpenlabs.io scheduled 'Rajil1213 <> uncomputable 1:1' at 2026-06-09 15:00 UTC with 2 attendee(s): rajil1213@alpenlabs.io, uncomputable@alpenlabs.io |
| 019eb675-668e-7000-88a7-08d9467e474a | observation | 0.82 | 42 | A Notion page titled '1:1 — Zk2u & AaronFeickert (2025-08-19)' was observed in database 51b3ea30-689d-4c18-9aff-f9a5bedd5cae. |
| 019eb676-542e-7000-92b5-4ecdab9f4c7e | belief | 0.86 | 2026 | A Notion page titled '1:1 — chhetri22 & chandansharmasubedi (2026-06-09)' was observed in database 51b3ea30-689d-4c18-9aff-f9a5bedd5cae. |
| 019eb676-cc1b-7000-ba47-e2030b3d81f9 | belief | 0.81 | 2026 | A Notion page for the 2026-06-09 1:1 between Rajil1213 and uncomputable was observed in database 51b3ea30-689d-4c18-9aff-f9a5bedd5cae. |
| 019eb677-436c-7000-b362-0668e95b904a | belief | 0.82 | 42 | In the 2025-08-19 1:1 with Zk2u, Hakkush-07's stated top-of-mind topic was edge cases in algebra. |
| 019eb677-c88e-7000-87e9-9f4a9bce2b5a | belief | 0.78 | 42 | Composite situation: In the 2025-08-19 Zk2u and AaronFeickert 1:1, proof soundness was AaronFeickert's top-of-mind topic, with an open action item to explain why the proof cannot rewind. \| AaronFeickert had proof sound... |
| 019eb678-45d3-7000-9376-3be86ce5499c | belief | 0.84 | 42 | Zk2u 1:1 note, reproducibility was listed as what was top of mind for Zk2u. |
| 019eb678-b1d7-7000-ae0e-4fc633124484 | belief | 0.68 | 42 | Composite situation: A Notion RFC exists for the strategic round technical diligence room. It defines scope, ownership, and review path, with product:strata explicitly included in the covered product areas. \| A Notion... |

## Cross-Phase Findings

- The ingestion substrate successfully produced a full Fyralis observation set across every enabled mock source that did not encounter a source-specific fetch/handler error.
- Correctness classification is deliberately conservative: a belief is marked correct only when a generated model's natural text or proposition includes enough phase-specific keywords to support the expected claim.
- Deviations focus on stale-state hazards from the pre-fix ALPEN report tables: December-2025 freeze, wrong headcount, wrong Drive/Jira/AWS counts, or absence of 2026/Mosaic state.
- Misses indicate either the sampled Think set did not cover that fact strongly enough, or Think observed it but did not persist a durable model with phase-specific language.

## Raw Artifacts

- `run_summary.json`: local ignored artifact at `model_layer_run_artifacts/run_summary.json`
- `models.json`: local ignored artifact at `model_layer_run_artifacts/models.json`
- `source_runs.json`: local ignored artifact at `model_layer_run_artifacts/source_runs.json`
- `phase_classification.json`: local ignored artifact at `model_layer_run_artifacts/phase_classification.json`

## Human-Audited Phase Assessment

This section supersedes the conservative keyword classifier above where the classifier is too literal or too permissive. I audited the generated models directly against the ground-truth company state in `ALPEN_COMPANY_STATE_REPORT.md`, with the remediation block at the top treated as authoritative over the older pre-fix tables.

Run verdict:

- The end-to-end pipeline completed: ingestion, Think, model layer writes, post-commit processing, and topology processing all ran to completion.
- All 25 mock sources ingested with zero source errors in the final run.
- Final source observations included the full modern corpus scale: 42-person Hibob/Gusto/Deel-style people surface, 323 Google Drive files, 1,325 Jira records with the expected 187 bug / 476 issue corpus, and 4,365 AWS events.
- Think produced 125 successful runs and 1 contained validation failure. The failed run was a Hibob approved time-off state-change model rejected by validation. It did not leave pending post-commit or topology work.
- The model layer created 209 final models. Post-commit processed 153 jobs with 0 failed and 0 dead-lettered. Topology processed 125 jobs with 0 failed.
- The model layer did not appear frozen at the older December 2025 / pre-fix state. It created June 2026 beliefs and current operational observations. However, it did not synthesize several high-level phase summaries or corpus aggregate counts that are present in the raw ingested corpus.

### Phase Matrix

| Phase | Ground truth | Correct model-layer beliefs | Deviated or weak beliefs | Missed or not durably represented |
| --- | --- | --- | --- | --- |
| Founding / stealth | Alpen founded 2024-02-01 by four cofounders around Bitcoin-native financial infrastructure, ZK, Strata/BitVM direction. | Captured Simanta spinning up Alpen Labs with three MIT cofounders to build Bitcoin-native finance infrastructure with ZK; captured individual cofounder employment/payroll records for Simanta, Chandan, Abishkar, and Pramod. | Formation is represented as "spinning up" and personnel facts rather than a dated legal/company founding event. Stealth, Strata, and BitVM are not unified with the founding belief. | Exact founding date 2024-02-01; explicit "four cofounders" as a canonical entity set; stealth-state summary; BitVM founding thesis. |
| Public launch / seed | Public launch on 2024-04-09; $10.6M seed led by Ribbit; Strata as core Bitcoin/ZK rollup/verification direction. | Captured several 2024-04-09 seed wire/payment facts, including Ribbit, Geometry, and Stillmark wire activity and signed PA/wire-instruction workflow. | Ribbit appears mainly as a wire reference, not as "seed lead." The model represents transaction mechanics better than company narrative. Strata exists in many engineering models but not as the launch thesis. | Public launch as a durable milestone; $10.6M total; "led by Ribbit"; coherent launch/seed summary connected to Strata. |
| Strategic round / testnet ramp | 2025-01-09 $8.5M strategic round led/involving DBA and Cyber; technical diligence; movement toward testnet/bridge execution and operational expansion. | Captured Strategic Round Technical Diligence kickoff on 2025-01-09, a Notion diligence room/RFC with Strata in scope, and Jira Epic STR-5728 for the diligence workstream. | The system inferred diligence/workstream activity but not the financing outcome. Bridge/testnet direction appears elsewhere, but is not strongly tied to the strategic round. | $8.5M amount; DBA/Cyber investors; strategic round as completed financing; hiring/ops expansion as phase-level consequence. |
| Public testnet | Public testnet launch on 2025-08-04. | Captured Public Testnet Launch kickoff scheduled for 2025-08-04 09:00 UTC, a commitment to track the launch workstream, Jira Epic STR-7721, and Strata testnet hardening / bridge throughput / prover-time progress. | The model often phrases kickoff events as future plans to verify, even when the as-of date is 2026-06-11 and the event date is historical. It captures launch preparation more strongly than "testnet is live." | Durable phase belief that Alpen's public testnet launched on 2025-08-04. |
| Glock public release | Glock public release on 2025-08-19; ZK/prover component; linked to trust-minimized BTC bridging and Strata/Starknet adoption. | Captured Glock Public Release kickoff on 2025-08-19, Jira Epic STR-3246, proof-soundness/prover context in adjacent 1:1 notes, and a belief that Strata and Starknet are first two chains adopting Glock for trust-minimized BTC bridging. | Strong evidence is split across several atomic models. "Release" is mostly a kickoff/epic, not a completed public release. Component semantics are implied through prover/proof/bridge context, not clearly explained. | Durable belief that Glock was publicly released; direct explanation that Glock is the relevant ZK/prover/verifier component. |
| Starknet Shared Glock | Starknet Shared Glock collaboration on 2025-10-15; $250K Starknet Foundation grant/payment tied to shared Glock/prover work. | Captured Starknet Shared Glock Verifier Collaboration kickoff on 2025-10-15, Jira Epic STR-5134, joint Starknet x Alpen announcement, first-chain adoption of Glock by Strata/Starknet, and $250,000 Starknet Foundation invoice/payment records. | The financial and technical facts are not unified. The $250K appears as an invoice/payment, not explicitly as a grant, and not explicitly tied in one model to Shared Glock/prover work. | A single coherent belief: Starknet Foundation awarded/funded $250K for Shared Glock/prover collaboration. |
| Prague testnet | Prague Testnet Support on 2025-12-04; bridge/testnet reliability and Strata execution through early 2026. | Captured Prague Testnet Support kickoff on 2025-12-04, Jira Epic STR-3074, Notion scope/rollout criteria for product:strata and product:strata-bridge, and same-day Strata bridge deployment activity. | One Notion-derived belief is truncated around the rollout period. Prague-specific December 2025 models are valid for that phase and should not be mistaken for stale-current-state evidence. | Broader synthesized statement that Prague support continued into early 2026 with reliability/bridge execution outcomes. |
| Mosaic / current state | Mosaic launch on 2026-05-07; current as of 2026-06-11 with 42 people, 323 Drive files, 187 Jira bugs / 476 issues, 4,365 AWS events, and H1 2026 artifacts. | Captured many June 2026 artifacts: investor update, runway above 18 months, Strata testnet hardening, Drive sprint notes, Fireflies meetings, 2026 calendar/Notion/Gusto/Brex/Deel/Ramp/AWS/Jira observations. Captured a Mosaic Jira bug and June 2026 state changes. | The machine classifier's "42" matches were false positives from timestamps and unrelated text, not real headcount beliefs. The model layer shows current artifacts but not current aggregate state. | Mosaic launch on 2026-05-07; explicit 42-person current headcount; explicit 323 Drive-file corpus; explicit Jira aggregate counts of 187 bugs / 476 issues; explicit 4,365 AWS-event aggregate. |

### Founding / Stealth Details

Expected:

- Alpen Labs was founded on 2024-02-01.
- The founding team had four cofounders.
- The original thesis was Bitcoin-native financial infrastructure, with ZK and the Strata/BitVM direction as the core technical frame.

Correct observed beliefs:

- `019eb670-0db3-7000-97f6-6befaf3fb0d7`: Simanta says he is spinning up Alpen Labs with three MIT cofounders to build Bitcoin-native finance infrastructure with ZK.
- `019eb670-0e7b-7000-92a0-58552b269687`: Composite belief repeats the Alpen Labs formation, three MIT cofounders, Bitcoin-native finance infrastructure, and ZK.
- Gusto/personnel-derived models capture cofounder records for Chandan (`019eb68d-638d...`), Abishkar (`019eb68d-e7af...`, `019eb68d-e890...`), Pramod (`019eb68e-5096...`), and Simanta (`019eb68e-cd12...`).

Deviations:

- The model layer treats founding mostly as a communication/personnel fact. It does not elevate it to a canonical company-state event.
- "Three MIT cofounders" plus Simanta implies four founders, but no model cleanly says "four cofounders."

Missed:

- Exact foundation date.
- Stealth-state framing.
- BitVM and Strata as founding-era thesis, not just later engineering artifacts.

### Public Launch / Seed Details

Expected:

- Alpen publicly launched on 2024-04-09.
- The seed round was $10.6M and led by Ribbit.
- Strata was the public technical direction.

Correct observed beliefs:

- `019eb668-e04b...`: signed PA received for Alpen seed.
- `019eb668-e0c9...`: wire instructions on file.
- `019eb66b-7d02...`: Geometry seed wire initiated on 2024-04-09 with `ALP-SEED-GEOMETRY`.
- `019eb66c-0bd9...`: Micky reported `ALP-SEED-RIBBIT` wire initiated on 2024-04-09.
- `019eb66d-003a...`: Stillmark seed wire initiated on 2024-04-09.

Deviations:

- The model layer learned the payment choreography and investor names, but not the announcement-level state.
- Ribbit is present as a wire participant/reference; the model does not say Ribbit led the round.

Missed:

- Public launch milestone.
- $10.6M seed amount.
- Round leadership.
- A company-level belief connecting launch, seed, and Strata.

### Strategic Round / Testnet Ramp Details

Expected:

- On 2025-01-09, Alpen had an $8.5M strategic round involving DBA and Cyber.
- The phase involved technical diligence, movement toward testnet and bridge execution, and company scaling.

Correct observed beliefs:

- `019eb673-4160...`: Strategic Round Technical Diligence kickoff on 2025-01-09.
- `019eb678-b1d7...`: Notion RFC for strategic-round technical diligence room, with scope, ownership, review path, and product:strata included.
- `019eb67a-77c1...`: Jira Epic STR-5728, Strategic Round Technical Diligence, To Do on 2025-01-09.

Deviations:

- The model captured diligence as work, not financing as a completed business event.
- Testnet and bridge work are correctly represented elsewhere, but not causally tied to the strategic financing phase.

Missed:

- $8.5M.
- DBA/Cyber.
- Financing outcome and expansion summary.

### Public Testnet Details

Expected:

- Alpen public testnet launched on 2025-08-04.

Correct observed beliefs:

- `019eb672-2508...`: Public Testnet Launch kickoff scheduled for 2025-08-04 09:00 UTC.
- `019eb672-27d8...`: commitment to track the launch workstream.
- `019eb672-2a03...`: future plan to verify the kickoff.
- `019eb67b-117e...`: Jira STR-7721, Alpen Public Testnet Launch epic.
- `019eb66d-fcf0...`: Strata testnet hardening continuing.
- `019eb66e-013b...`: bridge throughput and prover time targets on track.

Deviations:

- The "future plan to verify" pattern is temporally awkward. The run is as-of 2026-06-11, but the model still writes verification plans for 2025 dated events.
- The model stops short of saying the testnet launched.

Missed:

- Completed/live launch state.

### Glock Public Release Details

Expected:

- Glock public release happened on 2025-08-19.
- Glock is part of the ZK/prover/verifier story and connected to trust-minimized BTC bridging.

Correct observed beliefs:

- `019eb670-992c...`: Glock Public Release kickoff on 2025-08-19.
- `019eb670-9b3c...`: future plan to verify the kickoff.
- `019eb67b-8324...`: Jira STR-3246, Glock Public Release epic.
- `019eb66f-8269...`: Strata and Starknet are first two chains adopting Glock for trust-minimized BTC bridging.
- 1:1/proof-soundness models around Aaron Feickert and ZK/prover context provide adjacent technical evidence.

Deviations:

- The model has a release workstream, not a release accomplishment.
- It understands adoption/bridge relevance, but does not explain Glock as the component in a clean durable model.

Missed:

- Completed public release.
- Clear ZK/prover/verifier component statement.

### Starknet Shared Glock Details

Expected:

- Starknet Shared Glock collaboration started on 2025-10-15.
- The Starknet Foundation $250K grant/payment was tied to Shared Glock/prover work.

Correct observed beliefs:

- `019eb671-0c8c...`: Starknet Shared Glock Verifier Collaboration kickoff on 2025-10-15.
- `019eb671-0e64...`: future plan to verify the kickoff.
- `019eb67c-0965...`: Jira STR-5134, Starknet Shared Glock Verifier Collaboration.
- `019eb66f-7f1d...`: Starknet x Alpen joint announcement live.
- `019eb66f-8269...`: Strata and Starknet first two chains adopting Glock for trust-minimized BTC bridging.
- `019eb681-5c3d...`: QuickBooks invoice for Starknet Foundation paid in full for $250,000.
- `019eb681-e307...`: QuickBooks payment from Starknet Foundation for $250,000.

Deviations:

- The model does not join the payment and collaboration into one explanation.
- The funding is represented as invoice/payment, not as a grant.

Missed:

- Explicit Starknet Foundation grant semantics.
- Explicit linkage between the $250K and Shared Glock/prover work.

### Prague Testnet Details

Expected:

- Prague Testnet Support began on 2025-12-04.
- The work involved Strata, Strata bridge, rollout criteria, and reliability through early 2026.

Correct observed beliefs:

- `019eb672-c2b0...`: Prague Testnet Support kickoff on 2025-12-04.
- `019eb672-c369...`: commitment record for Prague Testnet Support kickoff.
- `019eb672-c3f6...`: future plan to verify Prague Testnet Support kickoff.
- `019eb67c-6f2d...`: Jira STR-3074, Prague Testnet Support epic.
- `019eb679-1715...`: Notion RFC "Prague Testnet Support Scope and Rollout Criteria" for product:strata and product:strata-bridge.
- `019eb683-4f86...`: Grafana production deployment `strata-bridge` v2.18.2 on 2025-12-04.

Deviations:

- One Notion-derived belief truncates the rollout-period text, so the early-2026 continuation is not fully captured.
- December 2025 facts are valid for this phase. They should not be interpreted as evidence of a stale current-state corpus by themselves.

Missed:

- A synthesized early-2026 reliability/execution summary.

### Mosaic / Current-State Details

Expected:

- Mosaic launched on 2026-05-07.
- Current run state as of 2026-06-11 has 42 people, 323 Drive files, 187 Jira bugs / 476 Jira issues, and 4,365 AWS events.
- H1 2026 artifacts are present and should override the pre-fix lower-table snapshots in `ALPEN_COMPANY_STATE_REPORT.md`.

Correct observed beliefs:

- `019eb66e-90b6...`: June 2026 investor update says Strata testnet hardening continues and bridge throughput/prover-time targets are on track.
- `019eb66e-9477...`: June 2026 investor update says runway is comfortably above 18 months at current burn.
- `019eb679-8388...`: Drive doc "Infra - Sprint Notes 2026-06."
- `019eb679-fe2c...`: Drive doc "Bridge - Sprint Notes 2026-06."
- `019eb693-3442...`: Fireflies Engineering Standup on 2026-06-08.
- `019eb693-af06...`: Fireflies customer OKR check-in on 2026-06-05.
- `019eb67c-dc2b...`: Jira STR-30168, "Mosaic garbling tables non-reproducible across runs," Medium Bug Done.
- `019eb67d-48f2...`: Mukesh moved STR-30168 from In Progress to Done on 2026-06-10.
- AWS models exist for 2026-05-07 operational activity, including `ec2:CreateImage` and monitoring alarm state changes.

Deviations:

- The automated classifier marked unrelated models as "correct" for 42 people because `42` appeared in timestamps, record identifiers, or arbitrary text. Those are false positives.
- The model layer captures current examples, not corpus-level current aggregates.
- Mosaic appears only as a Jira bug term, not as a product launch.

Missed:

- Mosaic launch on 2026-05-07.
- Current aggregate headcount of 42.
- Current aggregate Google Drive file count of 323.
- Current aggregate Jira bug/issue counts of 187 bugs and 476 issues.
- Current aggregate AWS event count of 4,365.

## Cross-Cutting Model-Layer Behavior

Correct strengths:

- The model layer is receiving and modeling current H1 2026 evidence. It is not generally stuck on the pre-fix December 2025 corpus.
- It is strong on operational and transactional facts: wires, invoices, meetings, Jira epics, GitHub events, Drive/Notion observations, deployments, payroll/personnel records, and source-specific state changes.
- It can compose local composite situations when related facts are present in the same signal context.
- Topology processing completed without failures and wrote structural models, useful nodes/paths, shortcuts, affordance reinforcements, and negative-memory records.

Recurring deviations:

- Phase-level synthesis is weak. The model layer frequently preserves the exact local artifact but does not promote it into a company-history milestone.
- Temporal language is weak for historical scheduled events. Several 2025 events are modeled as future verification plans despite a 2026-06-11 run date.
- Financing semantics are weaker than payment mechanics. The system sees wires, invoices, and payments, but misses round totals, leads, grants, and strategic meaning.
- Aggregate corpus state is not synthesized. Counts like people, Drive files, Jira bugs/issues, and AWS events are visible in ingestion/run artifacts but not represented as durable company beliefs.
- Some models are noisy duplicates or atomized fragments. This is expected from a sampled model-layer run but limits direct company-state readability.

Missed categories:

- Exact milestone dates when they appear as company events rather than artifact timestamps.
- Investor/round metadata: $10.6M seed, Ribbit lead, $8.5M strategic round, DBA/Cyber, Starknet grant semantics.
- Product launch statements: public launch, public testnet live, Glock released, Mosaic launched.
- High-level technical narrative: Strata/BitVM founding thesis, Glock as prover/verifier component, early-2026 Prague reliability outcome.
- Current-state aggregates: 42 people, 323 Drive files, 187 Jira bugs, 476 Jira issues, and 4,365 AWS events.

## Final Assessment

The full Alpen corpus now flows through the full Fyralis pipeline, and the model layer produces live, current, source-grounded beliefs without the earlier ingestion bottlenecks. The model layer's main gap is not ingestion coverage or topology execution; it is abstraction. It reliably captures the operational evidence underneath Alpen's history, but it under-promotes that evidence into durable, executive-level company-state beliefs. For future model-layer evaluation, the best next tests are phase-summary prompts/triggers, aggregate-state model generation, and temporal normalization for historical events.
