<!-- Path: docs/rl/Laya_C35_Result_2026-10-26.md | Purpose: Complete C35 accounting and the convergence adoption decision. -->
# Cohort 35: Finite long-handed inserter batches

Decision recorded **2026-10-26 IST** by the convergence owner. **Not adopted: the strict C6 primary endpoint did not improve.**

`data/laya-experiments/laya-c35-2123-miniship-20261025`: **120 planned = 120 completed**, 60 matched seeds per arm, no omitted crashes, cuts or reruns. Both arms use Factorio 2.1.23 build 87750, the same copied save, 540,000 ticks and a 7,200-second wall allowance. Strict predicates and the separate Laya choice/output C6 endpoint are reported independently.

| Endpoint | census / 60 | lhi / 60 | Treatment-only : control-only |
|---|---:|---:|---:|
| Strict C1 ever | 0 | 0 | 0:0 |
| Strict C2 ever | 48 | 54 | 10:4 |
| Strict C3 ever | 42 | 51 | 13:4 |
| Strict C4 ever | 31 | 35 | 14:10 |
| Strict C5 ever | 31 | 35 | 14:10 |
| Strict C6 ever | 8 | 6 | 4:6 |
| Strict C3 final | 42 | 51 | 13:4 |
| Strict C5 final | 5 | 15 | 14:4 |
| Strict C6 final | 4 | 6 | 6:4 |
| Laya C6-v2 | 27 | 21 | 10:16 |

C1 is historical `c1-v1`, C5 is `c5-v1`, and strict C6 is `c6-v1`. Main's c1-v2 and the requested plastic-startup C5-v2 are not results of this cohort. Ever and final use sampled acceptance; a final pass does not establish sustained service.

Control `9d9b279` includes the corrected structural census. Treatment `2c5dfbc` adds only the finite borrowed-cell path for long-handed inserters. Strict C2/C3 counts are comparable within this cohort, not to census-free C33/C36 counts.

The mechanism was exercised: 34/60 treatment worlds executed 100 long-handed temporary batches; control executed none. Long-handed shortage mentions fell from 903 to 341 and advanced-circuit cell kits were less often left in `supply_wait`. Strict C3 rose 42→51 (13:4, exact two-sided p=0.049) and final C5 rose 5→15 (14:4, p=0.031). Those secondary gains do not replace the preregistered strict C6 endpoint: 8→6 (4:6, p=0.754), with Laya C6-v2 also falling 27→21. The two pairs reaching strict C6 in both arms were slower in treatment (median +13.6 game minutes). No adoption exception is taken.

Caught exceptions and evidence errors are zero. `untyped_stuck` events are 15→12. No safety promotion or real-base action follows from this result. The LHI candidate stays in its experiment/recovery history.

Evidence: the cohort's `batch.json`, `freeze.json`, `job-status.json`, `report.json`, per-run acceptance journals and runner logs; independent raw aggregation of all 120 terminal worlds. Local helper evidence is in `docs/rl/laya_helper_runs/convergence-20261026/{c35-c36-review.md,c35-c36-derived.json,recent-cohorts-review.md}`. These helpers are evidence; the decision above belongs to this tracked result. [Adoption rule](laya_variants/README.md).

Evidence level: completed matched experiment on one copied starting save. No terrain generalization, deployed-runtime result or real-base promotion. Lifecycle: no restart or deployment for this result; existing C37 stays untouched.
