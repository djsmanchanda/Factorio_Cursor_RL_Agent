<!-- Path: docs/rl/Laya_C34_Result_2026-10-25.md | Purpose: Complete C34 accounting, outblock exposure, speed, stop classes and the full-output diagnosis decision. -->
# Cohort 34 result

`laya-c34-2123-miniship-20261025` compared two arms:
- **census** `9d9b279`: release `5a5ed28` + census cherry-pick `de40332` + the census position fix.
- **outblock** `9d8216d`: `9d9b279` + `7c63333`. In `_diagnose_machines`, machines with `full_output` status are reported as OUTPUT BLOCKED, a service condition, instead of a stuck construction fault.

It reruns C31's comparison now that milestones can be read again. The setup: seeds 79920–79979, the same copied save, 540,000-tick / 7,200 s windows, the host budget and no dependency. It ran from 09:29 to 18:13 IST on 2026-10-25. The preregistered primary endpoint is strict C3.

**Accounting: 120 planned = 120 completed**, 60 per arm, with no cuts or reruns. Per arm, 57 runs stopped `stalled` and 3 hit the game window. There were no tool errors or safety stops.

**Acceptance health:**
- All 4,323 acceptance checks were evaluated.
- Evidence errors: 0 in both arms.
- The census fix therefore held across the whole cohort, unlike C31, where every run failed the census.

| Arm (60) | C1 | C2 | C3 ever/final | C4 | C5 ever/final | Strict C6 ever/final | Laya C6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| census | 1 | 51 | 47/47 | 35 | 35/14 | 10/9 | 24 |
| outblock | 2 | 48 | 45/45 | 37 | 37/9 | 11/10 | 29 |

Paired attainment over 60 pairs, written outblock-only : census-only with a two-sided sign test:

| Endpoint | Pairs | p |
| --- | ---: | ---: |
| **strict C3 ever (primary)** | **8:10** | 0.81 |
| C2 | 5:8 | 0.58 |
| C4 | 10:8 | 0.81 |
| C5 ever | 10:8 | 0.81 |
| C5 final | 6:11 | 0.33 |
| strict C6 | 9:8 | 1.00 |
| Laya C6-v2 | 12:7 | |

C5 here is c5-v1. The user's startup definition (c5-v2) is not tested.

Time to milestone (`time_to.py`): game minutes from first decision, with RMST capped at 150; lower is faster.

| Milestone | census RMST | outblock RMST | paired outblock faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C2 | 54 | 60 | 26:30 (+2 min) |
| strict C3 | 72 | 77 | 30:25 (−3) |
| strict C4 | 112 | 110 | 21:24 (0) |
| strict C5 | 114 | 112 | 21:24 (+2) |
| strict C6 | 145 | 144 | 10:9 (−4) |
| Laya C6 | 127 | 121 | 21:15 (−5) |

## Exposure and failure classes

| Measure (events/runs) | census | outblock |
| --- | ---: | ---: |
| OUTPUT BLOCKED diagnoses | 0/0 | 23/20 |
| untyped_stuck | 48/15 | **0/0** |
| tool_error / safety stop | 0/0 | 0/0 |
| infrastructure collision | 1/1 | 1/1 |
| power route refusal | 65/13 (47 in seed 79976) | 119/11 (59 in 79932, 49 in 79976) |
| bootstrap_mall_slot_cap | 88/6 | 343/10 |
| median wall s per run | 3,066 | 3,613 |

The mechanism does what it was written to do. Every untyped stuck event in the census arm was a full-output line being treated as a construction fault, and the change removes all of them. The runs no longer stop on that false fault. They run longer and spend the extra time in the late mall, which is where the rise in `bootstrap_mall_slot_cap` comes from. They do not convert that time into milestones.

## Where runs stop (last 25 decisions per completed run)

| Group | census | outblock | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 9 | 12 | material shortage, temporary mall batches, chemical handoff |
| C2, no C3 | 5 | 6 | `steel_seed_site_wait`, material shortage |
| C3/C4, no C5 | 12 | 8 | temporary mall batches, `steel_seed_site_wait`, pending construction |
| C5, no strict C6 | 24 | 23 | temporary mall batches, `material_shortage`, `bootstrap_mall_slot_cap` (outblock 8) |
| strict C6 | 10 | 11 | `production_prerequisite_deferred` |

**Decision: outblock not adopted.** The adoption rule asks for more long-window completions with no earlier-service regression. Strict C3 is level, 45 against 47 (8:10, p 0.81). C6, C4, C5 and Laya C6 are level within noise in both attainment and speed. C2 is 3 lower (5:8), and C5 final is lower (6:11). Neither is significant, and neither counts as a gain. The change is a correct diagnosis, and it remains available on its branch. Together with the late-mall slot cap it now exposes, it belongs in a later late-mall cohort, not on the base by itself.

**Census fix `9d9b279`:** verified over 120 runs, with no acceptance or evidence errors. It is a measurement fix, not a decision variant, and it is the measurement base for new freezes; C35 already uses it. The release decision base stays `5a5ed28`.

## Next

The C5→C6 group remains the largest, at 23–24 runs per arm. It ends in temporary mall batches, material shortage and, once full-output lines stop halting runs, the bootstrap mall slot cap. C35 (long-handed-inserter batch path) addresses one cell-kit shortage. The chest gate's full-bill advanced-circuit check is the next candidate.

Evidence level: a completed matched experiment cohort on one copied save; this is not terrain generalization. Sources: `data/laya-experiments/laya-c34-2123-miniship-20261025/{freeze.json,batch.json,report.json,report.md,logs/}`, world logs under `arms/*/code/data/laya-saved-world/`, and `data/laya-orchestration/takeover-20261021/{failures.py,time_to.py,stops.py}`.
