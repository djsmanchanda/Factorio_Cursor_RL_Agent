<!-- Path: docs/rl/Laya_Cohort_Ledger_2026-10-21.md | Purpose: Preserve the inherited thread's cohort accounting and decision limits for the next owner. -->
# Cohort ledger at takeover

Recorded 2026-10-21, 00:50 IST. Owner history: `import:claudeAgent:14dfe1ab-afe7-4f55-bd4a-27eabe7cf3ea` (Advance Laya Cohort Experiments). Thread `cbfa7040-7f1b-4218-90a2-d8caf8bc5818` diagnosed failure drop points and received the user's revised C6 intent, then stopped at its session limit before delegation. The present takeover thread is `8ad17b32-a2dc-4339-9b53-6b08573e6e63`.

These are disposable guided-policy experiments on copies of one save. Matching policy seeds is not unseen-terrain evaluation. Old C6 is the deterministic consumer predicate with defective recipe/lifetime-counter attribution; it must not be relabelled as the new Laya choice/output endpoint. No listed cohort authorizes adoption or real-base action.

| Cohort | Planned and observed accounting | Main finding and decision |
| --- | --- | --- |
| C3, miniship, 2026-10-18 | Attempt 1: 96 planned, 8 interrupted by host reset, 88 unstarted. Replacement: 96 planned, 79 completed, 8 interrupted, 9 unstarted; 39 complete matched pairs. | `09c093a` versus `421e3de`: matched strict C2 18/17, C3 7/3; no C4/C5. No adoption; preserve both attempts. |
| C4, starship, 2026-10-18 | 96/96 completed, 48 seeds 77400–77447. `421e3de` review versus `5271f7f` bounded loan-cell pipe credit. | Historical strict C2 14/21, C3 3/7, C5 0/1. A stale loan-tag release loop was observed and fixed later in `37dc304`; no adoption. |
| C5, miniship, 2026-10-18/19 | Parts a/c/d/e: 288 planned records, 193 completed, 8 interrupted, 2 runner errors, 11 explicitly unstarted, 74 stale queued records from abandoned part c. Part b separately prepared 72 jobs but has no job-status or execution evidence; it was superseded. 95 complete matched pairs in the analysis. | `421e3de` versus `37dc304`: strict C2 41/41, C3 12/12, C5 1/0 in matched pairs. The new loan-cell query failed live 104 times because the inventory enum changed; fixed in `fba641b`. Review arm had 56 stale-tag release failures. No adoption; do not omit errors or abandoned jobs from the ledger. |
| C6, miniship, 2026-10-19 | 96/96 completed; 48 matched seeds, `37dc304` versus `5d1d866`. | Pipe reopenings 41→24; strict C2 17→21 (+10/−6), C3 4→2, no C4. Fixed query works, but no long-window adoption. |
| C7, miniship, 2026-10-19/20 | 396/396 completed: part a 96, part b 300, 198 complete pairs. A separately stopped starship attempt was excluded and its policy seeds rerun on miniship; its original per-job accounting is not recovered here. | `e18d8d0` versus `d633957`: C2 80/78, C3 20/37, C5 1/4, C6 0/0. Final acceptance was latched, so final service is unknown. Collision events rose 13→26; one robot survey race was fixed in `4a5c057`. No adoption. |
| C8, miniship, 2026-10-20 | Initial design 800 jobs, extended to 1,000 planned jobs, superseded before any start. Preserve its manifest; no completed-cohort claim. | Independent review replaced isolated designs with the cumulative C9 campaign. |
| C9, miniship, 2026-10-20 | 240/240 completed; 48 seeds 79300–79347 × five cumulative arms. Separate wiring smoke: 2/2 completed, not part of the cohort denominator. | C5 ever/final: ref 8/7, plastic 12/12, rung 14/13, extend 10/10, batch 15/11. Legacy C6 zero in all arms. No significant endpoint contrast; no adoption. |
| C10, miniship, 2026-10-20 | 144/144 completed; 48 seeds 79400–79447 × full/copper/norung. | Full C5 14/48 ever, 11/48 final. Copper/norung were invalidated by 91/61 link-identity tool errors and 14/11 safety stops. `030f8de` fixes that crash. No adoption. |
| C11, miniship, 2026-10-20/21 | **144/144 completed**, 48 per arm; seeds 79400–79447. Finished before C12 began on 2026-10-21. | C2 copper/ready/reconcile 17/18/18; C3 ever/final 14/14, 12/12, 12/11; C5 ever/final 16/14, 17/15, 16/15. Legacy C6 0/1/0 remains exploratory. Adjacent C5 discordances ready-vs-copper +10/−9 and reconcile-vs-ready +9/−10: no measurable effect, no adoption. [Final result](Laya_C11_Result_2026-10-21.md). |

