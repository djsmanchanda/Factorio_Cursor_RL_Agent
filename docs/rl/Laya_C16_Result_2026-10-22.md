<!-- Path: docs/rl/Laya_C16_Result_2026-10-22.md | Purpose: Complete C16 accounting, endpoints, stop classes and the adoption decision for the vacancy and foundation fixes. -->
# Cohort 16 result

`laya-c16-2123-miniship-20261022` ran three cumulative arms without steelbill on seeds 79400–79449: ref `c1b8680` → vacancy `0bf1bdc` (reuse a released finite mall half as a borrowable cell) → foundation `7fd2320` (an observed-ready foundation advances its plate stage, so starter retirement is offered). Factorio 2.1.23 build 87750, same copied save, 540,000-tick / 6,000 s windows, harness 65d5613 behind the host scheduler. It ran from 2026-10-22 04:50 to about 16:30 IST.

**Accounting: 150 planned = 150 completed**, 50 per arm, no reruns. 147 runs stopped `stalled` and 3 hit the window cap (one per arm). No runner errors, tool errors or safety stops.

| Arm (50) | C2 | C3 ever/final | C4 | C5 ever/final | New Laya C6 | Legacy C6 ever/final |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| ref | 15 | 6/5 | 13 | 13/3 | 11 | 6/3 |
| vacancy | 25 | 22/21 | 24 | 24/4 | 21 | 8/6 |
| foundation | 35 | 22/19 | 25 | 25/4 | 22 | 9/7 |

Paired contrasts, 50 pairs each (newer-only : older-only, two-sided sign test):

| Contrast | C2 | C3 | C5 ever | C5 final | New C6 |
| --- | --- | --- | --- | --- | --- |
| vacancy vs ref | 16:6 (0.053) | **18:2 (0.0004)** | 14:3 (0.013) | 4:3 | 13:3 (0.021) |
| foundation vs vacancy | 13:3 (0.021) | 10:10 | 13:12 | 1:1 | 13:12 |

**Decision: adopt both fixes as the Laya cohort base** (branch `work/miniship/laya-nosteelbill-20261021`; no production or real-base promotion). Per `docs/rl/laya_variants/README.md`:

- **Vacancy** raises long-window target completions on matched conditions: C3, C5 ever and new Laya C6 all improve, and C3 18:2 survives a Bonferroni correction over the ten contrasts. C2 also rises 15 → 25. Every gap is well outside the A/A spread (identical code: C2 5:12 on 33 seeds). Tool errors and safety stops stay at zero, and no earlier service regresses. It confirms C15's lean (vacancy vs steelbill C2 12:8, C3 13:7) now that steelbill is gone.
- **Foundation** raises C2 13:3 (35 vs 25) and leaves every later milestone level, with no failure-class increase. Its mechanism is confirmed: no foundation run ended with a ready foundation and no retirement option, against 10 in ref and 14 in vacancy.

C5 final stays at 3–4 per arm. It fails only `plastic_craft_counter_advances`: all 19 such foundation runs hold about 4,800 plastic and offer no plastic option, so the final sample measures a full output, not lost capability. This caveat (C12) is unchanged and is not a regression.

| Failure class (events/runs) | ref | vacancy | foundation |
| --- | ---: | ---: | ---: |
| tool_error / safety stop | 0/0 | 0/0 | 0/0 |
| untyped_stuck | 1/1 | 20/17 | 12/9 |
| infrastructure collision | 45/10 | 107/15 | 66/11 |
| power route refusal | 8/7 | 28/11 | 7/6 |
| loan-release refusals (journals) | 45 in 10 worlds | 107 in 15 | 66 in 11 |

The collisions are loan-release refusals (`release_owner_missing`, plus 2 `release_recipe_changed` in vacancy), not geometry. Vacancy borrows more and so releases more; `d39b6c7` (C17) removes them.

## Where runs stop (codex read-only analysis, 141 completed jobs)

Foundation, 47 analysed: never C2 14 (provider-chest/steel loop 5, bootstrap borrow deadlock 5, small unfunded bills 2, steel site 1, residual starter with a splitter bill 1); C2 but never C3 12 (oil/power pole supply 5, stone inserter self-bill 2, steel site 1, stone readiness never reconciled despite C5 and new C6 4); C3/C4 but never C5 2; C5 ever but final failed 19 (plastic plateau above). Six foundation runs end with an ingredient-starved pole loan. A follow-up traced 12 starved pole loans across the arms to zero network iron-stick, 8 still carrying a prerequisite credit that was never spent; `e37464d` fixes that and C18 tests it. A second fault (the rotating mall restores a live pole batch because the whole remaining bill is not yet in stock, while deliveries are in flight) is open. Reports: local `data/laya-orchestration/takeover-20261021/checkin_helper_runs/{c16-stop-classes-20261022.md,c16-starved-loan-20261022.md}`.

Evidence level: completed matched experiment cohort on one copied save; not terrain generalization, deployed runtime or live result. Sources: `data/laya-experiments/laya-c16-2123-miniship-20261022/{freeze.json,batch.json,job-status.json}`, runner logs and journals, and `data/laya-orchestration/takeover-20261021/{status.py,failures.py,release_counts.py}`.
