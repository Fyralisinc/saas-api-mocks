# Alpen Labs — Synthetic Company State Report

*Generated from the live `mock_orgs` database (all 25 seeded sources) + `corpus/facts/*`.*

---

## ✅ REMEDIATION APPLIED — 2026-06-11

The gaps below were fixed; the company is now re-seeded at **`virtual_now = 2026-06-11`**
(was 2026-01-01). The narrative sections further down describe the *pre-fix* snapshot;
this block records what changed.

| # | Issue | Before | After |
|---|---|---|---|
| 1 | Timeline frozen | data stopped **Dec 2025** | runs through **Jun 2026** (Mosaic launch, BTC Credit Markets, Duty-Free Bits, 2026 releases now present) |
| 1 | H1-2026 hires | 2 | **6** — added Sabin Thapa (Jan), Nisha Maharjan (Feb), Roshan KC (Mar), Lena Fischer (Apr, EU); headcount 38→**42**, with recruiter fees + payroll + onboarding footprint |
| 2 | Google Drive | 29 files | **323 files** — per-team design docs/specs, monthly sprint notes, runbooks, quarterly trackers, launch one-pagers |
| 3 | Jira bugs / issues | 6 bugs / 245 issues | **187 bugs / 476 issues** — biweekly per-team defect stream + ~monthly incidents, realistic P1–P4 mix & fix-latency |
| 4 | (kept) 3 payroll systems | — | unchanged by request (all tested) |
| 5 | Comms adoption dates | rolling `now−430d` artifact (Oct 2024 start, slides/loses history on advance) | **fixed adoption dates**: Signal Mar'24, Telegram Jun'24, LinkedIn Apr'24, Fireflies Jan'25 — no pre-founding data; history now accumulates forward |
| 6 | AWS realism | ~17 mgmt events/day | **~50/day (4,365 over the 90-day window)**, rides `at` forward → now shows Mar–Jun 2026 |
| 7 | GitHub commit skew | flattened (Rajil 391, top clustered ~390) | **real power-law restored** (Rajil 890, dependabot 755, prajwol 618, storopoli 534…) — this was purely the old horizon truncating heavy-hitters' 2026 commits |

