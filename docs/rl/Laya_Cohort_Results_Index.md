<!-- Path: docs/rl/Laya_Cohort_Results_Index.md | Purpose: One maintained chronological index of terminal Laya cohort and campaign results, with comparability and accounting caveats. -->
# Laya cohort results index — 1–28 October 2026

Compiled **2026-10-28 ~15:12 IST** from the local evidence tree. Later C45 and
actual C46c outcomes are recorded in [the 2.1.24 result](Laya_C45_C46c_Results_2026-10-30.md).
The C44–C46 pending rows below retain their historical snapshot, not current status.
This is a
retrieval index, not a re-analysis: each row states the terminal accounting,
the tested change, the reported endpoint, the decision and the source. Detailed
results and source-bound records provide supporting evidence; repository
contracts govern interpretation. Adoption entries record historical decisions,
not new approval from this index.

## How to read this index

- A row is a **cohort**, not a run. Slash-separated arm values follow the arm
  order named in the “Tested change” cell.
- `E/F` means **strict accepted ever / strict accepted final** as reported by
  the cohort. “Final” is the last sampled acceptance, **not** sustained
  service. `n/r` means the cohort did not report that field; `—` means the
  endpoint does not apply. Missing evidence is `unknown`, never zero.
- `strict` means the deterministic checkpoint predicate in the runner
  (`c2-v1` … `c6-v1` unless stated). A **proxy phase** is only a phase reached;
  it is not strict acceptance. Proxy counts are given only where the cohort’s
  main finding depends on them.
- **Laya C6 v2** is the separate ops choice/output endpoint
  (`laya-c6-choice-output-v2`), not deterministic strict C6. Some reports list
  it as “observed” jobs rather than passes; those cells say `obs.` and must not
  be read as acceptance counts.
- `C0` is a milestone, not a cohort. Cohort numbering starts at C1 on
  18 October 2026; the earlier October campaigns are listed separately below
  and are not C1/C2.

### Coverage

- **Numbered cohorts: 43/43** — C1 through C43, including superseded,
  cancelled and no-start records (C8, C32) and the invalid C31 measurement.
  Letter/retry variants are accounted inside their parent row (C3 attempt 1;
  C12/C13 original attempts; C14b/C14c; C15 attempt 1/b/c/d; C26/C27 pre-b;
  C30a/S30r).
- **Earlier named campaigns and collections:** terminal batches, pilots and
  owner-supervised campaigns from 7–17 October, plus 2 model/infrastructure
  trials. Persistent segments and overlapping collections are qualified below.
- **At the original 28 October snapshot:** C44, C45 and C46 were pending.
  C44 subsequently completed, and the intended 2.1.24 C45 and C46c completed;
  their original 2.1.23 freezes remained held and superseded before start.
- The October 3 L1–L3 learned-lab workflow and October 5 geometry campaign are
  **separate lines**. They are not folded into this ops index and are not
  comparable to C1–C43. Early-month separate evidence includes the Oct 4
  mechanics matrix (554 trials, 0/141 C2, 262/354 components) and the Oct 5
  seven-hour geometry/variety continuation (`docs/archive/rl/Laya_Seven_Hour_Variety_2026-10-05.md`);
  those use different predicates and funding. The first numbered ops cohort is
  C1 on 18 October.

### Comparability rules

1. **Seeds are policy randomisation on one copied save, not independent
   terrain.** Cross-cohort counts are not additive replications. C40
   deliberately reuses a C39 seed subset. Generalisation requires fresh policy
   seeds and independent terrain/save conditions.
2. **Engine windows differ.** C1–C6 used Factorio 2.1.21 build 87673; C7–C11
   used 2.1.22 build 87719; C12 onward used 2.1.23 build 87750. Do not pool
   these as one matched series.
3. **Budgets differ.** C1–C6: 360,000 ticks / 4,500 s. C7–C25: 540,000 ticks /
   6,000 s. C26b onward: 540,000 ticks / 7,200 s. C40 used a **216,000-tick**
   cap. A stall ends an episode early, so realised exposure is unequal even
   within one budget.
4. **Hosts differ.** C4 and the C30/S30r stratum are Starship evidence; most
   numbered rows are Miniship. Excluded Starship attempts remain in their rows.
5. **Predicate versions differ.** C5-v1/v2 and C6-v1/v2/v3 are distinct.
   A missing version label makes a paired C5 comparison invalid, not zero.
6. **`completed` means a terminal runner result**, including stalled and
   window-timeout outcomes. It is not a mission win. `failures=0` counts caught
   exceptions only; typed and untyped fault events are separate.

## Named campaigns before C1 (7–17 October)

These are terminal campaign records, not numbered cohorts. They are kept
separate because their predicates, arms or accounting are not the C1–C43
series.

