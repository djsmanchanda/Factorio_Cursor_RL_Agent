<!-- Path: docs/rl/Laya_C24_Result_2026-10-23.md | Purpose: Complete C24 accounting, attainment and speed endpoints, stop classes and the steel decision. -->
# Cohort 24 result

`laya-c24-2123-miniship-20261023` compared control `d8a4825` (the admitted baseline) with steel `5fb6371`, which admits funded steel for an owned iron-replacement dependency. Seeds 79500–79559, Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, frozen harness `1b2b7da`, gated on C22/C23 and the baseline decision. The cohort was prepared by thread cb16c604 and released at 18:16 IST. It ran until 23:28 IST on 2026-10-23.

**Accounting: 120 planned = 120 completed**, 60 per arm, no cuts or reruns. 112 runs stopped `stalled` and 8 hit the window cap (control 2, steel 6). No runner errors, tool errors or safety stops.

| Arm (60) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| control | 42 | 25/24 | 30 | 30/9 | 23 | 5/3 |
| steel | 41 | 31/29 | 33 | 33/9 | 26 | 8/5 |

Paired attainment, 60 pairs (steel-only : control-only, two-sided sign test): **C2 12:13 (1.0, preregistered primary)**, C3 18:12 (0.36), C5 ever 15:12 (0.70), C5 final 8:8, new Laya C6 13:10 (0.68).

Time to milestone (`time_to.py`; game minutes from first decision, RMST capped at 150, lower is faster):

| Milestone | control RMST | steel RMST | paired steel faster : slower (median diff) |
| --- | ---: | ---: | --- |
| strict C1 | 146 (2/60) | 148 (1/60) | 1:2 |
| strict C2 | 71 | 75 | **21:33 (+3 min)** |
| strict C3 | 116 | 108 | 22:21 (−9) |
| strict C5 | 119 | 116 | 23:22 (−1) |
| Laya C6 | 127 | 124 | 20:16 (−6) |

**Decision: steel not adopted.** The preregistered endpoint, strict C2 attainment, does not move (12:13), and steel reaches C2 slightly later (21:33 faster:slower, RMST +4). Its leans on later milestones are small and inside run-to-run noise. Steel also stopped more runs at the window cap (6 vs 2) and had more untyped-stuck runs (16 vs 11). The mall-utilization cohorts C26/C27 exclude steel from their base, so they stay valid as frozen.

| Failure class (events/runs) | control | steel |
| --- | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 |
| untyped_stuck | 43/11 | 55/16 |
| infrastructure collision | 0/0 | 1/1 |
| power route refusal | 68/18 (45 in seed 79511) | 22/15 |
| loan-release refusals (journals) | 0 | 1 in 1 world (`release_recipe_changed`) |

## Where runs stop (local read of the last 25 decisions per completed run)

| Group | control | steel | dominant terminal outcomes |
| --- | ---: | ---: | --- |
| never C2 | 18 | 19 | `chemical_capability_handoff` (6 / 8), refinery-module construction, temporary mall batches |
| C2, no C3 | 17 | 10 | temporary mall batches, material shortages, `bootstrap_mall_slot_cap`, steel-site waits |
| C3/C4, no C5 | 5 | 5 | temporary mall batches, pending system construction |
| C5, no legacy C6 | 18 | 18 | `material_shortage` (8 / 10), `bootstrap_mall_slot_cap` |

The never-C2 handoff class is structural. In 2.1 the iron foundation's passive provider chests need advanced circuits, so iron completion waits on the plastic/circuit ladder. `bootstrap_mall_slot_cap` appears in every group, and strict C1 (four working mall assemblers) is reached in only 3/120 runs. Both point at mall capacity and utilization, which C26 (ready-loan reconciliation) and C27 (linear wait pacing) test next. Fix candidates recorded, not implemented this round:
- a provider-chest-free iron completion path for C2;
- broader lender eligibility and parallel kit planning for the mall.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization. Sources: `data/laya-experiments/laya-c24-2123-miniship-20261023/{freeze.json,batch.json,job-status.json}`, journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py,time_to.py}`.
