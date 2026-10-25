<!-- Path: docs/rl/Laya_C7_Result_2026-10-20.md | Purpose: Record complete C7 accounting and the limited decision for C9. -->

# Cohort 7 completed

Observed 2026-10-20 05:23 IST on miniship. Both frozen parts completed: 96 + 300 = **396/396 planned jobs**, 198 matched seeds. No missing, interrupted or unstarted jobs. Factorio 2.1.22, 540,000-tick / 6,000-second windows; base `e18d8d0` versus netstall `d633957`. The discontinued Starship execution is excluded; its seeds were rerun in miniship-b.

| Historical deterministic acceptance | Base / 198 | Netstall / 198 | Netstall-only / base-only |
| --- | ---: | ---: | ---: |
| C0 | 198 | 198 | 0 / 0 |
| C1 | 1 | 3 | 3 / 1 |
| C2 | 80 | 78 | 44 / 46 |
| C3 | 20 | 37 | 28 / 11 |
| C4 | 1 | 4 | 4 / 1 |
| C5 | 1 | 4 | 4 / 1 |
| C6 | 0 | 0 | 0 / 0 |

These are **ever-passed** predicates. C7’s `accepted_final` was latched, so it cannot establish fresh final health. Proxy phase reaches are separate evidence. Base stopped as stalled in 197 runs and window_timeout in one; netstall stalled in 198.

Failure audit (all 198 pairs; raw outcomes and affected runs both counted):

| Outcome | Base events / affected runs | Netstall events / affected runs |
| --- | ---: | ---: | ---: |
| tool_error | 0 / 0 | 1 / 1 |
| infrastructure_collision | 13 / 12 | 26 / 20 |
| power_route_unavailable | 52 / 37 | 101 / 35 |
| untyped_stuck | 5 / 4 | 5 / 3 |

Netstall observed 43,812 decisions / 199.2 game hours versus 39,249 / 172.8 for base. Collisions remain higher both per decision (0.59 vs 0.33 per thousand) and per game hour (0.13 vs 0.08). At the shorter common game-time prefix, collision events are 18 vs 11, affected pairs netstall-only/base-only 13/9. Exposure adjustment does not establish safety equivalence; a nonsignificant sign test is not a no-regression result. The single tool error is the reproducible `construction-robot` endpoint-survey race in netstall-s78026, fixed in `4a5c057` for future runs.

Decision: **no adoption**. C3 improved, but C5 remains rare, C6 is zero, fresh final health is unavailable and collision counts rose. Netstall is only the experimental reference for C9; all arms receive the shared robot fix and fresh reporting. C9 targets C5/C6 with cumulative causal fixes and keeps C2/C3 as regression guards.

Evidence: `data/laya-experiments/laya-c7-20261019/{miniship,miniship-b}/{job-status.json,logs/*.out}`, each job’s owned `logs/laya-runner.log`, and `data/laya-orchestration/next-20261018/c7-failure-audit/paired_failures.py`. Final audit output is copied into C9 validation evidence. Existing frozen C7 code and worlds remain unchanged.

Lifecycle: C7 units ended normally; no restart or deployment required. New code is used only by new frozen disposable worlds.