| Date | Campaign | Plan → terminal | Arms / change | Reported result | Decision and source |
|---|---|---|---|---|---|
| 7 Oct | `ops-history-20261007` | 312/312 completed, 52 per arm | old baseline; mall-first-30; +follow-through; +no-repeat; +unstick; +all-history | Proxy C1 2/20/19/20/22/24; proxy C2 **0 in every arm**; 303 stalls + 9 window timeouts (per-arm 51/52/50/50/52/48 and 1/0/2/2/0/4) | Mall-first-30 beats baseline on C1 pairs 19:1, but no C2. No adoption; more history alone 10:6, p≈0.45. `S08` |
| 7–8 Oct | power-interrupted loan / honest campaigns, including `ops-loans-20261008b` | Final planned/terminal totals unknown in the cited handoff; October 8 audit had only 38 indexed completions | Loan and other-slot changes; one arm also changes core-slot effective weights | Interim counts are not final results; do not reuse the later 320-trial denominator | Ended/interrupted historical records remain separate from `next-20261008`; no isolated loan-effect claim. `S08`, `S09` |
| 8 Oct | `next-20261008` | 320/320, 160 per arm; cited stop classes total 299 (279 stall + 15 game + 5 wall); remaining 21 stop classifications unreconciled here | baseline core mall ×180 / other slots ×1 | Strict C1 25/16; strict C2 12/9; proxy C2 48/58; 0 process crashes; separately 5 `tool_error` and 93 `untyped_stuck` events | Candidate’s proxy looked better while strict C1/C2 were lower. Retain baseline weights; no improvement. `S09` |
| 10 Oct | first improvement loop | 35/35 indexed same-seed runs; 2,866 decisions | Sequential repair loop, not matched arms | All stalled; observed strict C3 in 9/35, no C4–C6. All 35 Muse report publications failed; zero fixer commits | Improvement pipeline did not function; recovered drafts are not successful publications. `W17` |
| 10 Oct | successor improvement loop | 3 allocated/indexed runs | Repair handoff | 2 hard failures + 1 runner error; 2 verified fixes | Handoff repair worked; no factory improvement established. `W17` |
| 10–11 Oct | first DeepSeek/Haiku campaign | 5 exploratory runs, one revision each, same seed | 4 reviewed fixes | Strict C2 observed in 2 runs; no research completion; final run had user intervention | n=1 per revision, no causal policy gain. `W17` |
| 10–14 Oct audit collection | owning-GPT campaign | 1 run | Supervised six-hour campaign | `stalled_wall_30m`, strict C1; campaign ended early on harness failure | Proposed fix abandoned; no subsequent verified adoption. Exact run date not resolved in this index. `W17` |
| 13 Oct | repair campaign | 4 allocated episodes | Sequential repairs, 2 fixes | Preparation hard failure; 96-decision stall; 2,300-decision stall; 36m40s runner error | One long run has 8,703 executor deferrals, not that many policy decisions. `W17` |
| 14 Oct | ten-hour successor | 2 episodes | Successor repairs | 461-decision stall; then 1,817 decisions / 15,378 seconds, stalled with strict C2 | Ended early on reporting; later record-retention repair is not gameplay success. `W17` |
| 14 Oct | eight-hour successor | 1 episode | Successor campaign | 1,391 decisions, about 3h05m, strict C2; no research completion | Ended on provider recovery failure; no verified successor gameplay fix. `W17` |
| 10–17 Oct audit collection | persistent controller | 37 segments, 2,780 decisions; often the same world | Continued worlds, not fresh trials | 27 deliberate stops + 5 stalls + 3 hard failures + 2 runner errors; no completed target | Heavily censored mechanics evidence; do not add to fresh-cohort denominators. `W17` |
| 15 Oct | epsilon pilot | 24/24; 6 epsilon values × 4 seeds | `163ae4e`, richer lineage, c1-v2 | Strict C2 by epsilon 0/5/10/15/20/25%: 2/0/1/1/2/2 of 4 | Tiny noisy pilot; no default change. Epsilon zero still uses weighted sampling. `W17` |
| 15 Oct | mechanics matrix | 216 admitted (192 core + 12 calibration + 12 confirmation) | four cumulative revisions × six epsilons × eight seeds; `c1-v1` | 180 completed, 36 wall timeouts; core: 6 C1, 11 C2, 1 C3, 0 C4 | No architecture or geometry winner; adjacent revisions only. `W17` |
| 16 Oct morning | fresh batch | 96/96; 24 per arm | control / goal-producer / reservation-release / shortage-weight-8 | Strict C2 9/12/13/14; proxy C2 12/15/17/15; all C4 zero | No demonstrated oil breakthrough; reservation lead did not replicate at night. `W17` |
| 16 Oct night | `batch-20261016-night` | 80/80, 20 seeds × 4 arms | control `a832bef`; oil-parts; reservation-release; shortage-weight-8 | Strict C2 12/17/12/15; strict C3 3/4/1/5; no C4 in any arm | No adoption; C4 never started an oil cell. 423 `untyped_stuck` events across the three 16–17 Oct batches. `B16` |
| 17 Oct early | fresh batch | 96/96; 24 per arm | control and 3 oil-frontier candidates | Strict C2 18/17/16/15; C3 5/3/5/4; all C4 zero. Coal treatment fired 0/24, handoff 6/24 | No challenger beat control; unexercised treatment is not a negative mechanism test. `W17` |
| 17 Oct day | `batch-20261017-day` | 64/64, 16 seeds × 4 arms | control `7bbb1d5`; no-latch; pipe-300; shortage-weight-8 | Strict C2 8/7/9/12; strict C3 1/4/2/4; no C4 | Weight-8 had the best C2 but no plastic and the chest loop remained. No adoption. `B17` |
| 17 Oct | `reset-chest-20261017` + sidecars, plus `normal-rung-20261017` | 282 planned → **117 completed + 165 not started by deadline**; normal-rung adds one analysed run | control; rung; chest; extra sidecar seeds 76716+ | 36 shared completed seeds: proxy C2 control 19, rung 23, chest 7. Normal-rung seed 76900: proxy C0 +95 s, C1 +277 s, then stall; strict C2/C3/C4/C6 failed, C5 unknown | Deadline-limited, not a completed matched cohort. No adoption. Count the 165 unstarted jobs; do not report 117/117. `H` |