C3–C6 used the older 2.1.21 campaign environment; C7 onward pin Factorio 2.1.22 build 87719. Do not merge the engine windows as matched evidence. Historical C1/C2 campaigns predate the inherited owner's C3 work; their summaries are in the original thread and are not new takeover runs.

Sources (relative to the repository unless marked starship):

- `data/laya-orchestration/next-20261018/state.json` and the existing dated sections of `docs/rl/Laya_Current_Handoff.md`.
- `data/laya-experiments/laya-c3-20261018/{job-status.attempt1.json,job-status.json,analysis.txt}`.
- starship: `data/laya-experiments/laya-c4-starship-20261018/{freeze.json,job-status.json}`.
- `data/laya-experiments/laya-c5-20261018/{a,c,d,e}/job-status.json`, `b/batch.json`, `analysis-all.txt`.
- `data/laya-experiments/laya-c6-20261019/{a/job-status.json,analysis.txt}`.
- [C7 result](Laya_C7_Result_2026-10-20.md), `data/laya-experiments/laya-c8-20261020/freeze.json`, [C9 result](Laya_C9_Result_2026-10-20.md), [C10 result](Laya_C10_Result_2026-10-20.md).
- `data/laya-experiments/laya-c11-20261020/{freeze.json,miniship/job-status.json}`; status and mechanism scripts at `data/laya-orchestration/next-20261018/c9-analysis/{status.py,c11_extras.py}`.

## Takeover experiments and fleet update

The original 2.1.22 C12/C13 attempts were stopped at the user's explicit fleet-upgrade request; preserve them as separate attempts. C12: 192 planned, 1 completed, 10 runner errors during shutdown, 7 interrupted, 174 unstarted. C13 on starship: 144 planned, 2 runner errors during shutdown, 4 interrupted, 138 unstarted. These are raw harness statuses, not measurements of new-policy safety. C13's first supervisor also failed before any job because PYTHONPATH was omitted; `run.attempt1.out` preserves it. No arm code was edited under an active campaign. Nothing from these shortened attempts enters the new matched denominators.

The user's newer instruction is authoritative: **no starship game runs after 2026-10-21 02:30 IST**, because it may sleep then. All starship game processes were stopped well before 01:00; no new starship runs are scheduled. Its C4 metadata, C13 evidence and menu handoff are copied into miniship `data/laya-orchestration/takeover-20261021/starship-evidence/`. All new campaigns and the Opus successor run on miniship.

Fresh C12: `data/laya-experiments/laya-c12-2123-miniship-20261021`, same-named user unit, 192 jobs. Four cumulative arms isolate raw-ore satisfaction, stocked-furnace legality and transitive circuit-seed sizing; all share the new C6 measurement and engine manager patch. Adaptive slots start 12, min 6, max 18. Deadline 2026-10-21 14:45 IST, hard service cap 16 hours.

Fresh C13: `data/laya-experiments/laya-c13-2123-miniship-20261021`, same-named user unit, 144 jobs. Ref → full shortage menu → loan prerequisite lifecycle. Adaptive slots start 6, min 3, max 8. Deadline 18:45 IST, hard service cap 20 hours. Combined initial concurrency is 18; maximum is 26 subject to UPS/memory guards. Comparisons are adjacent within each cohort, not cross-cohort promotion claims.

Both cohorts pin **Factorio 2.1.23 build 87750**, the same source save, same staged project mods and policy seeds 79400–79447. Full uncommitted common measurement/engine patches and the split seed diff are in each arm's freeze. Historical 2.1.22 runs are not pooled with these. A fresh miniship 2.1.23 three-decision smoke completed with zero runner failures and verified C6 observation compatibility, but did not prove C6 output.

New code: menu `3e29384` (integrated `8ccdbfb`), C6 `830c022` (integrated `42da7aa`), loan `bd9a082` (integrated `54df1d2`), engine defaults `054dd3f`. 433 integrated focused tests and 49 engine-management tests pass. Fast gate: 1,179 passed, one skipped, the preexisting `training/laya_ops.py → autonomous_builder` firewall failure. `d491d6c` consumer option is excluded because the user changed C6's meaning.

Both machines have private 2.1.23 headless runtimes, with older runtimes retained. The starship system GUI package is 2.1.23; miniship has a user-owned 2.1.23 GUI and an explicit desktop launcher, while `/usr/bin/factorio` and the package database retain 2.1.21 for rollback (local sudo requested a password and was cancelled). Use `/home/miniship/.local/bin/factorio` for the new GUI; shell PATH may still resolve the old system binary. Stopped fleet configurations and manager/dashboard defaults point to 2.1.23. No real save was loaded or changed.

