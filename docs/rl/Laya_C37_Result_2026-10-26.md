<!-- Path: docs/rl/Laya_C37_Result_2026-10-26.md | Purpose: Complete C37 accounting, robot-exclusion exposure, speed, stop classes and the adoption decision. -->
# Cohort 37 result

`laya-c37-2123-miniship-20261026` compared control `d9402da` with robots `32c1ef5`.
- **Control** is `5a5ed28` (the release base adopted after C29) plus refinery-extension refusals that name their blocking entities.
- **Robots** adds one change: `occupied_tiles` and `occupied_tile_owners` skip logistic and construction robots. A robot flying over a planned footprint no longer refuses the build.
- **Why:** C36's named blockers showed refusals on `logistic-robot` at in-flight positions.
- **Setup:** seeds 80100–80159, Factorio 2.1.23 build 87750, the same copied save as C36, 540,000-tick / 7,200 s windows.
- **When:** it waited behind C36 and ran from 04:23 to 12:00 IST on 2026-10-26. Slots were held at 8 until 05:42, then 15. It shared the host with C38 from 05:17.

**Accounting: 120 planned = 120 completed**, 60 per arm, no cuts or reruns.
- 111 runs stopped `stalled` (the 18,000-idle-tick rule) and 9 hit the window cap (control 7, robots 2).
- Every runner exited 0. No tool errors or safety stops.

| Arm (60) | C1 | C2 | C3 ever/final | C4 | C5 ever/final | Strict C6 ever/final | New Laya C6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 2 | 53 | 36/36 | 36 | 36/8 | 11/8 | 29 |
| robots | 1 | 50 | 37/36 | 36 | 36/12 | 7/6 | 26 |

Paired attainment, 60 pairs (robots-only : control-only, two-sided sign test):
- **strict C6 ever 5:9 (0.42, preregistered primary);**
- C2 3:6 (0.51) and C3 10:9 (1.0), the earlier-service guards;
- C5 ever 15:15, and C5 final 10:6 (0.45);
- C3 final 9:9;
- new Laya C6 16:19.

Every contrast is inside the A/A spread (identical code: C2 5:12). C5 here is the c5-v1 predicate. The user's plastic-startup definition is not tested.

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | robots RMST | paired robots faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C2 | 49 | 54 | 28:28 (0 min) |
| strict C3 | 97 | 94 | 27:19 (−7) |
| strict C4 | 110 | 109 | 30:21 (−3) |
| strict C5 | 112 | 111 | 28:23 (−2) |
| strict C6 | 143 | 146 | 7:9 (+18) |
| Laya C6 | 120 | 123 | 20:25 (+10) |

## Exposure

`blockers.py` classifies the named blockers of each refinery-extension refusal:

| Measure | control | robots |
| --- | ---: | ---: |
| refusals naming only robots (runs) | 6 events (6) | 0 |
| refusals naming robots and real infrastructure (runs) | 168 events (36) | 0 |
| refusals naming only real infrastructure (runs) | 527 events (48) | 570 events (46) |
| named `big-electric-pole` mentions | 638 | 568 |

The fix works as designed: no refusal in the robots arm names a robot. Control refusals nearly always name a real blocker as well, usually the big electric pole at (−40,−14). Only 6 control runs had a refusal caused by robots alone. So removing robots changes almost no extension outcome on the base, where the pole still blocks the line. That explains the null milestone effect.

The robots' effect should show up only once the pole is cleared. In C36's clearance arm, nearly every remaining refusal was robot-only. C38 (thread 94dcfaf2) tests clearance with robots excluded in both arms.

**Decision: robots not adopted as a policy change.**
- **Primary:** strict C6 ever leans against it (7 vs 11; 5:9 pairs, p 0.42) and is slightly slower (RMST +3).
- **Guards:** C2 (3:6) and C3 (10:9) are inside the A/A spread. Robots reaches C3 a little sooner (27:19 pairs).
- **Failures:** tool and safety failures are zero in both arms. Untyped stuck falls (29 events in 15 runs vs 72 in 17), and C5 final rises (12 vs 8). Neither is a preregistered endpoint.

Under `docs/rl/laya_variants/README.md` there is no gain in long-window completions, so the base stays `5a5ed28`. The change eliminates named robot refusals in this cohort. That mechanism check does not prove milestone non-regression or adoption; C38 retains it only as a common experimental condition.

| Failure class (events/runs) | control | robots |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 72/17 | 29/15 |
| infrastructure collision | 2/2 | 0/0 |
| power route refusal | 15/10 | 16/9 |
| loan-release issues (journal + runner) | 2 worlds | 3 worlds (3 runner failures) |

Untyped stuck is dominated by a few seeds:
- **control:** 25 events in seed 80154, 12 in 80116, 12 in 80125;
- **robots:** 11 events in 80152.

By message:
- `modular refinery for stone-brick built but not healthy: full_output` makes up 57 of control's events and 24 of robots'. This is the S1 output-backpressure class first seen in C29.
- 11 control events read `No recipe knowledge for 'stone'`. That is a separate planner gap, seen only in control.
- The rest are copper-plate (control 4) and iron-plate (robots 5) `full_output`.

## Where runs stop (last 25 decisions per completed run)

| Group | control | robots | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 7 | 10 | `chemical_capability_handoff`, `steel_seed_site_wait` |
| C2, no C3 | 17 | 13 | `steel_seed_site_wait`, temporary mall batches, `material_shortage` |
| C3, no C5 | 5 | 7 | temporary mall batches, pending system construction |
| C5, no strict C6 | 21 | 23 | `material_shortage`, temporary mall batches, `bootstrap_mall_slot_cap` |
| strict C6 | 10 | 7 | `production_prerequisite_deferred` |

These last-decision groups classify 59 control runs and all 60 robot runs; the primary acceptance table above retains all 120 jobs. The stop profile matches C29–C36 on this base:
- early runs stop on the chemical handoff and the steel seed site;
- late runs stop on material shortages, temporary mall batches and mall slot capacity.

Open items carried forward:
- late mall shortages;
- a mined steel-seed fallback that declares its project only once affordable;
- the never-C2 chemical handoff;
- `No recipe knowledge for 'stone'`.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c37-2123-miniship-20261026/{freeze.json,batch.json,job-status.json,batch-events.jsonl}`, journals, runner logs, and `data/laya-orchestration/takeover-20261021/{checkin.sh,time_to.py,blockers.py,release_counts.py,stops.py}`.