The weekly audit also lists individual stock-fix/fullmall/cellreserve/sharedsub/
chestcap validations and an authorized jumpstart with restarts. These are
mechanics or continued-world evidence, not additional matched cohorts; their
results and failures remain in `W17`. Its collections can overlap, so no
grand trial total is inferred from this table.

## Week of 18–20 October: deterministic-era cohorts C1–C11

These rows use historical deterministic predicates. Legacy C6 is reported only
where the cohort reported it; `n/r` is not zero. C1–C6 and C7–C11 are different
engine windows and must not be pooled.

| Cohort (seeds; date) | Plan → terminal | Tested change (frozen arms) | Strict E C2/C3/C4/C5, or named subset (per arm N) | Legacy C6 E | Decision / lesson | Source |
|---|---|---|---|---|---|---|
| **C1** 77100–77131; 18 Oct | 128 planned → **103 completed + 8 interrupted + 17 not started**; per-arm completed 27/25/25/26 of 32 | base → gate → starship → startup (2.1.21, 360k ticks) | base 17/5/0/0 (27); gate 16/4/0/0 (25); starship 14/1/0/0 (25); startup 14/2/0/0 (26) | n/r | No adoption. Gate/starship/startup selected 71/51/64 raises and **all failed**, while summary `failures=0`; a typed omission, not a clean run. Proxy C3 did not unlock oil. | `A1-3` |
| **C2** 77200–77235; 18 Oct | 108 planned → **72 completed + 36 not started**; 24 completed per arm | rung `f1469e1` / merged `88093ab` / ladder `09c093a` | rung 8/2/2/2; merged 6/2/0/0; ladder 11/4/1/1 | n/r | Ladder’s embodied credit improves C2 6→11, C3 2→4 and C4/C5 0→1 on matched seeds, but paired successes are slower. No adoption. | `A1-3` |
| **C3** 77300–77347; 18 Oct | Primary 96 → **79 completed + 8 interrupted + 9 not started**; 39 complete matched pairs. Superseded attempt: 96 = 8 interrupted + 88 not started, separately | ladder / review `421e3de` | ladder 18/7/0/0 (39); review 18/3/0/0 (40) | n/r | Review bundled two correctness fixes; strict C3 falls 7→3. No adoption. Preserve the separately counted reset attempt. | `A1-3`, `L` |
| **C4** 77400–77447; 18 Oct (Starship) | **96 planned, 48 seeds**; ledger reports 96/96 completed, but the C4 raw-log audit could not recover local terminal statuses | review `421e3de` / cell `5271f7f` | C2 14/21; C3 3/7; C5 0/1 | n/r | No adoption. 18 `release_owner_missing` failures in cell-s77403 despite `failures=0`; later fixed in `37dc304`. C4 raw terminal accounting is **unknown locally**, not zero. | `A4-6`, `L` |
| **C5** 18–19 Oct | **360 attempt slots** (A 24, B 72, C 86, D 82, E 96) → 193 completed + 8 interrupted + 2 runner error + 157 not started; 95 same-part matched pairs. B never started; C has 74 stale queued | `421e3de` vs `37dc304` (pipe credit, observed-tag release) | Matched C2 41/41; C3 12/12; C5 1/0 (95/arm) | n/r | No adoption. New loan-cell query failed 104 times on the changed inventory enum; review arm had 56 stale-tag release failures. Keep errors and abandoned jobs in the denominator. | `A4-6`, `L` |
| **C6** 19 Oct | **96/96 completed**; 48 pairs | prev `37dc304` vs facts `5d1d866` | C2 17/21; C3 4/2; C5 0/0 | n/r | No adoption. Inventory query fixed (error events 48→0; pipe reopenings 41→24), but no later-milestone gain. Old `accepted_final` is latched, not fresh. | `A4-6`, `L` |
| **C7** 19–20 Oct | **396/396 completed**; 198 per arm, 198 pairs; 395 stalled, 1 timeout. A separate Starship C7 attempt was stopped and its policy seeds rerun here; its per-job accounting is not recovered | base `e18d8d0` vs netstall `d633957` | C2 80/78; C3 20/37; C5 1/4 | 0/0 | No adoption. Netstall improves C3 reach but not C2. One `KeyError: construction-robot` is retained in the denominator. C7 final is old latched final. | `A7-9`, `L` |
| **C8** 20 Oct | **1,000 planned → 0 launched**; all superseded | independent candidate design | — | — | Not a cohort and not a 0% result. Preserve the manifest; C9 replaced it. | `L` |
| **C9** 79300–79347; 20 Oct | **240/240 completed**; 48 seeds × five arms | ref / plastic / rung / extend / batch | C5 E/F: ref 8/7; plastic 12/12; rung 14/13; extend 10/10; batch 15/11 | 0 in all arms | No adoption. No significant C5 contrast; batch ends with 15 ever versus 11 final C5 passes. Legacy C6 is zero but measurement was defective. | `L`, `R9 (10-20)` |
| **C10** 79400–79447; 20 Oct | **144/144 completed**; 48 per arm | full / copper / norung | full C5 14/11; copper/norung **invalid** | n/r | No adoption. Copper/norung invalidated by 91/61 link-identity tool errors and 14/11 safety stops; `030f8de` fixes the crash. Do not read their zeros as policy failures. | `L` |
| **C11** 79400–79447; 20–21 Oct | **144/144 completed**; 48 per arm | copper / ready / reconcile | C2 17/18/18; C3 14/14, 12/12, 12/11; C5 16/14, 17/15, 16/15 | 0/1/0 | No adoption. Adjacent C5 discordances ±10, i.e. no measurable effect. Legacy C6 remains exploratory. | `L`, `R11 (10-21)` |

