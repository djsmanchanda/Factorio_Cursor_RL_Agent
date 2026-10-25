<!-- Path: docs/rl/Laya_C23_Result_2026-10-23.md | Purpose: Complete C23 accounting, endpoints and the decision on the C6 receipt menu fix. -->
# Cohort 23 result

`laya-c23-2123-miniship-20261023` compared integrated `d8a4825` with receipt `3b7d702`. Integrated is the credit base plus the Starship review integration, without loan retention and with `b372672` reverted. Receipt adds one change: while the C6 explicit-choice receipt is open in phase C6, advanced circuits stay selectable for one more batch. Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, harness `3b7d702` behind the host scheduler. It ran from 2026-10-23 05:56 to 15:40 IST.

**Accounting: 150 originally planned = 120 completed + 30 cut by the user before any job started** (2026-10-23 06:30; seeds 79460–79474, listed in `cut-jobs.json`). Seeds 79400–79459 are complete in both arms. 118 runs stopped `stalled` and 2 hit the window cap (both integrated). No runner errors, tool errors or safety stops.

| Arm (60) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| integrated | 43 | 32/30 | 35 | 35/9 | 29 | 9/6 |
| receipt | 46 | 33/33 | 37 | 37/6 | 37 | 14/11 |

Paired contrasts, 60 pairs (receipt-only : integrated-only, two-sided sign test): C2 14:11 (0.69), C3 13:12 (1.0), C5 ever 13:11 (0.84), C5 final 4:7 (0.55), new Laya C6 **15:7 (0.13)**.

**Decision: not adopted now; the baseline stays integrated `d8a4825`.** The targeted endpoint moves the intended way: new Laya C6 is +8 and legacy C6 +5, and earlier service does not regress (C2 +3). The rule's other conditions also hold: no tool or safety failures, and no earlier-service regression. But the gap is one moderately sized discordance (15:7) among five contrasts, within run-to-run noise. C5 final leans the other way (4:7). Power-route refusals double (18 in 11 runs vs 9 in 8), which the mechanism does not explain. A replication on the chosen baseline should precede adoption; this is recorded explicitly, not silently. The C24/C25 queues, frozen against `d8a4825`, therefore keep their base, subject to C22's integration-neutrality result.

| Failure class (events/runs) | integrated | receipt |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 24/9 | 25/18 |
| infrastructure collision | 1/1 | 1/1 |
| power route refusal | 9/8 | 18/11 |
| loan-release refusals (journals) | 1 in 1 world | 4 in 3 worlds |

Remaining release refusals are `release_recipe_changed`, typed as `mall_cell_lifecycle_conflict`. Much of the run time overlapped scheduler stretches below the UPS floors (p95 down to about 83), which shared host load affects equally in both arms.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c23-2123-miniship-20261023/{freeze.json,batch.json,batch.pre-cut.json,cut-jobs.json,job-status.json}`, runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py}`.
