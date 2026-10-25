<!-- Path: docs/rl/Laya_C25_Result_2026-10-24.md | Purpose: Complete C25 accounting, attainment and speed endpoints, stop classes and the backing decision. -->
# Cohort 25 result

`laya-c25-2123-miniship-20261023` compared control `d8a4825` (the admitted baseline) with backing: `d8a4825` plus the isolated patch `65604cd`, which replans unfinished loans from current input backing. Seeds 79560–79619, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, frozen harness `1b2b7da`. It was admitted after C24 and ran from about 23:30 IST on 2026-10-23 to 04:52 IST on 2026-10-24.

**Accounting: 120 planned = 120 completed**, 60 per arm, no cuts or reruns. 116 runs stopped `stalled` and 4 hit the window cap (control 1, backing 3). No runner errors, tool errors or safety stops.

| Arm (60) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 46 | 33/32 | 35 | 35/8 | 31 | 13/9 |
| backing | 44 | 28/27 | 33 | 33/8 | 29 | 10/5 |

Paired attainment, 60 pairs (backing-only : control-only, two-sided sign test): C2 7:9 (0.80), C3 11:16 (0.44), C5 ever 8:10 (0.81), C5 final 7:7, **new Laya C6 10:12 (0.83, preregistered primary)**.

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | backing RMST | paired backing faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C1 | 150 (0/60) | 148 (1/60) | 1:0 |
| strict C2 | 64 | 69 | 22:31 (+2 min) |
| strict C3 | 104 | 119 | **14:28 (+43 min, p 0.044)** |
| strict C5 | 113 | 115 | 22:21 (−2) |
| strict C6 | 142 | 143 | 8:11 (+5) |
| Laya C6 | 118 | 121 | 20:21 (+0) |

**Decision: backing not adopted.** The preregistered endpoint, new Laya C6, does not move (10:12, RMST +3). No attainment contrast leaves the A/A spread (identical code: C2 5:12). Backing is slower to strict C3: 14:28 pairs, a median of 43 minutes later. That is a single nominal p 0.044 among about a dozen speed contrasts, so it is not a confirmed harm, but it agrees with C3 attainment (11:16) and RMST (+15). Its mechanism does show: power-route refusals fall from 142 events in 21 runs to 61 in 17. Most of control's refusals sit in two seeds (79603: 58, 79563: 41), however, and no milestone gain follows. Backing also stopped more runs at the window cap (3 vs 1). C26b/C27b's base `2d9b4b5` already excludes backing, so they stay valid as frozen.

| Failure class (events/runs) | control | backing |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 27/10 | 14/14 |
| infrastructure collision | 0/0 | 1/1 |
| power route refusal | 142/21 | 61/17 (40 in seed 79614) |
| loan-release refusals (journals) | 2 in 1 world (`release_recipe_changed`) | 0 |

## Where runs stop (local read of the last 25 decisions per completed run)

| Group | control | backing | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 14 | 16 | `chemical_capability_handoff` (4 / 5), `steel_iron_retirement_wait` (4 / 3), material shortages |
| C2, no C3 | 13 | 16 | `steel_seed_site_wait` (4 / 6), temporary mall batches (4 / 4), `bootstrap_mall_slot_cap` (3 / 3) |
| C3/C4, no C5 | 4 | 3 | temporary mall batches (control), `steel_seed_site_wait` (backing) |
| C5, no legacy C6 | 19 | 16 | `material_shortage` (10 / 3), `bootstrap_mall_slot_cap`, temporary mall batches, `production_prerequisite_deferred` |
| legacy C6 | 10 | 9 | `production_prerequisite_deferred` (7 / 4) |

The C24 classes repeat. Never-C2 runs end in the chemical handoff (the iron foundation's passive provider chests need advanced circuits) or wait to retire the iron starter for steel. `bootstrap_mall_slot_cap` and temporary mall batches appear in every group, and strict C1 (four working mall assemblers) is reached in 1/120 runs. The C2-no-C3 group's extra backing runs mostly wait on the steel seed site, consistent with backing's slower strict C3. Fix candidates are unchanged:
- a provider-chest-free iron completion path for C2;
- broader lender eligibility and parallel kit planning for the mall, which C26b (ready-loan reconciliation) and C27b (linear wait pacing) approach from the decision side.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c25-2123-miniship-20261023/{freeze.json,batch.json,job-status.json}`, journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py}`.
