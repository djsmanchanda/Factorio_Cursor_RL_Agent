<!-- Path: docs/rl/Laya_C27b_Result_2026-10-24.md | Purpose: Complete C27b accounting, pacing endpoints, speed, stop classes and the linear-wait decision. -->
# Cohort 27b result

`laya-c27b-2123-miniship-20261024` compared control with linear3. Both arms run frozen code `2d9b4b5` with reconciliation, research and pacing off. Linear3 adds only the `ops-wait-linear3` variant: `decision_wait_schedule=linear`, growth 0.03 per game minute, a ×4 cap and a 60-tick floor. Effective waits are `max(60, ceil(nominal / min(4, 1 + 0.03 × minutes)))`. Seeds 79650–79679, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 7,200 s windows. It replaced C27, which was cancelled before any start on the user's 7,200 s request, and it waited behind C24/C25. It ran from about 04:55 to 09:04 IST on 2026-10-24.

**Accounting: 60 planned = 60 completed**, 30 per arm, no cuts or reruns. 58 runs stopped `stalled` and 2 hit the window cap (one per arm). No runner errors, tool errors or safety stops.

| Arm (30) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 24 | 18/16 | 20 | 20/5 | 16 | 7/5 |
| linear3 | 20 | 12/11 | 12 | 12/2 | 10 | 6/5 |

Paired attainment, 30 pairs (linear3-only : control-only, two-sided sign test): C2 4:8 (0.39), C3 5:11 (0.21), C5 ever 4:12 (0.077), C5 final 2:5 (0.45), new Laya C6 4:10 (0.18).

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | linear3 RMST | paired linear3 faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C1 | 150 (0/30) | 150 (0/30) | – |
| strict C2 | 64 | 79 | 14:14 (−1 min) |
| strict C3 | 108 | 125 | 7:15 (+29, p 0.13) |
| strict C4 | 108 | 122 | 8:16 (+31, p 0.15) |
| strict C5 | 113 | 123 | 8:15 (+15) |
| Laya C6 | 119 | 128 | 8:12 (+23) |

## Pacing endpoints (`mall_endpoints.py`)

| Endpoint | control | linear3 |
| --- | ---: | ---: |
| wait selections | 1,466 | 1,469 |
| mean nominal / effective wait | 17.2 / 17.2 game s | 17.7 / 8.2 game s |
| decisions per game second | 0.053 | 0.061 |
| decisions per wall second | 0.093 | 0.104 |
| sampled assembler working share (first sample per game-minute bin) | 0.146 (2,683 bins, 159 missing) | 0.170 (2,340 bins, 116 missing) |
| game minutes played before stopping (median / mean) | 103 / 94 | 65 / 81 |
| reconciliation offered | 0 (setting off) | 0 (setting off) |

The intervention was fully exercised. Linear3 more than halves effective waits and makes 15% more decisions per game second and 12% more per wall second. Its sampled assembler working share is slightly higher. But linear3 runs stall sooner in game time (median 65 vs 103 game minutes). The stall rule is game-time based: 18,000 ticks without a progress signal. Faster sampling therefore doesn't trip it by itself, and the early stops are real loss of progress, not an artefact.

**Decision: linear3 not adopted.** It reaches fewer later milestones: C4 12 vs 20, C5 12 vs 20 (4:12 pairs) and new Laya C6 10 vs 16 (4:10). It is slower to strict C3–C6 (RMST +10 to +17). No single contrast is significant, and C2 (4:8) is inside the A/A spread (identical code: C2 5:12). But every later endpoint, attainment and speed alike, moves against it, and nothing moves for it. Strict C2 timing is level (14:14). C29's arms run with waits off, so they need no refreeze.

| Failure class (events/runs) | control | linear3 |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 23/10 | 5/5 |
| infrastructure collision | 1/1 | 0/0 |
| power route refusal | 6/4 | 11/8 |
| loan-release refusals (journals) | 0 | 1 in 1 world (`release_recipe_changed`) |

## Where runs stop (last 25 decisions per completed run)

| Group | control | linear3 | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 6 | 10 | `chemical_capability_handoff` (2 / 4), temporary mall batches, refinery-module construction |
| C2, no C3 | 6 | 8 | temporary mall batches (control), `steel_seed_site_wait` and chemical handoff (linear3) |
| C3/C4, no C5 | 2 | 2 | temporary mall batches, rotating-mall handoff, steel seed site |
| C5, no legacy C6 | 10 | 5 | `material_shortage`, `bootstrap_mall_slot_cap`, temporary mall batches |
| legacy C6 | 6 | 5 | `production_prerequisite_deferred` |

Linear3's extra early stops sit in the never-C2 and C2-no-C3 groups, on the chemical handoff and the steel seed site. Those waits need game time for oil and steel to build, and shorter waits do not supply it. The stale mall-reservation fix in C29 targets the never-C2 chemical handoff from the supply side.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c27b-2123-miniship-20261024/{freeze.json,batch.json,job-status.json}`, journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py,mall_endpoints.py}`.