## Week of 21–23 October: C12–C23

At C12 the endpoint set changes: the cohort reports **Laya C6 v2** (explicit
advanced-circuit choice plus attributable output), separate from legacy
deterministic C6. The strict/legacy C6 column below is the old deterministic
predicate; the Laya v2 column is not a substitute for it.

| Cohort (date) | Plan → terminal | Tested change | C2 | C3 E/F | C5 E/F | Strict/legacy C6 E/F | Laya C6 v2 | Decision / lesson | Source |
|---|---|---|---|---|---|---|---|---|---|
| **C12** 21 Oct | **192 → 160 completed + 32 `not_started_deadline`** (seeds 79440–79447, whole blocks) | ref `f82f4dd` → raw → furnace → seed | 15/16/11/7 | 10/9; 11/11; 7/7; 9/9 | 14/5; 11/9; 10/8; 6/1 | 1/1; 1/1; 0/0; 0/0 | 12/7/8/6 | No adoption. Original 2.1.22 C12 attempt excluded: 192 = 1 completed + 10 shutdown runner errors + 7 interrupted + 174 unstarted. Deadline loss is real. | `R12 (10-21)`, `L` |
| **C13** 21 Oct | **144/144**, 48 per arm | ref → shortage menu → loan lifecycle | 18/14/18 | 8/8; 11/9; 6/6 | 12/3; 19/2; 19/2 | 1/1; 4/3; 5/2 | 10/18/17 | No adoption. Menu raises C5/C6 but lowers C2; loan recovers C2, loses C3. Original Starship attempt excluded: 2 runner errors + 4 interrupted + 138 unstarted. | `R13 (10-21)`, `L` |
| **C14** 21–22 Oct | **150 analysed; 75 matched pairs**. C14b 71 completed + 79 never started; retain 70, supersede its lone steelbill-s79435 by C14c's pair. C14c 80/80 (4 power-cut reruns recorded) | ref `c1b8680` vs steelbill `934f9cb` | 26/18 | 9/8; 10/10 | 25/11; 20/5 | 5/4; 4/3 | 21/17 | No adoption; steelbill suppressed retries and lost C2/C5/C6. Attempt 1 excluded: 1 completed + 4 interrupted + 1 shutdown error + 144 unstarted. | `R14 (10-22)`, `L` |
| **C15** 21–22 Oct | **150/150** (C15d; 8 power-cut reruns), 50 per arm | steelbill → vacancy → foundation | 21/25/27 | 13/13; 19/16; 17/13 | 21/2; 24/3; 21/3 | 5/3; 9/6; 7/4 | 21/21/18 | No adoption in C15. Attempt 1 (1 interrupted + 149 unstarted), C15b and C15c (150 planned each, 0 started) are excluded. C16 retests vacancy/foundation on ref. | `R15 (10-22)`, `L` |
| **C16** 22 Oct | **150/150**, 50 per arm | ref → vacancy `0bf1bdc` → foundation `7fd2320` | 15/25/35 | 6/5; 22/21; 22/19 | 13/3; 24/4; 25/4 | 6/3; 8/6; 9/7 | 11/21/22 | **Adopted vacancy and foundation as the cohort base.** C3 18:2, C5 14:3, Laya C6 13:3 vs ref; no production promotion. | `R16 (10-22)` |
| **C17** 22 Oct | **150/150**, 75 per arm | foundation `7fd2320` vs release `d39b6c7` | 60/59 | 39/36; 39/37 | 47/12; 45/7 | 12/8; 16/10 | 40/41 | Not adopted by endpoints; kept as a correctness fix. Loan-release refusals 169→1 and typed as `mall_cell_lifecycle_conflict`. Untyped stuck 50→24. | `R17 (10-22)` |
| **C18** 22–23 Oct | **150 → 120 completed + 30 cut** before start; 60 pairs; 2 reset reruns | release `d39b6c7` vs credit `e37464d` | 42/51 | 27/24; 35/32 | 29/8; 44/12 | 7/4; 10/9 | 24/35 | **Adopted credit** into the cohort base. C2 13:4, C5 ever 20:5 (p≈0.004), Laya C6 20:9. Reported `tool_error`/safety-stop counters zero. | `R18 (10-23)` |
| **C19** 22–23 Oct | **150 → 70 completed + 48 cut + 32 stopped**; 35 full pairs | credit `e37464d` vs started `b372672` | paired 3:9 | n/r | n/r | n/r | paired 5:10 | Stopped by user; no aggregate totals recovered. `b372672` reverted. Do not report this as a completed 35-pair rate. | `R19 (10-23)`, `L` |
| **C20** 22–23 Oct | **150 → 33 completed + 117 stopped**; 16 full pairs + 1 unpaired | started `b372672` vs integrated `8507710` | n/r | n/r | n/r | n/r | n/r | Superseded by C22 after the user stop. No endpoint claim. | `A20-21` |
| **C21** 22–23 Oct | **150 → 70 completed + 80 stopped**; 35 pairs | integrated `8507710` vs retain `657572e` | n/r | n/r | n/r | n/r | n/r | Superseded by C22. No endpoint claim. | `A20-21` |
| **C22** 23 Oct | **150 → 120 completed + 30 cut**; 40 per arm | credit → integrated `d8a4825` → retain `3d221aa` | 28/30/31 | 19/17; 24/22; 22/18 | 25/5; 23/7; 27/1 | 11/6; 6/6; 11/3 | 23/18/27 | Baseline `d8a4825` retained; loan retention not adopted. Whole-base integration, not a single-factor win. | `R22 (10-23)` |
| **C23** 23 Oct | **150 → 120 completed + 30 cut**; 60 pairs | integrated `d8a4825` vs receipt `3b7d702` | 43/46 | 32/30; 33/33 | 35/9; 37/6 | 9/6; 14/11 | 29/37 | Receipt not adopted now. Targeted Laya C6 +8 (15:7) but within noise; C5 final leans opposite 4:7; power-route refusals double. Baseline stays `d8a4825`. | `R23 (10-23)` |