**What changed in code:** `corpus/scripts/05_render_events.py` (Drive generators, `sprint_bug_events`,
incident frequency, `_has_started` guard), `corpus/facts/facts.yaml` + `patterns.yaml` (4 hires),
seeders `spammers/{telegram,signal,linkedin,fireflies}/seed.py` (adoption anchors) and
`spammers/aws/seed.py` (volume). Re-rendered `events.jsonl`, reset + re-prepared at
`AS_OF=2026-06-11`. **All 648 tests pass.** (8 pre-existing `thread_events` warnings remain —
thread specs assigning a beat before the assignee's join date — unrelated to these changes.)

---

*Pre-fix snapshot below — simulated "present" was 2026-01-01 (`virtual_now`).*

---

## 1. Executive summary

The spammer is modelling **Alpen Labs** — a Bitcoin financial-infrastructure startup
(Strata rollup, Glock SNARK verifier, Mosaic garbled circuits), founded **Feb 2024**,
HQ NYC + Kathmandu team. The synthetic record is **coherent and, for the core sources,
genuinely Alpen-shaped**:

- **Funding:** 4 rounds totalling **$20.85M** at the real dates/amounts/investors.
- **Headcount:** grows **4 → 35** with a realistic protocol-heavy org.
- **Cash:** ends the sim at **~$14.7M** with ~30-month runway; burn ramps $75K → $470K/mo.
- **Engineering:** 4,046 commits / 2,389 PRs across the real `alpenlabs` repos, real handles.
- **Behavioural "deviations"** (ship-lag, review thoroughness, standup attendance, weekend
  activity) are encoded per-person, and ops is textured (deploys, alerts, conferences, audits).

**The headline problem is the clock, not the content.** Almost every source **stops dead at
Dec 2025**. The real Alpen's entire 2026 chapter — the **Mosaic public launch (2026-05-07)**,
Bitcoin Dollar / BTC Credit Markets, Duty-Free Bits, the `asm` repo, ~10 more hires through
May 2026 — is **absent**. Anyone reading this data concludes Alpen's newest milestone is the
*Prague testnet (Dec 2025)*. The sim is frozen ~6 months behind reality.

Secondary gaps where the data does **not** resemble a real Alpen: **Google Drive is
implausibly empty (29 files)**, **Jira is thin & nearly bug-free (245 issues / 6 bugs)**,
**three redundant payroll systems** (Gusto + Deel + HiBob, identical 36 employees) plus
three finance rails coexist, comms/social sources **only begin Oct 2024**, and the GitHub
commit distribution is **flattened** vs the real power-law. Details in §6.

---

## 2. Company snapshot (as of the sim's Dec-2025 horizon)

| | |
|---|---|
| Mission | "Bitcoin's own financial system — borrow, earn, spend in dollars with Bitcoin" |
| Founded | 2024-02-01 · 4 cofounders (Simanta CEO, Pramod CTO, Abishkar COO, Chandan) |
| Headcount | 4 → **35** (Feb '24 → Jan '26); 38 people on file, 2 modelled departures |
| Teams | Protocol 13 · Research 10 · Bridge 5 · Exec 4 · Infra 3 · Ops 2 · DevRel 1 |
| Total raised | **$20.85M** (founders $1.5M, seed $10.6M, strategic $8.5M, grant $0.25M) |
| Cash @ sim-end | **~$14.7M** · burn ~$350–470K/mo · runway ~30 months |
| Products | Strata (rollup), Glock (SNARK verifier), Mosaic (garbled circuits), Strata Bridge |
| Code | 35 repos, 4,046 commits, 2,389 PRs, 1,088 reviews — all Rust-heavy, real handles |

---

## 3. Month-by-month breakdown

`HC`=headcount · `Cash`=cash at month-end ($M) · `Auth`=active GitHub authors ·
`Commits`/`PRs` · `Slack`=msgs · `Jira`=issues created · `Inc`=grafana alerts/deploys ·
`Raised`/`Burn` in $K.

### 2024 — stealth → seed → first hires

| Month | HC | Raised | Burn | Cash | Auth | Commits | PRs | Slack | Jira | Inc | What's happening |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| 2024-02 | 4 | 1,500 | 75 | 1.42 | 0 | 0 | 0 | 240 | 0 | 34 | **Founders' capital $1.5M.** Slack workspace stood up; founders only |
| 2024-03 | 4 | — | 75 | 1.35 | 0 | 0 | 0 | 240 | 0 | 27 | Stealth build; no public repos yet |
| 2024-04 | 4 | 10,600 | 79 | 11.87 | 0 | 0 | 5 | 250 | 5 | 41 | **Public launch (blog) + $10.6M seed (Ribbit lead).** Jira opens |
| 2024-05 | 7 | — | 235 | 11.64 | 3 | 69 | 10 | 312 | 6 | 23 | `alpen` repo created; first eng hires; payroll steps up |
| 2024-06 | 9 | — | 259 | 11.38 | 8 | 104 | 7 | 275 | 7 | 40 | **Summer offsite ($95K).** Team doubling |
| 2024-07 | 11 | — | 190 | 11.19 | 9 | 219 | 40 | 314 | 8 | 27 | BTC++ Berlin (8 attendees). PR throughput rising |
| 2024-08 | 11 | — | 164 | 11.02 | 8 | 296 | 57 | 325 | 5 | 32 | Heads-down protocol work |
| 2024-09 | 12 | — | 156 | 10.87 | 10 | 265 | 80 | 337 | 2 | 35 | CoreWeave GPU spend begins (ZK proving) |
| 2024-10 | 16 | — | 188 | 10.68 | 14 | 238 | 124 | 393 | 5 | 29 | **Big hiring month (+4).** Discord community opens |
| 2024-11 | 17 | — | 241 | 10.44 | 10 | 296 | 68 | 432 | 4 | 33 | Strata Bridge work ramps |
| 2024-12 | 17 | — | 297 | 10.14 | 12 | 84 | 85 | 390 | 3 | 31 | **Winter offsite.** bbist departs. Holiday commit dip |

### 2025 — strategic round → testnet → Glock → Prague

| Month | HC | Raised | Burn | Cash | Auth | Commits | PRs | Slack | Jira | Inc | What's happening |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|---|
| 2025-01 | 19 | 8,500 | 325 | 18.32 | 18 | 169 | 150 | 465 | 16 | 31 | **Strategic round $8.5M (DBA + Cyber Fund).** Jira adoption jumps |
| 2025-02 | 20 | — | 300 | 18.02 | 18 | 202 | 116 | 466 | 18 | 23 | BTC++ Asia (10 attendees). Legal/insurance step up post-round |
| 2025-03 | 20 | — | 240 | 17.78 | 18 | 191 | 149 | 447 | 16 | 34 | Steady delivery |
| 2025-04 | 20 | — | 226 | 17.55 | 17 | 188 | 127 | 496 | 18 | 38 | — |
| 2025-05 | 24 | — | 250 | 17.30 | 22 | 176 | 145 | 525 | 12 | 18 | **Hiring wave (+4).** Research team expanding (garbled circuits) |
| 2025-06 | 28 | — | 439 | 16.86 | 21 | 171 | 178 | 469 | 3 | 32 | **Summer offsite + comp step-ups.** Burn jumps |
| 2025-07 | 29 | — | 353 | 16.51 | 21 | 211 | 208 | 599 | 12 | 31 | Pre-testnet push; PRs peak |
| 2025-08 | 31 | — | 342 | 16.17 | 20 | 123 | 152 | 638 | 13 | 41 | **Public testnet (Aug 4) + Glock release (Aug 19).** Compute → $42K/mo |
| 2025-09 | 32 | — | 496 | 15.67 | 23 | 203 | 165 | 592 | 21 | 36 | **First security audit ($80K).** ZK Day/Devcon. ProofOfKeags departs |
| 2025-10 | 32 | 250 | 364 | 15.56 | 20 | 179 | 157 | 768 | 30 | 32 | **Starknet collab + $250K grant.** Jira/Slack peak; Mosaic repo |
| 2025-11 | 34 | — | 350 | 15.21 | 22 | 300 | 168 | 654 | 18 | 44 | Most incidents of any month; pre-Prague crunch |
| 2025-12 | 34 | — | 473 | 14.73 | 24 | 362 | 203 | 689 | 23 | 28 | **Prague testnet support (Dec 4).** Highest commit + PR month |

### 2026 — **essentially missing**
`2026-01`: 7 events total. `2026-03`: 1. Then nothing. The real Alpen's Mosaic launch
(May 2026), BTC Credit Markets, Duty-Free Bits, and ~10 H1-2026 hires are **not represented**.

---

## 4. People patterns

- **Trajectory:** 4 founders → 35 employees over 23 months. Hiring clusters: Oct '24 (+4),
  May '25 (+4), Jun '25 (+4) — each precedes a delivery push (community launch, testnet, Glock).
- **Org shape is realistic** for a protocol startup: engineering + research dominate (29 of
  38); single DevRel; tiny ops; 4-person exec.
- **Behavioural "deviations" are encoded per-person** (`corpus/facts/patterns.yaml`) and surface
  in Jira ship-lag and message timing:
  - **Ship-lag** spans **−12h (ships early) to +62h (chronically late)** — e.g. Rajil −12.2,
    Simanta +62.7, ceyhunsen +62.4, purusang +45.5.
  - **Review thoroughness** 0.50–0.92 (Zk2u 0.53 and the cofounders ~0.5–0.6 rubber-stamp;
    AaronFeickert/Rajil ~0.91 are rigorous).
  - **Standup attendance** 0.75–1.00; **weekend activity** 0.18–0.89 (MdTeach 0.89, Zk2u 0.72
    are weekend-heavy; most ICs 0.2–0.4).
  - **Message peak hours** cluster at 13–15 (Nepal daytime) vs 19–23 (a late-night cohort) —
    a genuine two-timezone signature.
- **Attrition is under-modelled (a fidelity gap):** only **2 people** carry an `ended_at`
  (bbist Dec '24, ProofOfKeags Sep '25). But ~8–10 short-tenure contributors (ceyhunsen,
  aunth, cyphersnake, just-erray, ali-rezai, Arniiiii, sampkaALP, Hakkush-07, sistemd) have
  `last_active` months before the scrape and clearly left — without `ended_at` they're counted
  as still employed, so headcount grows almost monotonically and **overstates retention**.

---

## 5. Funding & resource usage

**Rounds (all dates/amounts/investors match the real Alpen):**

| Date | Round | Amount | Lead |
|---|---|--:|---|
| 2024-02-15 | Founders' capital | $1.5M | Founders + angels (Davenport, Pfeffer, Wall) |
| 2024-04-10 | Seed | $10.6M | Ribbit Capital (+ Castle Island, Robot, Stillmark, Paxos…) |
| 2025-01-09 | Strategic | $8.5M | DBA + Cyber Fund (+ Ribbit, Castle Island returning) |
| 2025-10-15 | Starknet grant | $0.25M | Starknet Foundation |

**Spend by category (all-time, QuickBooks):**

| Category | Total | Notes |
|---|--:|---|
| Payroll | **$3.14M** | 432 monthly lines — the dominant cost, scales with headcount |
| Compute | $855K | AWS + Quicknode + CoreWeave; steps $18K→$42K/mo at testnet |
| Legal | $466K | Wilson Sonsini retainer + round-closing fees ($127K seed, $102K strategic) |
| Recruiting | $447K | 18%-of-salary recruiter fees, tracks each hire |
| Offsites | $380K | 4 × $95K (summer/winter '24 & '25) |
| Office | $204K | WeWork NYC + Kathmandu coworking |
| Travel | $131K | BTC++ Berlin/Asia, ZK Day, Devcon — real conferences, real attendee counts |
| Software | $85K | Slack, Notion, GitHub, Jira, Linear, Figma, Discord, Anthropic |
| Audit | $80K | Trail of Bits, post-testnet (Sep '25) |
| Insurance/Accounting/Marketing/Learning | $330K | D&O+health, Pilot, sponsorships, books |

**Runway is healthy and the curve is coherent:** cash dips to ~$10.1M before the strategic
round, jumps to ~$18.3M, then bleeds to **$14.7M** by sim-end at an accelerating ~$400K/mo
burn → ~30 months of runway. Spend is causally tied to milestones (compute ramps at testnet;
recruiter fees track hires; legal spikes one month after each round closes).

---

## 6. Per-source fidelity — does it resemble Alpen?

### ✅ Resembles Alpen well
| Source | Volume | Verdict |
|---|--:|---|
| **GitHub** | 4,046 commits / 2,389 PRs / 1,088 reviews | Real repos, handles, bot noise (dependabot, ghost). Rich. |
| **Slack** | 10,316 msgs | Real channels, two-timezone cadence. Richest source. |
| **QuickBooks** | 865 purchases / 4 deposits | Hand-authored finance — fully Alpen-coherent. |
| **Calendar** | 2,034 events | 6 team standups + realistic 1:1 web + PTO. |
| **Notion** | 1,448 pages / 1,448 blocks | Solid corpus depth. |
| **Grafana** | 798 annotations | Realistic deploy + alert stream (~30/mo, real service names). |
| **Mercury / Brex / Ramp** | ~865–911 txns each | Faithful finance volume (mirror the QB schedule). |
| **Ashby** | 336 entities | Real recruiting funnel (jobs + candidates + interviews). |
| **Gmail / Discord / Miro / Figma** | 736 / 689 / 233 / 177 | Adequate, real people/content. |

### ⚠️ Does NOT resemble a real Alpen — flag list
1. **Timeline frozen at Dec 2025.** Biggest gap. No 2026 (Mosaic launch, BTC Credit Markets,
   Duty-Free Bits, H1-2026 hires). The sim is ~6 months behind the real company. If the goal
   is "resembles Alpen *today*," this is the #1 thing to fix — extend `virtual_now` forward
   and replay the corpus (it already contains events to 2026-10).
2. **Google Drive — 29 files over 23 months.** Implausible for a 35-person company with 1,448
   Notion pages. Either under-seeded or the company is Notion-only; as-is it reads as "Drive
   is unused." **Under-seeded — enrich.**
3. **Jira — 245 issues, only 6 bugs, 10 epics.** Real eng orgs file hundreds of bugs. GitHub
   carries the real eng signal; Jira looks like a lightly-used afterthought. The 6-bug count
   undercuts the "issues/deviations to test" goal on the Jira surface specifically.
4. **Three redundant payroll/HR systems** — Gusto, Deel, **and** HiBob each carry the *identical
   36 employees*, alongside three finance rails (Mercury bank + Brex card + Ramp card) + QuickBooks.
   No real company runs 3 payroll providers + 3 card/bank systems. A test-harness artifact (every
   connector needs data), but a multi-source ingest would **triple-count payroll & headcount**.
5. **Comms/social sources start only Oct 2024** — Telegram, Signal, LinkedIn, Fireflies have
   *zero* data for the company's first ~8 months (Feb–Sep 2024). Plausible if those tools were
   adopted later, but it's an unexplained 8-month hole.
6. **AWS — only 90 days (Oct–Dec 2025), 1,460 events.** Faithful to CloudTrail's 90-day
   retention, but means no cloud-infra history for the first ~20 months and a per-month spike
   that dwarfs every other source. Expected behaviour, worth knowing.
7. **GitHub commit distribution is flattened.** Real contribution is a power-law (Rajil 907,
   Prajwol 618 … long tail to 0). The seeded sample compresses the top to ~170–392 each, so the
   *ranking* survives but the *skew* doesn't. The DB holds 4,046 of the ~6,000+ real commits.
8. **LinkedIn — 26 posts / 15 months.** Thin but defensible for a low-posting startup org page.
9. **Attrition under-modelled** (see §4) — headcount overstates retention.

### Consumer-side note (from `INGESTION_SWEEP_FINDINGS.md`)
Separately from data richness: Fyralis's *own* ingestion clients for **brex, deel, ramp, gusto,
figma, fireflies, carta, linkedin, hibob** are **placeholders** (wrong API paths → 404 against
the faithful mocks). So even though these sources are well-seeded, Fyralis can't currently pull
them. That's a Fyralis-client gap, not a spammer-data gap — but it means several of the
"resembles Alpen" sources above won't reach the model layer until those clients are rewritten.

---

## 7. Recommended enrichment priorities
1. **Advance `virtual_now` to ~mid-2026 and replay** — unlocks the entire 2026 story the corpus
   already knows (Mosaic, BTC Credit Markets, more hires). Single highest-leverage fix.
2. **Seed Google Drive properly** (design docs, specs, spreadsheets per team) — 29 → hundreds.
3. **Deepen Jira** — more bugs/incidents/sub-tasks so the "deviations to test" live somewhere
   structured, not only in GitHub.
4. **Pick one payroll system as canonical** (or document the 3-system redundancy as intentional
   test scaffolding) to avoid triple-counted headcount on ingest.
5. **Backfill comms/social to founding** or annotate the Oct-2024 adoption date as deliberate.
6. **Model silent attrition** — set `ended_at` for the short-tenure contributors so headcount
   reflects real churn.
7. **Restore the real commit skew** if power-law contribution matters to the patterns under test.
