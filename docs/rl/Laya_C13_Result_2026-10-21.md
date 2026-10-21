<!-- Path: docs/rl/Laya_C13_Result_2026-10-21.md | Purpose: Complete C13 accounting, endpoints and the no-adoption decision. -->
# Cohort 13 result

Unit `laya-c13-2123-miniship-20261021` ran on miniship with Factorio 2.1.23 build 87750, from 2026-10-21 00:47 until it exited `success` at about 16:40 IST. **Accounting: 144 planned = 144 completed**, 48 per arm on seeds 79400–79447, with no errors, interruptions or unstarted jobs. 139 runs stopped `stalled` and 5 hit the window cap (ref 2, loan 3). Same copied save, 540,000-tick / 6,000 s windows, unchanged policy and exploration. Slots were adaptive 6 (3–8) under the old per-cohort harness.

Cumulative arms: ref `830c022` (1f7980b path fixes + common C6 v2) → menu `42da7aa` (keep all legal bill-backed produce shortages) → loan `54df1d2` (finish unfinished loan prerequisites, keep delivered credits).

| Arm (48 completed) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ref | 18 | 8/8 | 12 | 12/3 | 10 | 1/1 |
| menu | 14 | 11/9 | 19 | 19/2 | 18 | 4/3 |
| loan | 18 | 6/6 | 19 | 19/2 | 17 | 5/2 |

New Laya C6 (`laya-c6-choice-output-v2`) totals 45/144. Legacy deterministic C6 is reported separately and unchanged.

Adjacent paired contrasts, 48 pairs each (newer-only : older-only, two-sided sign test):

| Contrast | C2 | C3 | C5 ever | C5 final | New C6 |
| --- | --- | --- | --- | --- | --- |
| menu vs ref | 6:10 (0.46) | 9:6 | 13:6 (0.17) | 2:3 | 14:6 (0.12) |
| loan vs menu | 11:7 (0.48) | 2:7 (0.18) | 11:11 | 2:2 | 11:12 |

**Decision: no adoption.** Menu raises C4, C5-ever and new Laya C6 (+7 to +8 runs), the largest positive signal so far, but no contrast reaches significance. Menu also lowers C2 (14 vs 18), an earlier-service guard. Loan recovers C2 but lowers C3 (stone line) with no C5/C6 gain over menu. Read against the A/A spread (identical code: C2 5:12 on 33 seeds), every gap here is within noise. C5 final is low in every arm (2–3/48). It largely reflects the single full plastic chest, not lost capability; see the C12 result.

| Failure class (completed; events/runs) | ref | menu | loan |
| --- | ---: | ---: | ---: |
| tool_error | 0/0 | 0/0 | 0/0 |
| safety stop | 0/0 | 0/0 | 0/0 |
| untyped_stuck | 4/3 | 5/5 | 5/5 |
| infrastructure collision | 17/6 | 43/10 | 41/9 |
| power route refusal | 12/7 | 9/7 | 7/6 |

Collisions rise about 2.5× in menu and loan; the stop-class analysis attributes them (section below, when complete). Loan's C3 deficit traced earlier: stone foundation success 2/33 loan vs 7 ref/menu at the 10:43 snapshot, with more stone-foundation material shortages, consistent with prerequisite finishing committing materials to the circuit chain.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization; not pooled with 2.1.22 attempts. Sources: `data/laya-experiments/laya-c13-2123-miniship-20261021/{freeze.json,batch.json,job-status.json,controller.jsonl}`, runner logs and journals, `data/laya-orchestration/takeover-20261021/{status.py,failures.py}`.