## Week of 23–26 October: C24–C38

The same endpoint caveat applies: the `Laya C6 v2` column is the ops endpoint,
not deterministic strict C6. C33 onward also reports strict C6 separately.

| Cohort (date) | Plan → terminal | Tested change | C2 | C3 E/F | C5 E/F | Strict/legacy C6 E/F | Laya C6 v2 | Decision / lesson | Source |
|---|---|---|---|---|---|---|---|---|---|
| **C24** 23 Oct | **120/120**, 60 per arm | control `d8a4825` vs steel `5fb6371` | 42/41 | 25/24; 31/29 | 30/9; 33/9 | 5/3; 8/5 | 23/26 | Steel not adopted. Preregistered C2 12:13; steel slower to strict C2 (21:33). | `R24 (10-23)` |
| **C25** 23–24 Oct | **120/120**, 60 per arm | control vs backing `65604cd` | 46/44 | 33/32; 28/27 | 35/8; 33/8 | 13/9; 10/5 | 31/29 | Backing not adopted. Slower to strict C3 (14:28); primary Laya C6 10:12. | `R25 (10-24)` |
| **C26 → C26b** 24 Oct | **60/60**, 30 per arm; C26 was 60 cancelled before start | control vs ready (mall loan reconciliation) | 22/19 | 18/15; 11/11 | 19/2; 14/3 | 6/2; 3/3 | 17/11 | Ready loans not adopted. C2 3:6, C3 2:9, Laya C6 3:9. Seven-thousand-two-hundred-second window, unlike C24/C25. | `R26b (10-24)` |
| **C27 → C27b** 24 Oct | **60/60**, 30 per arm; C27 was 60 cancelled before start | control vs linear3 wait pacing | 24/20 | 18/16; 12/11 | 20/5; 12/2 | 7/5; 6/5 | 16/10 | Linear3 not adopted; C2 4:8, C5 4:12, Laya C6 4:10. Stalls sooner. | `R27b (10-24)` |
| **C28** 24 Oct | **60/60**, 15 per arm | A/A control_a/control_b; steel30; feedstock16 | 9/11/12/12 | 6/5; 4/4; 9/7; 5/5 | 4/1; 7/1; 8/2; 7/0 | 1/1 each | 1/1/1/1 | Separate policy-weight screen, not the C29+ base line. A/A C2 4:2, C3 3:5; steel30 C3 9 vs 6/4 is within noise; feedstock thin. No adoption. | `E28`, `I24` |
| **C29** 24 Oct | **120/120**, 60 per arm | control `daefefc` vs release `5a5ed28` | 44/50 | 25/23; 38/36 | 30/4; 38/5 | 13/8; 15/13 | 27/30 | **Adopted release `5a5ed28` as the cohort base.** C3 19:6 (p≈0.015), C2 faster 13 min RMST, C1 0→3. No production promotion. | `R29 (10-24)` |
| **C30** 24–25 Oct | **120/120**, 60 per arm; C30a 40/40 and S30r 48/48 reported separately, not pooled | release `5a5ed28` vs site `f765d30` | 50/47 | 40/39; 36/35 | 32/12; 37/16 | 10/6; 8/6 | 20/25 | Site not adopted; preregistered C2 leans 6:9 and site is slower. C30a: C2 0:3, C5 0:8. S30r (Starship): C2 2:4. Keep strata separate. | `R30 (10-25)` |
| **C31** 25 Oct | **120 → 85 completed + 35 cancelled queued** | census `de40332` vs outblock `7c63333` | invalid | invalid | invalid | invalid | 21/43 vs 15/42 obs. | Census acceptance defect: every completed run strict C0 only. This is **not an adoption result**; C34 reruns the comparison after repair. | `E31`, `L` |
| **C32** 25 Oct | **120 → 120 cancelled before any start** | clearance candidate | — | — | — | — | — | No jobs, no result. Superseded by C33. | `Q` |
| **C33** 25 Oct | **120/120**, 60 per arm | control `5a5ed28` vs clearance `ba31bec` | 50/52 | 44/38; 37/36 | 35/6; 38/12 | 10/8; 11/7 | 26/29 | Clearance not adopted: C3 regression 7:14, strict C6 11:10 (primary) level. C5 is c5-v1 here. | `R33 (10-25)` |
| **C34** 25 Oct | **120/120**, 60 per arm | census `9d9b279` vs outblock `9d8216d` | 51/48 | 47/47; 45/45 | 35/14; 37/9 | 10/9; 11/10 | 24/29 | Outblock not adopted; primary strict C3 8:10. Census held (4,323 checks, 0 evidence errors), unlike C31. | `R34 (10-25)` |
| **C35** 25–26 Oct | **120/120**, 60 per arm | census `9d9b279` vs LHI `2c5dfbc` | 48/54 | 42/42; 51/51 | 31/5; 35/15 | 8/4; 6/6 | 27/21 | Finite LHI not adopted: primary strict C6 4:6; C3/C5 gains secondary; Laya C6 falls 10:16. | `R35 (10-26)` |
| **C36** 25–26 Oct | **120/120**, 60 per arm | control `d9402da` vs clearance `cdb16a2` | 47/48 | 31/28; 34/34 | 31/17; 38/7 | 5/2; 15/13 | 20/32 | **Held, not adopted.** Strict C6 5→15 (12:2, p≈0.013) is the strongest recent signal, but untyped faults rise 12→36 (11→21 runs) and C5 final falls 17→7. | `R36 (10-26)` |
| **C37** 26 Oct | **120/120**, 60 per arm | control `d9402da` vs robots `32c1ef5` | 53/50 | 36/36; 37/36 | 36/8; 36/12 | 11/8; 7/6 | 29/26 | Robot-only occupancy exclusion not adopted. Primary strict C6 5:9; C5 final 10:6 but inside noise. | `R37 (10-26)` |
| **C38** 26 Oct | **120 → 119 completed + 1 `prepare_failed`**; 59 complete pairs | robots `9764fb6` vs clearance_robots (distinct frozen diff) | 0/0 | 0/0; 0/0 | 0/0; 0/0 | 0/0; 0/0 | none obs. | Both arms fail C2 and never start an oil cell; 94/119 reach proxy C0, 23 proxy C1. **Not a C36 clearance replication**; 1 preparation failure retained (`EADDRINUSE`, holder unknown). | `R38 (10-26)` |

