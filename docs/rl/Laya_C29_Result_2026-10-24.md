<!-- Path: docs/rl/Laya_C29_Result_2026-10-24.md | Purpose: Complete C29 accounting, attainment and speed endpoints, stop classes and the stale-reservation release decision. -->
# Cohort 29 result

`laya-c29-2123-miniship-20261024` compared control `daefefc` (the mall line `2d9b4b5` plus allowance defaults; behaviour equals the admitted baseline `d8a4825` with all settings off) with release `5a5ed28`. Release adds one change: after every operation it closes paired mall-cell projects whose producer has run, so a cell served once while idle no longer keeps its whole bill reserved. Seeds 79680–79739, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 7,200 s windows. It waited behind C26b/C27b and ran from about 10:25 to 18:02 IST on 2026-10-24.

**Accounting: 120 planned = 120 completed**, 60 per arm, no cuts or reruns. 116 runs stopped `stalled` and 4 hit the window cap (control 1, release 3). No runner errors, tool errors or safety stops.

| Arm (60) | C1 | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 0 | 44 | 25/23 | 30 | 30/4 | 27 | 13/8 |
| release | 3 | 50 | 38/36 | 38 | 38/5 | 30 | 15/13 |

Paired attainment, 60 pairs (release-only : control-only, two-sided sign test): **C2 14:8 (0.29, preregistered primary)**, **C3 19:6 (0.015)**, C5 ever 16:8 (0.15), C5 final 5:4, new Laya C6 14:11 (0.69).

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | release RMST | paired release faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C1 | 150 (0/60) | 143 (3/60) | 3:0 |
| strict C2 | 68 | 55 | 35:23 (−4 min, p 0.15) |
| strict C3 | 120 | 103 | **29:15 (−37 min, p 0.049)** |
| strict C4 | 117 | 108 | 27:19 (−4) |
| strict C5 | 118 | 110 | 26:20 (−5) |
| strict C6 | 141 | 141 | 13:10 (−14) |
| Laya C6 | 122 | 119 | 23:18 (−6) |

**Exposure:** the release closed at least one stale project (`MATERIAL PROJECT CLOSED`) in all 60 release worlds, and never in control, which lacks the code. At run end, constructing paired-mall projects still held their bills in 17 control runs (14 of them never-C2) and 3 release runs. C25 had shown the same split (16 never-C2, 0 C2).

**Decision: adopt the release (`5a5ed28`) as the Laya cohort base** (branch `work/miniship/laya-mall-integrated-20261023`; no production or real-base promotion). Per `docs/rl/laya_variants/README.md`:
- **More long-window target completions on matched conditions:** release reaches every strict milestone in more runs. C2 is 50 vs 44 and C3 38 vs 25, with C3 at 19:6 pairs (p 0.015), outside the A/A spread (identical code: C2 5:12). C4/C5 are 38 vs 30, new Laya C6 30 vs 27 and legacy C6 15 vs 13. Strict C1, which needs four working mall assemblers, appears for the first time in three release runs. The preregistered C2 contrast (14:8) leans the same way, though not significantly on its own.
- **Faster:** release reaches strict C2 13 minutes sooner on average (RMST 55 vs 68) and strict C3 17 minutes sooner (29:15 pairs, median 37 minutes).
- **No increase in safety or tool failures:** both are zero in both arms.
- **No earlier-service regression:** final C3 36 vs 23 and final C5 5 vs 4.

Untyped-stuck events rise (38 in 22 runs vs 8 in 8). Every one is `modular refinery for stone-brick built but not healthy: full_output` at the S1 stage, a backed-up stone-brick line rather than a broken build. Release reaches that stage more often. C31 tests typing it as output backpressure. Release also stopped one more run at the window cap (3 vs 1).

C30 (release vs steel-seed site fix), its C30a and Starship S30r strata, and C31 (output backpressure) are all built on `5a5ed28`. They stay valid and proceed.

| Failure class (events/runs) | control | release |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 8/8 | 38/22 (all S1 stone-brick `full_output`) |
| infrastructure collision | 0/0 | 0/0 |
| power route refusal | 48/10 (36 in seed 79733) | 14/13 |
| loan-release refusals (journals) | 0 | 0 |

## Where runs stop (last 25 decisions per completed run)

| Group | control | release | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 16 | 10 | `chemical_capability_handoff` (6 / 3), temporary mall batches, steel waits |
| C2, no C3 | 19 | 12 | temporary mall batches and material shortages (control); `steel_seed_site_wait` (release 6) |
| C3/C4, no C5 | 5 | 3 | temporary mall batches, steel seed site |
| C5, no legacy C6 | 11 | 21 | `material_shortage` (release 8), `bootstrap_mall_slot_cap`, `production_prerequisite_deferred` |
| legacy C6 | 9 | 14 | `production_prerequisite_deferred` (release 9), `bootstrap_mall_slot_cap` |

Release moves runs out of the early groups (never-C2 16 → 10, C2-no-C3 19 → 12) into the late ones. In release, `steel_seed_site_wait` is now the main C2-no-C3 stop (6 of 12); C30 tests the site fix for it. The terminal chemical handoff falls from 6 to 3 runs. `ppc3.py` finds 13 never-C2 runs still chest-short at their last iron-foundation attempt; it pools both arms. Late runs end on material shortages, `bootstrap_mall_slot_cap` and deferred production prerequisites, which mark the next capacity work.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization, deployed runtime or live result. Sources: `data/laya-experiments/laya-c29-2123-miniship-20261024/{freeze.json,batch.json,job-status.json}`, journals, runner logs, reservation ledgers, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py,stops.py,ppc3.py}`.