Opening-order/coverage remains a research task. Gear-first recovery exists in C11 `ready-s79434` and `reconcile-s79433`; no copper-first legality rule is justified. The needed shared API previews the exact selected mall plan and construction/logistic coverage without declaring ledger bills, claiming slots, changing reservations or submitting work. Steel-seed placement remains unimplemented.

The new Opus 5.5 high owner is scheduled for **2026-10-21 03:32 IST** on miniship, with 55-minute check-ins and authority to continue bounded disposable experiments and verified fixes. Read the local takeover handoff for exact launch/scheduler records. No real-base authority is granted.

C14 (launched 2026-10-21 05:05 IST, miniship): 150 planned jobs, 75 seeds 79400–79474 × ref `c1b8680` / steelbill `934f9cb`, Factorio 2.1.23 build 87750, same copied save. It shares one host slot budget with C12/C13 (start 20 total, max 30, older first), using the harness at `57feb31`. Results are pending.

C14 attempt 1 was stopped at 05:18 IST: 150 planned = 1 completed, 4 interrupted, 1 shutdown runner_error, 144 not started. Shared admission ignored host health (fixed in `28a2bb8`). The rerun `laya-c14b-2123-miniship-20261021` (150 jobs, same arms/seeds) is the C14 denominator; attempt 1 is not pooled.

C15 attempt 1 (stopped 13:41 IST): 150 planned = 1 interrupted, 149 not started; excluded. C15b (`laya-c15b-2123-miniship-20261021`, 150 jobs, steelbill vs vacancy) is the C15 denominator; it is queued behind C14b under the shared host budget.

C12 final (2026-10-21 ~14:20 IST): 192 planned = 160 completed + 32 not_started_deadline (seeds 79440–79447, whole blocks); no errors or interruptions. C2 15/16/11/7, C5 ever/final 14/5, 11/9, 10/8, 6/1, new Laya C6 12/7/8/6 for ref/raw/furnace/seed. No adoption. [Result](Laya_C12_Result_2026-10-21.md).

C15b was superseded before any job started (150 planned, 0 started). The C15 denominator is now C15c: 150 jobs = 50 seeds × steelbill/vacancy/foundation.

C13 final (2026-10-21 ~16:40 IST): 144/144 completed; no errors. C2 18/14/18, C5 ever/final 12/3, 19/2, 19/2, new Laya C6 10/18/17 for ref/menu/loan. No adoption. [Result](Laya_C13_Result_2026-10-21.md).

C15c was superseded before any job started (150 planned, 0 started) by C15d on the scheduler-gated harness; C15d's own first attempt admitted nothing (legacy registry shape) and was restarted on 81f0735. C14b was drained at ~17:10 IST and continues as C14c (80 jobs, seeds 79435–79474); account C14b's completed/in-flight jobs plus C14c's 80 together.

C14b closed 2026-10-21 17:40 IST after draining: 150 planned = 71 completed + 79 queued-never-started. All 79 (seeds 79435 ref and 79436–79474) are carried by C14c; earlier interrupted/rerun attempts are recorded in its job history. C14 accounting = C14b completed + C14c's 80 jobs, paired within one cohort per seed.

C14 final (2026-10-22 ~04:40 IST): C14b 71 completed + 79 never started (carried by C14c), C14c 80/80 completed (4 reruns after the 02:39 power cut). 75 matched pairs; C2 ref 26 / steelbill 18 (10:18), C5 ever/final 25/11 vs 20/5, new Laya C6 21/17. No adoption. [Result](Laya_C14_Result_2026-10-22.md).

C16 (launched 2026-10-22 04:50 IST, miniship): 150 planned = 50 seeds 79400–79449 × ref c1b8680 / vacancy 0bf1bdc / foundation 7fd2320 (no steelbill). Scheduler-gated harness 65d5613. Results pending.

C15d final (2026-10-22 ~06:20 IST): 150/150 completed (8 power-cut reruns under --resume). C2 21/25/27, C5 ever/final 21/2, 24/3, 21/3, new Laya C6 21/21/18 for steelbill/vacancy/foundation. No adoption. [Result](Laya_C15_Result_2026-10-22.md).

C17 (launched 2026-10-22 06:40 IST, miniship): 150 planned = 75 seeds 79400–79474 × foundation 7fd2320 / release d39b6c7. Results pending.

C18 (launched 2026-10-22 15:09 IST, miniship): 150 planned = 75 seeds 79400–79474 × release d39b6c7 / credit e37464d. Results pending.

C16 final (2026-10-22 ~16:30 IST): 150/150 completed. C2 15/25/35, C3 6/22/22, C5 ever/final 13/3, 24/4, 25/4, new Laya C6 11/21/22 for ref/vacancy/foundation. Vacancy and foundation adopted as the cohort base. [Result](Laya_C16_Result_2026-10-22.md).

