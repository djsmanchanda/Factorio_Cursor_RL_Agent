<!-- Path: docs/rl/Laya_C12_Result_2026-10-21.md | Purpose: Complete C12 accounting, endpoints and the no-adoption decision. -->
# Cohort 12 result

Unit `laya-c12-2123-miniship-20261021` ran on miniship with Factorio 2.1.23 build 87750, from 2026-10-21 00:47 until it exited `success` at about 14:20 IST. Seed admission closed at 13:05; a seed block starts only if its 6,000 s window fits before 14:45.

**Accounting: 192 planned = 160 completed + 32 `not_started_deadline`.** Seeds 79400–79439 completed in all four arms. Seeds 79440–79447 were never started, as whole blocks. There were no runner errors, interruptions or crashes. All 160 runs stopped `stalled` and none hit the window cap. Common windows were 540,000 ticks / 6,000 s, the same copied save, and unchanged policy and exploration.

Cumulative arms: ref `f82f4dd` (corrected copper + common C6 v2) → raw `d4d3af8` (raw-ore satisfaction) → furnace `1f7980b` (stocked-furnace legality, seed sizing removed) → seed `1f7980b` (+ transitive advanced-circuit seed sizing).

| Arm (40 completed) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ref | 15 | 10/9 | 14 | 14/5 | 12 | 1/1 |
| raw | 16 | 11/11 | 11 | 11/9 | 7 | 1/1 |
| furnace | 11 | 7/7 | 10 | 10/8 | 8 | 0/0 |
| seed | 7 | 9/9 | 6 | 6/1 | 6 | 0/0 |

C2 ever equals final in every arm. New Laya C6 (`laya-c6-choice-output-v2`: an explicit advanced-circuit choice followed by attributable output) totals 33/160. Legacy deterministic C6 is reported separately and unchanged.

Adjacent paired contrasts, 40 pairs each (newer-only : older-only, two-sided sign test):

| Contrast | C2 | C3 | C5 ever | C5 final | New C6 |
| --- | --- | --- | --- | --- | --- |
| raw vs ref | 8:7 (p=1) | 6:5 | 2:5 (0.45) | 7:3 (0.34) | 2:7 (0.18) |
| furnace vs raw | 6:11 (0.33) | 4:8 (0.39) | 6:7 | 5:6 | 6:5 |
| seed vs furnace | 2:6 (0.29) | 6:4 | 3:7 (0.34) | **1:8 (0.039)** | 3:5 |

**Decision: no adoption.** No arm improves a target endpoint. Seed's C5-final 1:8 is the only nominal p<0.05. It is one of 15 contrasts, and it falls inside the A/A spread below, so it is a warning, not an established regression. Furnace and seed lower C2 (11, 7 vs 16), an earlier-service guard failure that would block adoption anyway.

**A/A noise.** C12 `seed` and C13 `ref` are byte-identical frozen code running concurrently on the same save and seeds. On 33 common seeds they differed C2 6 vs 13 (discordant 5:12) and C5 4 vs 8 (2:6). Per-seed outcomes are strongly stochastic, so single-cohort contrasts of about ±7/40 are within run-to-run noise.

**C5-final caveat.** C5 final fails only `plastic_craft_counter_advances` in the inspected runs. In at least 7 of 20 such C12+C13 runs, network plastic sat at ~4,800 (one full passive provider), so the final sample partly measures leftover plastic consumption, not lost capability.

| Failure class (completed; events/runs) | ref | raw | furnace | seed |
| --- | ---: | ---: | ---: | ---: |
| tool_error | 0/0 | 0/0 | 0/0 | 0/0 |
| safety stop | 0/0 | 0/0 | 0/0 | 0/0 |
| untyped_stuck | 12/5 | 2/2 | 1/1 | 0/0 |
| infrastructure collision | 6/4 | 9/4 | 8/3 | 10/5 |
| power route refusal | 5/4 | 6/5 | 7/5 | 6/5 |

Ref's untyped events are mostly one repeated "modular refinery for stone-brick built but not healthy" (all cells `full_output`, no consumer).

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization; not pooled with 2.1.22 attempts. Sources: `data/laya-experiments/laya-c12-2123-miniship-20261021/{freeze.json,batch.json,job-status.json,controller.jsonl}`, each job's runner log and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py}`.