## Week of 26–28 October: converged strict cohorts C39–C43

These cohorts use the converged tree and report deterministic strict C2–C6.
`C6 F` is strict final. A separate Laya v2 receipt is not included in the strict
columns; where relevant it is named in the decision text.

| Cohort (date) | Plan → terminal | Tested change | Strict E C2/C3/C4/C5/C6 (per arm /N) | C6 F | Key paired result / counterpoint | Decision | Source |
|---|---|---|---|---|---|---|---|
| **C39** 26–27 Oct | **120/120**, 60 pairs, seeds 80240–80299, 540k/7,200 s | reference `9d9b279` vs integrated `f098f9f` | ref 53/50/39/39/13; integrated 13/7/0/0/0 | 8 / 0 | C6 ever 13:0, p≈0.00024; C2 53:13. Whole-base comparison, not one factor. C5 predicates incompatible (v1 vs v2), so C5 not paired. | Retain reference; reject integrated. No adoption. | `R39 (10-27)`, `T` |
| **C40** 27 Oct | **80/80**, 40 pairs, seeds 80240–80279 (overlaps C39), **216k tick cap** | control `876b778` vs gateheld `7067c55` | control 12/6/0/0/0; gateheld 32/5/0/0/0 | 0 / 0 | C2 24:4 (12→32), strong early effect; C3 6→5 and no C4–C6. Short window; not a long-window result. | Keep gate fact as an intermediate finding only. No adoption. | `E40`, `T` |
| **C41** 27–28 Oct | **120/120**, 40 pairs per comparison, seeds 80400–80439 | ref `9d9b279` / gateheld `7067c55` / mineres `5fd2a5e` | ref 32/30/23/23/8; gateheld 34/5/2/2/0; mineres 22/15/3/3/0 | 7 / 0 / 0 | Reference C6 8:0 vs each main arm. Gateheld has C2 34 but C3 5; mineres trades C2 34→22 for C3 5→15 and has 343 untyped events (319 petroleum-gas). C5 ref is version-unknown (40/40), so its C5 is descriptive only. | No adoption; modify narrow reservations/fluid options. | `E41`, `T` |
| **C42** 27–28 Oct | **120/120**, 40 pairs, seeds 80440–80479; no reference arm | gateheld `7067c55` / siting `bf6d748` / resiting `3139909` | gateheld 33/9/6/6/0; siting 37/15/9/9/1; resiting 26/21/15/15/0 | 0 / 1 / 0 | Resiting vs siting: C2 1:12 (p≈0.003), C3 9:3; siting vs gateheld C3 7:1. Reservation again buys C3 by losing C2. All C5 are c5-v2. | No adoption; siting/reservation remain hypotheses. | `E42`, `T` |
| **C43** 28 Oct | **120/120**, 60 pairs, seeds 80480–80539 | reference `9d9b279` vs accell `1f63435` | ref 56/55/44/44/12; accell 51/47/38/38/11 | 10 / 7 | C6 ever accell-only:ref-only 7:8; C3 4:12; C2 4:9. C5 version labels absent in **60/60** pairs, so no version-qualified paired C5 claim. Typed faults: ref power-route 16, untyped 21, collision 2; accell power-route 13, untyped 31. | **Reject accell**, retain reference. No C6 gain; the rule cannot fire before C4, so the early C2/C3 deficit is not the late-stage mechanism claim. No adoption. | `E43`, `F43`, `T` |

## Model and infrastructure trials

These are separate from the numbered cohort series. They retain strict
checkpoint measurements, but their policy/runtime packages and exposure differ;
do not pool their counts with the numbered cohorts.

