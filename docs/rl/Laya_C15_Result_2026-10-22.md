<!-- Path: docs/rl/Laya_C15_Result_2026-10-22.md | Purpose: Complete C15 (C15d) accounting, endpoints and the no-adoption decision. -->
# Cohort 15 result (C15d)

`laya-c15d-2123-miniship-20261021` ran three cumulative arms on seeds 79400–79449: steelbill `934f9cb` → vacancy `ef38247` (reuse released finite mall halves) → foundation `d8e3f7c` (observed-ready foundation advances the plate stage). Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows. It was the first cohort on the host scheduler's gate (harness 81f0735, later 65d5613 for `--resume`). Earlier C15 attempts (attempt 1, C15b, C15c) started no evaluable job and are excluded; see the ledger.

**Accounting: 150 planned = 150 completed**, 50 per arm. 145 runs stopped `stalled` and 5 hit the window cap. No runner errors, tool errors or safety stops. Eight jobs left running by the accidental 2026-10-22 02:39 power cut were rerun in fresh worlds under `--resume`; their dead attempts are recorded in job history.

| Arm (50) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| steelbill | 21 | 13/13 | 21 | 21/2 | 21 | 5/3 |
| vacancy | 25 | 19/16 | 24 | 24/3 | 21 | 9/6 |
| foundation | 27 | 17/13 | 21 | 21/3 | 18 | 7/4 |

Paired contrasts, 50 pairs each (newer-only : older-only, two-sided sign test):

| Contrast | C2 | C3 | C5 ever | C5 final | New C6 |
| --- | --- | --- | --- | --- | --- |
| vacancy vs steelbill | 12:8 (0.50) | 13:7 (0.26) | 9:6 (0.61) | 3:2 | 7:7 |
| foundation vs vacancy | 13:11 (0.84) | 8:10 | 8:11 (0.65) | 3:3 | 10:13 (0.68) |

**Decision: no adoption.** Both fixes lean positive on earlier service (vacancy +4 C2, +6 C3; foundation +2 C2), but every contrast is within the A/A spread (identical code: C2 5:12 on 33 seeds). Foundation's early lead (C2 6:1 at 18 pairs) regressed to 13:11, a reminder that interim pairs mislead. All arms carry steelbill, which C14 showed is likely harmful (ref 26 vs steelbill 18 C2). Absolute levels are depressed, though adjacent contrasts stay valid. C16 retests both fixes directly on ref.

| Failure class (completed; events/runs) | steelbill | vacancy | foundation |
| --- | ---: | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 | 0/0 |
| untyped_stuck | 16/7 | 10/7 | 18/5 |
| infrastructure collision | 50/13 | 64/10 | 45/10 |
| power route refusal | 34/16 | 12/9 | 16/11 |

Most "collisions" are loan releases refused with `release_owner_missing`: a never-configured loan was being released. `d39b6c7` fixes that and types the outcome; C17 tests it.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c15d-2123-miniship-20261021/{freeze.json,batch.json,job-status.json,job-status.before-reboot-resume.json}`, runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py}`.