C17 final (2026-10-22 20:06 IST): 150/150 completed. C2 60/59, C5 ever/final 47/12 vs 45/7, new Laya C6 40/41 for foundation/release; refusals 169 → 1. Not adopted by endpoints; kept as a correctness fix. [Result](Laya_C17_Result_2026-10-22.md).

C19 (launched 2026-10-22 20:31 IST, miniship): 150 planned = 75 seeds 79400–79474 × credit e37464d / started b372672. Results pending.

C20 (launched 2026-10-22 22:46 IST, miniship): 150 planned = 75 seeds × started b372672 / integrated 8507710 (Starship review integration without loan retention). Results pending.

C21 (launched 2026-10-22 22:46 IST, miniship): 150 planned = 75 seeds × integrated 8507710 / retain 657572e (review loan retention). Results pending.

C18/C19 cut (2026-10-23 00:17 IST, user request): C18 → 120 planned (seeds 79400–79459; 30 cut), C19 → 102 planned (seeds 79400–79450; 48 cut). Cut jobs never started; see each root's cut-jobs.json.

C18 final (2026-10-23 01:39 IST): 120/120 completed + 30 cut. C2 42/51, C5 ever/final 29/8 vs 44/12, new Laya C6 24/35 for release/credit. Credit adopted. [Result](Laya_C18_Result_2026-10-23.md).

C19/C20/C21 stopped (2026-10-23 05:52 IST, user, after reverting b372672): running worlds finish; not-started jobs listed in each root's cut-jobs.json. C19's completed pairs are its result.

C22 (launched 2026-10-23 05:53 IST, miniship): 150 planned = 50 seeds 79400–79449 × credit e37464d / integrated d8a4825 / retain 3d221aa. Results pending.

C23 (launched 2026-10-23 05:56 IST, miniship): 150 planned = 75 seeds × integrated d8a4825 / receipt 3b7d702. Results pending.

C19 final (stopped 2026-10-23 06:44 IST): 70 completed + 48 cut + 32 stopped. 35 pairs: C2 3:9, new Laya C6 5:10; b372672 reverted. [Result](Laya_C19_Result_2026-10-23.md). C20 (33 completed + 117 stopped) and C21 (70 + 80) superseded by C22.

C22/C23 trimmed to 120 planned each (2026-10-23 06:30 IST, user; before any job started): C22 seeds 79400–79439 × 3, C23 seeds 79400–79459 × 2.

C23 final (2026-10-23 15:40 IST): 120/120 completed + 30 cut. New Laya C6 29/37 (15:7), legacy C6 9/14 for integrated/receipt. Receipt not adopted now; baseline d8a4825. [Result](Laya_C23_Result_2026-10-23.md).

C24 (prepared by thread cb16c604, waiting): 120 planned = 60 seeds 79500–79559 × integrated d8a4825 / steel 5fb6371; ports 38100/31100. C25 (waiting behind C24): 120 planned = 60 seeds 79560–79619 × integrated / isolated backing patch; ports 38200/31200. Both gated on C22/C23 closure and the baseline decision.

C22 final (2026-10-23 17:38 IST): 120/120 completed + 30 cut. New Laya C6 23/18/27, C5 final 5/7/1 for credit/integrated/retain. Baseline d8a4825 decided; retention not adopted. [Result](Laya_C22_Result_2026-10-23.md). C24/C25 gate released 18:16 IST.

C26 (queued 2026-10-23 22:30 IST, waits for C24+C25): 60 planned = 30 seeds 79620–79649 × control / ready (2d9b4b5 + mall_loan_reconciliation). C27 (same): 60 planned = 30 seeds 79650–79679 × control / linear3 (2d9b4b5 + linear wait pacing). Results pending.

C24 final (2026-10-23 23:28 IST): 120/120 completed. C2 42/41 (12:13), new Laya C6 23/26; steel slower to strict C2 (21:33). Steel not adopted. [Result](Laya_C24_Result_2026-10-23.md). C25 admitting since.

C26/C27 (2026-10-23): cancelled before any start, 60 cancelled_queued each, superseded by C26b/C27b (2026-10-24 00:50 IST; same design and seeds, 7,200 s wall, ports 38500/38600). Results pending.

C25 final (2026-10-24 04:52 IST): 120/120 completed. New Laya C6 31/29 (10:12), C2 46/44, C3 33/28; backing slower to strict C3 (14:28). Backing not adopted. [Result](Laya_C25_Result_2026-10-24.md). C26b/C27b admitting since. C28 (`laya-c28-2123-miniship-20261024`, 60 jobs: control_a/control_b/steel30/feedstock16 on 2d9b4b5, seeds 82400–82414) belongs to thread 94dcfaf2 (user-authorized Starship follow-up), queued behind C26b/C27b.
