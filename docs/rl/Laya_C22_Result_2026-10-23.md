<!-- Path: docs/rl/Laya_C22_Result_2026-10-23.md | Purpose: Complete C22 accounting, endpoints and the baseline decision (integration and loan retention). -->
# Cohort 22 result and baseline decision

`laya-c22-2123-miniship-20261023` ran three cumulative arms:
- credit `e37464d`, the adopted base;
- integrated `d8a4825`: the Starship review integration (operation session/observation refactor, exact foundation and stock/flow acceptance diagnostics, typed faults), without loan retention and with `b372672` reverted;
- retain `3d221aa`: integrated plus an active final-recipe loan that keeps its cell while its next craft is supplied, owned WIP supplies it, or the machine works.

Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, harness `d8a4825` behind the host scheduler. It ran from 2026-10-23 05:53 to 17:38 IST.

**Accounting: 150 originally planned = 120 completed + 30 cut by the user before any job started** (2026-10-23 06:30; seeds 79440–79449, listed in `cut-jobs.json`). Seeds 79400–79439 are complete in all three arms. 113 runs stopped `stalled` and 7 hit the window cap (credit 3, integrated 2, retain 2). No runner errors, tool errors or safety stops.

| Arm (40) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| credit | 28 | 19/17 | 25 | 25/5 | 23 | 11/6 |
| integrated | 30 | 24/22 | 23 | 23/7 | 18 | 6/6 |
| retain | 31 | 22/18 | 27 | 27/1 | 27 | 11/3 |

Paired contrasts, 40 pairs each (newer-only : older-only, two-sided sign test):

| Contrast | C2 | C3 | C5 ever | C5 final | New C6 |
| --- | --- | --- | --- | --- | --- |
| integrated vs credit | 9:7 (0.80) | 9:4 (0.27) | 8:10 (0.81) | 7:5 (0.77) | 6:11 (0.33) |
| retain vs integrated | 5:4 (1.0) | 8:10 (0.81) | 9:5 (0.42) | **1:7 (0.07)** | **14:5 (0.06)** |

| Failure class (events/runs) | credit | integrated | retain |
| --- | ---: | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 | 0/0 |
| untyped_stuck | 10/10 | 14/10 | 19/11 |
| infrastructure collision | 0/0 | 0/0 | 1/1 |
| power route refusal | 9/6 | 14/8 | **90/8** |
| loan-release refusals (journals) | 2 in 2 worlds | 0 | 2 in 2 worlds |

## Baseline decision (2026-10-23 18:16 IST): integrated `d8a4825`

- **Integration accepted as the base.** It is a refactor plus acceptance diagnostics and is meant to change no decision. Across 40 pairs no endpoint moves consistently. C2 and C3 lean toward integrated (9:7, 9:4), while C5 ever and new Laya C6 lean toward credit (8:10, 6:11). Legacy C6 ever is 6 vs 11, and its final is equal (6/6). Tool errors and safety stops stay at zero. This is read as neutral within run-to-run noise, not as an improvement. The C6 lean toward credit is recorded and should be checked again in the C24/C25 control arms (both run `d8a4825`).
- **Retention `3d221aa` not adopted.** New Laya C6 rises (14:5), but C5 final collapses: 1 of 40 retain runs keeps it, against 7 integrated (1:7). Power-route refusals rise about sixfold (90 in 8 runs vs 14 in 8). The mechanism keeps loans on their cells; the refusal increase and the final-window plastic loss are not explained. Explain them before any retest.
- **Receipt `3b7d702` not adopted** (C23: new C6 15:7, but C5 final 4:7 and refusals doubled); see the [C23 result](Laya_C23_Result_2026-10-23.md).
- **Consequence:** the chosen baseline equals the base frozen in C24 and C25, so their gate is released as frozen. No queue is cancelled or rebuilt.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c22-2123-miniship-20261023/{freeze.json,batch.json,batch.pre-cut.json,cut-jobs.json,job-status.json}`, runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py}`.