| Date | Trial | Plan → terminal | Arms | Result | Decision and source |
|---|---|---|---|---|---|
| 27 Oct | `laya-model-base-2123-miniship-20261026` | **120/120 terminal** = 30 seeds × 4 arms; 7 original + 113 transferred attempt-0; 113 administrative `cancelled_queued` copies add no trials | guided `2511e4`; released Kev; Vela; Jiwo | Strict C6 ever/final: guided **7/5**; Kev 0/0; Vela 0/0; Jiwo 0/0 (each /30). C2 final 26/1/5/0; C3 final 22/0/2/0. Learned-vs-guided 0:7, p≈0.016; final 0:5, p≈0.063. | Retain guided; set aside all three learned arms. **Not a clean causal model-quality estimate**: every original job is `comparison_eligible:false` (menu/legality/weight reconstruction unverified); strict endpoints are source-bound c2-v1…c6-v1 without per-record version labels; latency and exposure differ. `M` |
| 28 Oct | GPU Kev weights trial `laya-gpukev-...-20261028` | **90 planned → 8 completed + 4 endpoint-connection faults + 2 operator-stop casualties + 76 not started**; ended early at 06:07 IST | guided; released Kev; weak-v3 Kev | Completed 2/3/3; the four connection faults and two operator interruptions are infrastructure/operator, not policy failures. Eight completions cannot rank weights. | No weight-improvement verdict. Preserve the 6 faults and 76 unstarted in accounting; a continuation needs a new frozen registry. `Q`, `T` |

## Historical pending snapshot — 28 October, not current status

Snapshots are time-stamped and go stale. Do not rank arms from unequal terminal
samples.

| Cohort | Snapshot (2026-10-28) | Design | Status | Source |
|---|---|---|---|---|
| **C44** | 14:58 IST: **36 completed / 15 running / 149 queued** of 200; the 15:11 read was 42 completed / 16 running / 142 queued, showing the snapshot is rolling | 40 seeds 80570–80609 × control `bf6d748`, steel `7e70e00`, ownservice `9ccdd1c`, nofluid `c054f95`, reference `9d9b279`; deadline 30 Oct 06:00 IST | Pending. The steel arm is the primary candidate; ownservice/nofluid are scope/typing tests. No interim ranking. | `Q`, `T` |
| **C45** | 15:11 IST: **120 queued / 120** | 60 seeds 80610–80669 × reference `9d9b279` vs reference-line steel `33a7c73`; deadline 31 Oct 06:00 IST | Queued; no endpoint evidence. | `Q`, `T` |
| **C46** | 15:11 IST: **120 queued / 120** | 60 seeds 80670–80729 × reference `9d9b279` vs consumer `9462d4c`; deadline 1 Nov 06:00 IST | Queued; no endpoint evidence. The v3 consumer receipt must be read from journals; the harness reports v2. | `Q`, `T` |

## Current best and recurring bottlenecks

**Best observed late-stage benchmark:** reference `9d9b279` (C29 policy
`5a5ed28` plus the verified census fix), not the integrated main line and not a
learned model. Its observed strict C6 ever/final is:

- C43: **12/60 ever, 10/60 final** (C2/C3/C4/C5 56/55/44/44).
- C39: **13/60 ever, 8/60 final** (53/50/39/39).
- C41: **8/40 ever, 7/40 final** (32/30/23/23).

These are different seed cohorts, not a trend or independent replications. They
show C2 reach of 80–93% and a large C5-ever/C6-never group. Checkpoints are
independently evaluated; aggregate counts do not establish conditional reach.

**The dominant late-stage gap is C5 → C6, not C2.** In C43 reference, 44 runs
reach C5 but only 12 reach strict C6; 32 are lost. In C39 the corresponding
loss is 39→13, and in C41 23→8. The C43 review found that 29/32 C5-ever,
C6-never runs did observe new advanced-circuit output, while **27/32 never
observed an owned advanced-circuit consumer producing**; five saw both facts
separately but never simultaneously. 22/32 entered S1 and
competed for construction supply. The recurring shortage names are
passive-provider-chest (685 events/23 runs), long-handed inserter (640/29) and
assembling-machine-1 (589/27). Three runs had wait-only menus while advanced
circuits were `output_blocked`. This is a construction-closure, ownership and
option/acceptance gap, not simply “no advanced circuits were made.”

**Earlier-stage bottlenecks remain siting and shared construction supply.**
C41 gateheld raised C2 to 34/40 but C3 fell to 5/40; C42 siting improved C3 but
resiting lost C2 1:12. Steel-seed site waits, foundation chest/inserter/EMD
supply, mall-cap pressure, power-route/collision refusals and unsupported
fluid-produce options recur. These are candidate facts, not one proven cause.

**Counterexamples to keep visible.** C40’s gate-held fact raised C2 12→32 but
did not restore C3/C6 in C41. C36’s clearance raised C6 5→15 while untyped faults
rose 12→36 and C5 final fell 17→7. C30’s site arm had better C5 ever (37 vs 32)
but worse C2 (47 vs 50) and was not adopted. C28’s steel30 C3 lead (9 vs 6/4)
was inside A/A noise. C7’s netstall improved C3 20→37 but not C2. C13’s menu
raised C5/C6 but lowered C2. The index records these as rejected or held, not
as wins hidden by a single endpoint.

**Fault reporting is not “zero faults.”** `failures=0` is the caught-exception
counter. C43 reference has power-route 16, untyped 21 and collision 2 events;
accell has power-route 13 and untyped 31. C41 mineres has 343 untyped events
(319 petroleum-gas). C1 gate’s 71/71 rejected raises sat beside a zero summary
failure count; C7 separately retained a caught `KeyError`. Always report typed and untyped events,
affected runs, and whether an event is a policy failure, a safe refusal or an
infrastructure fault.

## Reporting traps and known gaps

- **Proxy is not strict.** A `DECISION phase=C4` or a proxy C4 can occur while
  strict C2–C5 fail. Do not add proxy and strict counts.
- **Ever is not final.** A final pass is the last sample, not sustained service.
  C9 batch reports C5 15 ever/11 final; C18 C5 44→12; C43 C5 44→12.
