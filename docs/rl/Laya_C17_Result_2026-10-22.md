<!-- Path: docs/rl/Laya_C17_Result_2026-10-22.md | Purpose: Complete C17 accounting, endpoints and the decision for the never-configured loan release fix. -->
# Cohort 17 result

`laya-c17-2123-miniship-20261022` compared foundation `7fd2320` (C16's adopted base) with release `d39b6c7` (a bootstrap loan that was only proposed, never configured, is not released; a refused mall-cell release raises a typed `mall_cell_lifecycle_conflict` instead of `infrastructure_collision`). Seeds 79400–79474, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, harness 65d5613 behind the host scheduler. It ran from 2026-10-22 06:30 to 20:06 IST.

**Accounting: 150 planned = 150 completed**, 75 per arm, no reruns. 141 runs stopped `stalled` and 9 hit the window cap (foundation 3, release 6). No runner errors, tool errors or safety stops.

| Arm (75) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| foundation | 60 | 39/36 | 47 | 47/12 | 40 | 12/8 |
| release | 59 | 39/37 | 45 | 45/7 | 41 | 16/10 |

Paired contrasts, 75 pairs (release-only : foundation-only, two-sided sign test): C2 11:12 (1.0), C3 18:18 (1.0), C5 ever 16:18 (0.86), C5 final 7:12 (0.36), new Laya C6 19:18 (1.0).

**Mechanism confirmed.** Journalled loan-release refusals fall from 169 events in 30/75 foundation worlds (168 `release_owner_missing`, 1 `release_recipe_changed`, all reported as `infrastructure_collision`) to 1 event in 1/75 release worlds, now typed as `mall_cell_lifecycle_conflict: release_recipe_changed`. Untyped stuck events halve (50 in 19 runs → 24 in 13).

| Failure class (events/runs) | foundation | release |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 50/19 | 24/13 |
| infrastructure collision | 171/32 | 0/0 |
| power route refusal | 20/14 | 23/12 |

**Decision: not adopted by the endpoint rule.** `docs/rl/laya_variants/README.md` requires more long-window target completions, and no endpoint moves: every contrast is level and within the A/A spread (identical code: C2 5:12 on 33 seeds). C5 final 7:12 is the only lean, against release; it is the plastic-plateau sample (C12 caveat), not a measured capability loss, and is watched in C18, whose arms both carry the fix. The change is kept in the experimental line as a lifecycle correctness fix: it removes a false collision class without an endpoint or safety regression. What release's 6 window-cap runs (against 3) were doing at the cap is part of the stop-class analysis.

Foundation's absolute levels here (C2 60/75) exceed its C16 arm (35/50) on overlapping seeds under different host load; compare arms only within a cohort.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization, deployed runtime or live result. Sources: `data/laya-experiments/laya-c17-2123-miniship-20261022/{freeze.json,batch.json,job-status.json}`, runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py}`. Stop classes: local `data/laya-orchestration/takeover-20261021/checkin_helper_runs/c17-stop-classes-20261022.md` (pending at writing).
