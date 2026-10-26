<!-- Path: docs/rl/Laya_C14_Result_2026-10-22.md | Purpose: Complete C14 (C14b + C14c) accounting, endpoints and the no-adoption decision for the steel-bill fact. -->
# Cohort 14 result (C14b + C14c)

C14 compares ref `c1b8680` (the integrated takeover head, code-identical to C13 loan) with steelbill `934f9cb` (the steel seed's unchanged-bill fact plus goal-serving bill items) on seeds 79400–79474. Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, unchanged policy and exploration.

**Accounting.** C14b (`laya-c14b-2123-miniship-20261021`, legacy shared-budget harness) ran from 2026-10-21 05:24 to its drain at ~17:40 IST: 150 planned = 71 completed + 79 never started. Its interrupted jobs from the 14:45 `--resume` restart were rerun in fresh worlds. Its unstarted seeds continued as C14c (`laya-c14c-2123-miniship-20261021`, scheduler-gated harness): 80 planned = 80 completed, including 4 reruns after the accidental 2026-10-22 02:39 power cut (resumed with `--resume`). Each seed is paired within one cohort: C14b for seeds ≤79434 (70 completed), C14c for 79435–79474 (80). C14b's lone `steelbill-s79435` run is superseded by C14c's pair for that seed. **75 complete matched pairs**; no runner errors, tool errors or safety stops; 148 runs stopped `stalled` and 2 hit the window cap.

| Arm (75 completed) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ref | 26 | 9/8 | 25 | 25/11 | 21 | 5/4 |
| steelbill | 18 | 10/10 | 20 | 20/5 | 17 | 4/3 |

Paired discordances (steelbill-only : ref-only, two-sided sign test): C2 10:18 (p=0.19), C3 8:7 (1.0), C5 ever 11:16 (0.44), C5 final 5:11 (0.21), new Laya C6 11:15 (0.56).

**Decision: no adoption; steelbill is dropped from future bases.** It improves no endpoint and trails ref on C2, C5 and new C6. None of these reaches significance, and the A/A spread (identical code: C2 5:12 on 33 seeds) covers them. The mechanism is consistent, though. In seeds where ref reached C2 and steelbill did not, steelbill chose `op.steel` 4–5× less often, `extend` about 2.5× less and `retire_starter` about 2× less. Its unchanged-failure fact is stock-based, so it suppressed steel-seed retries even after the missing items had been produced and consumed elsewhere. The next cohort therefore rebuilds the two promising fixes directly on ref (branch `work/miniship/laya-nosteelbill-20261021`: vacancy `0bf1bdc`, foundation `7fd2320`). C15d's arms carry steelbill; their adjacent contrasts stay valid, but their absolute levels are confounded.

Failure counts in C14c (completed; events/runs): ref collisions 62/7 and power-route refusals 81/12; steelbill collisions 26/9 and refusals 22/13; untyped 2/2 vs 6/5. The extra ref refusals come from a few runs; per earlier analysis, collisions here are mostly loan-release `release_owner_missing` events labelled as `infrastructure_collision`.

Evidence level: completed matched experiment cohort split across two supervisors on one copied save; not terrain generalization. Sources: both cohort roots (`freeze.json`, `job-status.json`, `job-status.before-*` snapshots), runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py}`.
