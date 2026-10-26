<!-- Path: docs/rl/Laya_C33_Result_2026-10-25.md | Purpose: Complete C33 accounting, clearance exposure, speed, stop classes and the refinery pole-clearance decision. -->
# Cohort 33 result

`laya-c33-2123-miniship-20261025` compared control `5a5ed28` (the release base adopted after C29) with clearance `ba31bec`. Clearance is `5a5ed28` + `e5471f8` (C32's 7a89997, which clears connected same-force poles before refinery expansion preflight) + `ba31bec` (3a79880, which preserves power and sibling poles during clearance). Neither arm carries the foundation census (`de40332`) or the c5-v2 commits, so strict acceptance is the release base's. The user asked for this cohort via thread 94dcfaf2 after C31/C32 were cancelled for the census defect. Seeds 79860–79919, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 7,200 s windows, no dependency. It ran from about 04:59 to 12:19 IST on 2026-10-25, partly under the user's 2-hour cool window (10 slots).

**Accounting: 120 planned = 120 completed**, 60 per arm, no cuts or reruns. 113 runs stopped `stalled` and 7 hit the window cap (control 4, clearance 3). No runner errors, tool errors or safety stops.

| Arm (60) | C1 | C2 | C3 ever/final | C4 | C5 ever/final | Strict C6 ever/final | New Laya C6 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 1 | 50 | 44/38 | 35 | 35/6 | 10/8 | 26 |
| clearance | 1 | 52 | 37/36 | 38 | 38/12 | 11/7 | 29 |

Paired attainment, 60 pairs (clearance-only : control-only, two-sided sign test): **strict C6 ever 11:10 (preregistered primary)**, C2 7:5 (0.77), C3 14 against clearance, 7:14 (0.19), C5 ever 15:12 (0.70), C5 final 10:4 (0.18), new Laya C6 16:13 (0.71). No seed reached strict C6 in both arms.

C5 here is the c5-v1 predicate. The user's definition, plastic starting and reaching a stable rate within the first two minutes, is **not** tested by this cohort.

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | clearance RMST | paired clearance faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C2 | 55 | 51 | 29:28 (0 min) |
| strict C3 | 83 | 100 | 20:31 (+15) |
| strict C4 | 112 | 111 | 23:27 (+1) |
| strict C5 | 114 | 113 | 23:27 (+1) |
| strict C6 | 144 | 143 | 11:10 (−17) |
| Laya C6 | 123 | 122 | 21:21 (−3) |

## Exposure

| Measure | control | clearance |
| --- | ---: | ---: |
| runs refused at (−41,−15),(−41,−14),(−40,−15) | 40/60 (483 events) | 0/60 |
| runs with any iron-plate refinery-extension refusal | 42 | 27 |
| runs with a POLE RELOCATION (lines) | – | 38 (42) |
| runs logging SMELTER POLE CLEARANCE | – | 26 |
| relocated runs refused again later on other iron tiles | – | 18/38 |

The mechanism works. The known blocker, the big electric pole at (−40,−14), is cleared in every clearance run that meets it, and refusals there vanish. In about half of the relocated runs, however, the growing extension then meets other existing infrastructure further along ((−37,−20), (−33,−17), (−41,−22) and similar). Clearing one pole does not free the line, which matches the flat later milestones.

**Decision: clearance not adopted.** The preregistered strict C6 is level (11 vs 10; 11:10 pairs), and so are C4, C5 and Laya C6, in attainment and in speed. The one gain is C5 final (12 vs 6, 10:4, p 0.18). Against it, strict C3 ever drops from 44 to 37 (7:14) and is reached later (20:31 pairs, RMST +17). Under `docs/rl/laya_variants/README.md` that is an earlier-service regression, which blocks adoption, although no single contrast is significant. Tool and safety failures stay at zero. Untyped stuck rises (37 in 23 runs vs 24 in 15). Power-route refusals fall (22 in 13 vs 62 in 9, 46 of control's in seed 79868).

**The C3 loss does not come from relocation.** Split by exposure, pairs where the clearance run never relocated a pole carry the deficit: control-only C3 8, clearance-only 2, same 12. Pairs with a relocation are about even: 6 control-only, 5 clearance-only, 27 same. A power gap during relocation is excluded by design: `relocate_obstructing_poles` removes the old pole only after `_connect` finds a built replacement wired to every old neighbour, and otherwise recovers only the replacement. A few clearance runs time out retiring the stone starter, and "roboport ... network is not active" appears in 3 clearance runs (0 control). That is too few to explain the gap. The remaining explanations are run-to-run variance and the extra pole survey before each extension, which runs even when nothing is moved.

| Failure class (events/runs) | control | clearance |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 24/15 | 37/23 |
| infrastructure collision | 3/3 | 1/1 |
| power route refusal | 62/9 (46 in seed 79868) | 22/13 |
| loan-release issues (journal + runner) | 4 worlds | 4 worlds |

## Where runs stop (last 25 decisions per completed run)

| Group | control | clearance | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 10 | 8 | chemical handoff, material shortage, temporary mall batches |
| C2, no C3 | 7 | 15 | `steel_seed_site_wait`, temporary mall batches, `bootstrap_mall_slot_cap` |
| C3/C4, no C5 | 9 | 5 | `steel_seed_site_wait`, temporary mall batches |
| C5, no strict C6 | 25 | 22 | `material_shortage`, temporary mall batches, `bootstrap_mall_slot_cap` |
| strict C6 | 9 | 10 | `production_prerequisite_deferred` |

## Next

The base stays `5a5ed28`. The clearance mechanism is sound and worth keeping on its branch. A version worth retesting should clear the whole extension footprint, not only the first pole, and should first be checked against a C3 replication. Two leads:
- skip the pre-expansion pole survey when no pole intersects the footprint;
- report the next blocker by entity type, so the refusals that follow relocation can be classified.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c33-2123-miniship-20261025/{freeze.json,batch.json,job-status.json}`, journals, runner logs, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py,stops.py}`.
