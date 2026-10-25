<!-- Path: docs/rl/Laya_C18_Result_2026-10-23.md | Purpose: Complete C18 accounting, endpoints and the adoption decision for the loan-credit fix. -->
# Cohort 18 result

`laya-c18-2123-miniship-20261022` compared release `d39b6c7` with credit `e37464d`. The credit fix spends a bootstrap loan's prerequisite credit on the final crafts it funded, so a renewed batch is no longer planned on credit that was already consumed. Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, harness 65d5613 behind the host scheduler. It ran from 2026-10-22 15:09 to 2026-10-23 01:39 IST.

**Accounting: 150 originally planned = 120 completed + 30 cut by the user before starting.** The cut (2026-10-22 23:05) removed seeds 79460–79474; those jobs are listed in the root's `cut-jobs.json`, and seeds 79400–79459 are complete in both arms. None of the 12 runs live during the cut was interrupted. 115 runs stopped `stalled` and 5 hit the window cap (release 1, credit 4). No runner errors, tool errors or safety stops.

| Arm (60) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| release | 42 | 27/24 | 29 | 29/8 | 24 | 7/4 |
| credit | 51 | 35/32 | 44 | 44/12 | 35 | 10/9 |

Paired contrasts, 60 pairs (credit-only : release-only, two-sided sign test): C2 **13:4 (0.049)**, C3 17:9 (0.17), C5 ever **20:5 (0.004)**, C5 final 11:7 (0.48), new Laya C6 20:9 (0.061).

**Decision: adopt credit `e37464d` into the cohort base** per `docs/rl/laya_variants/README.md`. Long-window target completions rise on matched conditions: C4/C5 +15, new Laya C6 +11, legacy C6 +3. Earlier service also improves (C2 +9, C3 +8). Tool errors and safety stops stay at zero. C5 ever 20:5 survives a Bonferroni correction over the five contrasts. The C2 and C5 gaps are outside the A/A spread (identical code: C2 5:12 on 33 seeds). This adopts the experiment only; there is no production or real-base promotion.

| Failure class (events/runs) | release | credit |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 22/10 | 10/10 |
| infrastructure collision | 0/0 | 0/0 |
| power route refusal | 11/9 | 17/9 |
| loan-release refusals (journals) | 1 in 1 world | 1 in 1 world |

The remaining refusals are `release_recipe_changed`, typed as `mall_cell_lifecycle_conflict`. Credit's extra window caps (4 vs 1) and its stop classes are in the local analysis `data/laya-orchestration/takeover-20261021/checkin_helper_runs/c18-stop-classes-20261023.md` (pending at writing).

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization, deployed runtime or live result. Sources: `data/laya-experiments/laya-c18-2123-miniship-20261022/{freeze.json,batch.json,batch.pre-cut.json,cut-jobs.json,job-status.json}`, runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py}`.
