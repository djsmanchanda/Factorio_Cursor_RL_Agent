<!-- Path: docs/rl/Laya_C30_Result_2026-10-25.md | Purpose: Complete C30 accounting with its C30a and S30r strata, steel-seed exposure, speed, stop classes and the site-fix decision. -->
# Cohort 30 result

`laya-c30-2123-miniship-20261024` compared release `5a5ed28` (the base adopted after C29) with site `f765d30`. Site is `5a5ed28` plus `f83b0e8`, whose mined steel-seed fallback tries up to 12 iron starter sites and both pole sides instead of the nearest site with pole side 1, plus a log line. Seeds 79740–79799, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 7,200 s windows. It waited behind C29 and ran from about 18:05 IST on 2026-10-24 to 01:24 IST on 2026-10-25.

**Accounting: 120 planned = 120 completed**, 60 per arm, no cuts or reruns. 114 runs stopped `stalled` and 6 hit the window cap (3 per arm). No runner errors, tool errors or safety stops.

| Arm (60) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| release | 50 | 40/39 | 32 | 32/12 | 20 | 10/6 |
| site | 47 | 36/35 | 37 | 37/16 | 25 | 8/6 |

Paired attainment, 60 pairs (site-only : release-only, two-sided sign test): **C2 6:9 (0.61, preregistered primary)**, C3 13:17 (0.59), C5 ever 18:13 (0.47), C5 final 12:8 (0.50), new Laya C6 16:11 (0.44).

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | release RMST | site RMST | paired site faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C2 | 54 | 61 | **21:35 (+3 min, preregistered)** |
| strict C3 | 91 | 103 | 19:32 (+9) |
| strict C4 | 115 | 109 | 30:20 (−4) |
| strict C5 | 117 | 112 | 27:22 (−2) |
| strict C6 | 145 | 145 | 8:10 |
| Laya C6 | 130 | 124 | 21:15 (−7) |

**Separate strata of the same comparison** (user-authorized via thread 94dcfaf2; reported here, not pooled):
- **C30a** (Miniship, seeds 83200–83219, 40/40): C2 0:3, C3 3:6, C5 0:8, C5 final 2:7, new Laya C6 2:7; site slower to strict C2 (6:14).
- **S30r** (Starship, seeds 83100–83123, 48/48; read-only at 23:27): C2 2:4, C3 5:5, C5 5:7, C6 4:6.

**Exposure:** 53 site worlds used the mined fallback. 40 took the nearest site, 10 the second and 3 the third. A run at the nearest site is not necessarily unchanged, because the fallback now tries the survey's pole side first. Per pair, `seed_plans.py` compares the persisted `*.steel-seed.json` plans:

| Pair type (release / site plan) | pairs | net site − release C2 / C3 / C5 |
| --- | ---: | --- |
| both mined, identical plan | 16 | 0 / −1 / +3 |
| both mined, different plan | 22 | 0 / −1 / +4 |
| release mined, site none | 9 | −9 / −9 / −9 |
| release none, site mined | 9 | +6 / +7 / +7 |
| neither | 4 | 0 / 0 / 0 |

Every C2 difference sits in the pairs where only one arm built a seed, and those groups nearly cancel (−9 vs +6). In 3 of the 9 release-only pairs (79740, 79750, 79757), the site arm found a fallback site while its bill was unaffordable. Its deliberate full-bill `conversion_steel-plate` hold then sat at priority 100 in `supply_wait`, holding furnaces, the drill, poles and a chest. In the other 6, the site run never attempted the seed.

**Decision: site `f83b0e8` not adopted.** The preregistered strict C2 contrast leans against it (6:9; strict C2 timing 21:35, RMST +7). C30a leans the same way more strongly (C5 0:8). S30r is mildly against. Site's later leans in C30 (C4/C5, Laya C6) are inside the A/A spread (identical code: C2 5:12) and do not replicate in C30a. Tool and safety failures are zero in both arms. Untyped stuck rises (33 in 16 runs vs 17 in 12). Site's power-route refusals (49 in 13 runs) are mostly one seed (79785: 32). No earlier-service gain offsets the C2 lean. The release base `5a5ed28` stays. A later version would declare the mined fallback's project only once its bill is allocatable, so the fallback cannot enter the deliberate full-bill hold early.

| Failure class (events/runs) | release | site |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 17/12 | 33/16 |
| infrastructure collision | 1/1 | 0/0 |
| power route refusal | 11/11 | 49/13 (32 in seed 79785) |
| loan-release refusals (journals) | 0 | 1 in 1 world (`release_recipe_changed`) |

## Where runs stop (last 25 decisions per completed run)

| Group | release | site | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 10 | 13 | `producer_bootstrap_seed_shortage`, `bootstrap_mall_slot_cap`, chemical handoff, refinery-module construction |
| C2, no C3 | 10 | 11 | pending system construction, temporary mall batches, `steel_seed_site_wait` (release 2) |
| C3/C4, no C5 | 10 | 2 | temporary mall batches (release 6) |
| C5, no legacy C6 | 21 | 27 | `material_shortage` (8 / 11), temporary mall batches, `bootstrap_mall_slot_cap` |
| legacy C6 | 9 | 7 | `production_prerequisite_deferred`, material shortage |

`steel_seed_site_wait` is the most frequent failure in the last 25 decisions of 5 release runs and of no site run, so the fix does remove that stop. Those runs move on to other limits rather than further milestones. On the release base the dominant stops are late: material shortages, temporary mall batches and `bootstrap_mall_slot_cap`, which is mall capacity. That is the next work.

Evidence level: completed matched experiment cohort plus two replication strata on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c30{,a}-2123-miniship-20261024/{freeze.json,batch.json,job-status.json}`, journals, runner logs, reservation ledgers and seed plans, `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py,stops.py,seed_plans.py}`, and the S30r job status on Starship.