- **Do not claim version-qualified C5 wins for C43.** Per-record C5 version
  labels are absent in all 60 C43 pairs; the report excludes C5 from
  version-matched paired analysis. C43 reference C5-ever 44/60 is descriptive only. C41’s
  reference C5 is unknown in 40/40 pairs; C39’s C5 predicates are v1/v2
  incompatible.
- **Do not call counts independent terrain or a trend.** C40 reuses C39 seeds;
  every numbered cohort here uses policy seeds on one copied save. Exact paired
  p-values are exploratory, not multiplicity-adjusted.
- **Do not call `completed` a mission win.** Most completed runs are stalls or
  window timeouts. C31 completed 85/120 but every completed run was strict C0
  only; that is an invalid measurement, not a 0% policy result.
- **C4 evidence gap:** ledger says 96/96 completed, but the C4 audit could not
  recover local raw terminal statuses; C4 is therefore lower-confidence than
  its one-line ledger entry suggests.
- **Provenance is abbreviated in older rows.** C1–C6 and C8–C10 name the arms
  but do not repeat every frozen SHA; the cited audits/ledger hold those roots.
  C12 onward rows carry arm SHAs where the result reported them.
- **C20/C21** were stopped and superseded; no aggregate endpoint totals are
  claimed. **C8/C32** never started. **C38** has one preparation failure.
- **Historical pending snapshots** are not results. C44’s 14:58 numbers were
  already different by 15:11; the old C45/C46 queue entries were superseded.

## Maintenance instructions

Append or edit a row only after the cohort’s **terminal accounting** is closed:

1. Record the cohort ID, all planned slots and every terminal class:
   `completed`, `prepare_failed`, `interrupted`, `runner_error`,
   `not_started`/deadline, `cancelled_queued`, user cut/stopped and superseded
   attempts. Make the classes sum to the planned count, or state what is
   unknown. Do not drop crashes, cuts or stale queued jobs.
2. Record the frozen identity: arm SHAs (or freeze hash), engine/build, host,
   seed range, tick cap, wall cap and snapshot time. Note when a row is a
   retry or an A/A stratum.
3. Record the predicate version and both **ever** and **final** for strict
   endpoints. Keep proxy and Laya v2 receipts in separate columns. If a version
   label is missing, write `unknown`; do not pair it.
4. Record the decision (`adopted`, `rejected`, `held`, `invalid`,
   `superseded`) and any lifecycle action. Link the detailed result or review;
   do not paste the review into this index.
5. Keep active/queued cohorts in the pending table, never in the results
   tables. Move a pending row only when terminal accounting exists; delete or
   refresh a stale snapshot; retain historical bytes in the evidence archive.
6. Keep Starship, isolated lab/geometry/mall and model-package trials separate.
   Do not pool different engine windows, predicate versions or hosts.

## Source key

Local evidence paths are relative to the repository root. Frozen cohort copies
under `data/` are local evidence and may not be tracked in Git; they are cited
as paths, not as stable links.

- `L` — `data/laya-orchestration/c40-20261027/code/docs/rl/Laya_Cohort_Ledger_2026-10-21.md`
- `A1-3` — `docs/rl/laya_helper_runs/c1-c9-audit-20261020/cohorts1-3.md`
- `A4-6` — `docs/rl/laya_helper_runs/c1-c9-audit-20261020/cohorts4-6.md`
- `A7-9` — `docs/rl/laya_helper_runs/c1-c9-audit-20261020/cohorts7-9.md`
- `A20-21` — `docs/rl/laya_helper_runs/cohort-review-20261023/completed-cohorts.md`
- `Rnn (MM-DD)` — `data/laya-orchestration/c40-20261027/code/docs/rl/Laya_C<label>_Result_2026-MM-DD.md`, where `<label>` is the row label (including `26b`/`27b`)
- `E28` — `data/laya-experiments/laya-c28-2123-miniship-20261024/report.md`
- `E31` — `data/laya-experiments/laya-c31-2123-miniship-20261024/report.md`
- `E40` — `data/laya-experiments/laya-c40-2123-miniship-20261027/report.md`
- `E41` — `data/laya-experiments/laya-c41-2123-miniship-20261027/report.md`
- `E42` — `data/laya-experiments/laya-c42-2123-miniship-20261027/report.md`
- `E43` — `data/laya-experiments/laya-c43-2123-miniship-20261028/report.md`
- `F43` — `data/laya-orchestration/c40-20261027/findings/c43-review-codex.md`
- `T` — `docs/rl/laya_helper_runs/terra-review-20261028/cohorts.md` (helper synthesis; source-check critical claims against cohort reports)
- `Q` — `data/laya-orchestration/laya-loop/log.md` and `queue.json` (rolling snapshots; time-stamp them)
- `S08` — `docs/rl/Laya_System_Audit_2026-10-08.md`
- `S09` — `docs/rl/Laya_Session_Handoff_2026-10-09.md`
- `W17` — `docs/rl/Laya_Weekly_Reset_Audit_2026-10-17.md`
- `H` — `docs/rl/Laya_Current_Handoff.md`
- `B16` — `docs/rl/laya_helper_runs/combined-batch-20261016-night.md`
- `B17` — `docs/rl/laya_helper_runs/combined-batch-20261017-day.md`
- `I24` — `data/laya-orchestration/takeover-20261021/insight_helper_runs/20261024/synthesis.md`
- `M` — `docs/rl/Laya_Model_Result_2026-10-27.md` and `docs/rl/Laya_Model_Cohort_2026-10-27.md`
