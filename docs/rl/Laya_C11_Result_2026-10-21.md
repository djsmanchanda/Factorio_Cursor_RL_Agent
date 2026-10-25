<!-- Path: docs/rl/Laya_C11_Result_2026-10-21.md | Purpose: Preserve complete C11 accounting and the no-adoption decision before the engine transition. -->
# Cohort 11 result

Final observation 2026-10-21 00:39 IST: **144/144 planned jobs completed**, 48 per arm, seeds 79400–79447, Factorio 2.1.22 build 87719. Frozen arms: copper `8bd4149`, ready `2ffefee`, reconcile `071c0aa`. Common windows: 540,000 ticks / 6,000 wall seconds, same copied save, unchanged policy/exploration. Evidence level: completed matched experiment cohort, not unseen terrain.

| Arm | C2 | C3 ever/final | C4 | C5 ever/final | Legacy C6 ever/final | Stops |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| copper | 17 | 14/14 | 16 | 16/14 | 0/0 | 48 stalled |
| ready | 18 | 12/12 | 17 | 17/15 | 1/1 | 47 stalled, 1 window timeout |
| reconcile | 18 | 12/11 | 16 | 16/15 | 0/0 | 47 stalled, 1 window timeout |

Adjacent paired comparisons (48 pairs, newer-only / older-only): ready vs copper C2 11/10, C3 8/10, C5 ever and final 10/9; reconcile vs ready C2 8/8, C3 7/7, C5 ever 9/10 and final 9/9. C5 two-sided sign-test p=1.00 in every adjacent comparison. No adoption: neither readiness nor reconciliation has a measurable target effect.

Legacy C6 uses defective current-recipe lifetime counters. The one accepted ready run is exploratory evidence, not a verified result under the user's new explicit-choice/output definition. Do not retrospectively relabel any C11 C6 result.

| Mechanism/failure count | copper | ready | reconcile |
| --- | ---: | ---: | ---: |
| Tool-error events / affected runs | 0/0 | 0/0 | 0/0 |
| Untyped-stuck / safety stops | 0/0 | 0/0 | 0/0 |
| Power-route refusals (events) | 3 | 63 | 7 |
| Infrastructure collisions (events) | 10 | 7 | 5 |
| Decisions / waits selected | 11258/1824 | 11278/1881 | 10718/1677 |
| Starter decisions / successful outcomes | 532/143 | 509/144 | 514/144 |
| Full-output readiness receipts | 0 | 7 | 1 |
| Menus exposing pending starter reconciliation | 0 | 0 | 1129 |

Diagnostic waiting remains unmeasurable from the available counter (zero matching text is not proof that none occurred). Starter success is about 27–28% in every arm. Reconciliation is frequently offered without a target-completion improvement.

Sources: `data/laya-experiments/laya-c11-20261020/{freeze.json,miniship/job-status.json}`, each job's runner journal/log, and `data/laya-orchestration/next-20261018/c9-analysis/{status.py,c11_extras.py}`. The earlier 37-C5-run blocker analysis remains a timestamped sample, not this cohort's final denominator.

Lifecycle: C11 finished unchanged. Its initial 2.1.22 successors were interrupted by the user's fleet-upgrade request and archived. Fresh C12/C13 use 2.1.23 on miniship; their new Laya C6 endpoint is separate from legacy deterministic acceptance. Starship runs are prohibited after 02:30 IST. See [the cohort ledger](Laya_Cohort_Ledger_2026-10-21.md) for complete takeover accounting.
