<!-- Path: docs/rl/Laya_C26b_Result_2026-10-24.md | Purpose: Complete C26b accounting, reconciliation endpoints, speed, stop classes and the ready-loan decision. -->
# Cohort 26b result

`laya-c26b-2123-miniship-20261024` compared control with ready. Both arms run frozen code `2d9b4b5` with reconciliation, research and pacing off. Ready adds only the `ops-ready-loans` variant, `mall_loan_reconciliation=true`: a mall loan whose bill is already stocked becomes a choice to hand its cell back. Seeds 79620–79649, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 7,200 s windows. It replaced C26, which was cancelled before any start on the user's 7,200 s request, and it waited behind C24/C25. It ran from about 04:55 to 10:22 IST on 2026-10-24.

**Accounting: 60 planned = 60 completed**, 30 per arm, no cuts or reruns. 58 runs stopped `stalled` and 2 hit the window cap (both control). No runner errors, tool errors or safety stops.

| Arm (30) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 22 | 18/15 | 19 | 19/2 | 17 | 6/2 |
| ready | 19 | 11/11 | 14 | 14/3 | 11 | 3/3 |

Paired attainment, 30 pairs (ready-only : control-only, two-sided sign test): C2 3:6 (0.51), C3 2:9 (0.065), C5 ever 4:9 (0.27), C5 final 3:2, **new Laya C6 3:9 (0.15)**.

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | ready RMST | paired ready faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C1 | 146 (1/30) | 141 (2/30) | 2:1 |
| strict C2 | 68 | 77 | 10:15 (+2 min) |
| strict C3 | 105 | 119 | 8:12 (+12) |
| strict C5 | 110 | 120 | 11:12 (+3) |
| strict C6 | 142 | 146 | 3:5 (+30) |
| Laya C6 | 115 | 126 | 9:11 (+7) |

## Reconciliation and utilization endpoints (`mall_endpoints.py`)

| Endpoint | control | ready |
| --- | ---: | ---: |
| runs with an eligible observation (ready loan present) | 27/30 (1,104 obs) | 27/30 (750 obs) |
| runs / decisions offering reconciliation | 0 / 0 (setting off) | 27/30 / 750 |
| reconciliations selected | – | 28 |
| outcome: changed (`loan_reconciled`) / pending / unknown | – | 28 / 0 / 0 |
| sampled assembler working share (first sample per game-minute bin) | 0.171 (2,700 bins, 131 missing) | 0.161 (2,275 bins, 91 missing) |
| decisions per game / wall second | 0.056 / 0.100 | 0.057 / 0.100 |

The setting was exercised: every eligible observation was offered, and Laya chose to reconcile 28 times, every one a receipt-verified handback. Ready therefore holds stocked loans for less time (750 eligible observations vs 1,104). Freeing those cells did not raise sampled assembler working share (0.161 vs 0.171), and later target output was not measured per reconciliation. So the cohort shows no utilization gain to credit.

**Decision: ready loans not adopted.** The preregistered endpoints move against it. C2 attainment is 3:6 with strict C2 slower (10:15 pairs, RMST +9). New Laya C6 is 3:9 and C3 is 2:9 (p 0.065), outside the A/A spread (identical code: C2 5:12), though no single contrast is significant. The only lean for ready is strict C1 (2 vs 1 run), too few to weigh. C29's arms run with reconciliation off, so they need no refreeze.

| Failure class (events/runs) | control | ready |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 8/5 | 3/3 |
| infrastructure collision | 1/1 | 2/2 |
| power route refusal | 3/3 | 4/4 |
| loan-release refusals (journals) | 0 | 0 |

## Where runs stop (last 25 decisions per completed run)

| Group | control | ready | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 8 | 11 | `chemical_capability_handoff`, `steel_seed_site_wait`, refinery-module construction |
| C2, no C3 | 4 | 8 | temporary mall batches (ready 4), `steel_seed_site_wait` |
| C3/C4, no C5 | 0 | 1 | `steel_seed_site_wait` |
| C5, no legacy C6 | 12 | 7 | `material_shortage`, `bootstrap_mall_slot_cap` (control 5) |
| legacy C6 | 6 | 3 | `production_prerequisite_deferred`, temporary mall batches |

Ready's extra stops sit early. Its C2-no-C3 group ends on temporary mall batches. One unverified explanation is that handed-back cells are borrowed again for another finite batch rather than producing. The never-C2 chemical handoff and the steel seed site recur in both arms; C29's stale-reservation release targets the chest side of that cycle.

## Combined reading with C27b

Both mall-utilization settings were exercised and neither helps. Shorter waits (C27b) and faster loan handback (C26b) each leave later milestones worse and do not raise sampled assembler working share materially. Utilization is not limited by how often Laya decides or how long a stocked loan stays out. It is limited by what a cell can be given to make: the chemical handoff, the steel seed site and `bootstrap_mall_slot_cap` are supply and capacity limits. The next fix candidates stay on the supply side, as the C29 release and an iron completion path that needs no new chests.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c26b-2123-miniship-20261024/{freeze.json,batch.json,job-status.json}`, journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py,mall_endpoints.py}`.
