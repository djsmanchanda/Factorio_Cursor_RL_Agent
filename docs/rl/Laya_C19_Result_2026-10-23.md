<!-- Path: docs/rl/Laya_C19_Result_2026-10-23.md | Purpose: C19 accounting (stopped early), endpoints and the revert of the started-foundation menu change. -->
# Cohort 19 result

`laya-c19-2123-miniship-20261022` compared credit `e37464d` with started `b372672`. The started change kept a started foundation on the menu after its plate was satisfied, while the world observed it not ready. Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, harness 65d5613 behind the host scheduler. It ran from 2026-10-22 20:31 IST.

**Accounting: 150 originally planned = 70 completed + 48 cut + 32 stopped, none of them started.** The user cut seeds 79451–79474 (48 jobs) on 2026-10-22 23:05. On 2026-10-23 the user stopped the cohort after `b372672` was reverted; the supervisor was frozen at 05:52 and stopped at 06:44 IST. Both lists are in the root's `cut-jobs.json`. No running world was interrupted. Seeds 79400–79434 are complete in both arms (35 pairs). 66 runs stopped `stalled` and 4 hit the window cap (credit 1, started 3). No runner errors, tool errors or safety stops.

| Arm (35) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| credit | 28 | 20/19 | 20 | 20/2 | 20 | 2/1 |
| started | 22 | 17/14 | 18 | 18/3 | 15 | 5/5 |

Paired contrasts, 35 pairs (started-only : credit-only): C2 3:9 (p=0.15), C3 5:8, C5 6:8, C5 final 3:2, new Laya C6 5:10 (p=0.30). At 20 pairs the gaps were C2 0:6 (p=0.03) and new C6 0:7 (p=0.016); they narrowed toward noise but stayed against started.

**Decision: reverted** (`d8a4825` on work/miniship/laya-integrated-20261022). The endpoint gap alone is within the A/A spread (identical code: C2 5:12 on 33 seeds), but the mechanism is measured and harmful. Power-route refusals rose from 3 in 3 runs to 80 in 16. Foundation readiness is False while bots are still building, so the menu re-offered the copper foundation in C1. Each re-pick re-ran the build: in the 20-pair analysis, 30 of 55 picks hit `power_route_unavailable` and re-earmarked drills the stone starter needed. The stone state it targeted never arose. Analysis: local `data/laya-orchestration/takeover-20261021/checkin_helper_runs/c19-started-regression-20261023.md`.

| Failure class (events/runs) | credit | started |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 21/11 | 7/7 |
| infrastructure collision | 1/1 | 1/1 |
| power route refusal | 3/3 | 80/16 |

C20 (`laya-c20-2123-miniship-20261022`, 33 completed + 117 stopped) and C21 (`laya-c21-2123-miniship-20261022`, 70 completed + 80 stopped) carried `b372672` in every arm. They were stopped the same way and are superseded by C22, which repeats their contrasts on the reverted base. Their completed runs are kept as evidence but not analysed for adoption.

Evidence level: incomplete matched experiment cohort (stopped by the user) on one copied save; not terrain generalization. Sources: the cohort roots' `freeze.json`, `batch.json`, `batch.pre-cut.json`, `cut-jobs.json` and `job-status.json`, plus `data/laya-orchestration/takeover-20261021/{status.py,failures.py}`.
