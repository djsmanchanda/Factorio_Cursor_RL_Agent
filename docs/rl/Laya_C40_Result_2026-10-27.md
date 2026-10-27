<!-- Path: docs/rl/Laya_C40_Result_2026-10-27.md | Purpose: Complete C40 accounting (gate-held phase goal fact) and the next-cohort decision. -->
# Cohort 40 result: gate-held phase goal

`laya-c40-2123-miniship-20261027` tested the cause of C39's collapse on main, where strict C2 fell to 13/60 against 53/60 for reference `9d9b279`.

**Mechanism found in C39:**
- Since `46eec3f`, standing mall gates follow current demand. Early gear and cable cells stop at small caps (`< 27` / `< 92`).
- Their measured rate stays under Laya's C1 proxy goal (gear 0.25/s, cable 0.5/s). C1 is never reached, so the lifecycle builder never offers the C2 foundations.
- 38 of 47 integrated never-C2 runs ended in C1 with a wait-only menu. This was verified independently by Codex and DeepSeek (`data/laya-orchestration/c40-20261027/findings/`).

**Arms:**
- **control:** main `876b778`, whose runtime is identical to C39's integrated `f098f9f`.
- **gateheld:** `7067c55`. It changes one function, `training/laya_factory_status.goal_met`. A goal machine held off by its own logistic stock gate now meets the proxy goal, provided its installed capacity covers the goal and it has stock on hand. Strict milestones are unchanged.

**Setup:**
- Seeds 80240–80279, the C39 seeds, so the C39 runs serve as a historical benchmark.
- Same copied save; a shortened window of 216,000 ticks / 2,400 s; ports 40500/33500.
- It ran from 12:09 to about 15:30 IST on 2026-10-27.

**Accounting:** 80 planned = 80 completed. Every return code was 0, with no failures, evidence errors or maintenance failures, and no reruns.

| Strict, ever (40 per arm) | control | gateheld | gateheld-only : control-only | p (sign test) |
| --- | ---: | ---: | ---: | ---: |
| C1 | 27 | 38 | 13:2 | 0.0074 |
| **C2 (primary)** | **12** | **32** | **24:4** | **0.00018** |
| C3 | 6 | 5 | 1:2 | 1.0 |
| C4–C6 | 0 | 0 | — | — |

| Measure | control | gateheld |
| --- | ---: | ---: |
| Final menu wait-only | 20 | **0** |
| Final proxy phase | C1 20, C2 9, C3 7, C4 4 | C2 10, C3 27, C4 3 |
| Stop: stalled / window | 29 / 11 | 21 / 19 |
| Decisions | 4,018 | 5,965 |
| Median wall s | 1,211 | 1,910 |
| untyped_stuck events | 58 | 39 |

**Historical benchmark:** C39 reference on the same seeds reached strict C2 within 3,600 game seconds in 35/40 runs, and C3 in 33/40. This is a different cohort, not a concurrent arm.

**Decision: test deeper, not adopted yet.**
- The fact repairs the C1 dead end it was written for. It removes every wait-only stall, and C2 nearly reaches the reference level within the window.
- The short window cannot grant adoption under `docs/rl/laya_variants/README.md`.
- Main still trails the reference at C3 (5 vs 33/40). Gateheld runs now stop in proxy C3, dominated by `temporary_mall_batch`, `steel_seed_site_wait` and `production_prerequisite_deferred`.

**Next:**
- A long-window cohort: reference `9d9b279` vs gateheld, plus a single-factor C3 repair on top once it is diagnosed.
- The reserved-project shortage fact `ff974c8` (prepared, fast 1,627 passed) is the next candidate arm. Codex and DeepSeek reviews of C40 and of the C2→C3 gap are running.

Evidence level: a completed matched cohort with a shortened window, on one copied save. Sources: `data/laya-experiments/laya-c40-2123-miniship-20261027/{freeze.json,batch.json,job-status.json}`, the per-run journals, and `data/laya-orchestration/c40-20261027/findings/`.
