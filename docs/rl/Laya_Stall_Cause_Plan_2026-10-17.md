<!-- Path: docs/rl/Laya_Stall_Cause_Plan_2026-10-17.md | Purpose: Common stall causes across the reset-chest cohort (118 runs), correlated with the Muse and Sonnet helper findings, and the ordered plan that follows. Diagnosis and plan only; not adopted, not committed. -->

# Laya stall causes and plan — 2026-10-17

## Evidence

- **Runs:** 118 completed ops runs. That is reset-chest-20261017 plus sidecar slots: control a63e074 ×44, rung f1469e1 ×38, chest f40ef39 ×36, plus the normal rung run seed 76900. The cohort used a fresh committed save at speed 2, under the 18,000-tick stall guard.
  - Evidence level: matched cohort on frozen code. All runs used one starting save.
- **Per-run classification:** 12 Claude Haiku 5.5 T3 analysts produced `data/laya-experiments/reset-chest-20261017/analysis/shard-*.results.jsonl`, 118/118 rows. The procedure and taxonomy are in `analysis/BRIEF.md`.
- **Mechanical cross-checks:**
  - A grep baseline over all logs (`analysis/baseline.py` → `baseline.json`).
  - Pattern counts and menu checks over `decision-states.jsonl`. These are the numbers below.
- **Helpers on seed 76900:**
  - Sonnet: `docs/rl/laya_helper_runs/normal-rung-20261017-s76900-sonnet/findings.md`.
  - Muse: `data/laya-runs/normal-rung-20261017/code/docs/rl/laya_helper_runs/normal-rung-20261017-s76900-1792257431/findings.md`.

## Common cause

**A deterministic tool hits an internal prerequisite it does not expose as a choice.** Examples:
- A mall slot needs a reserved assembler, long-handed inserter or chest.
- Plastic needs a chest that needs plastic.
- An expansion site is unbuildable.
- A ladder loan hop never completes.

The menu then holds only the failing produce/wait options. Laya repeats them until the stall guard fires.
- In **88 of 113** classified runs, the option that would have broken the chain was not offered (5 were unknown).
- **103 of 118** repeated a failing option.
- Repetition is the symptom. Menu and fact coverage is the cause.

Stall phase is the phase the run was working toward, from its last decision:

| Working toward | Runs | Dominant mechanism (verified counts) |
|---|---|---|
| C2 | 55 (chest 25) | **Mall capacity deadlock.** Slot claims are blocked by *reserved* materials, and borrow retries find no lender. Copper/iron expansion is unbuildable in 13 runs. |
| C4 | 38 | **Chemical ladder.** Plastic↔passive-chest gate, serial loan hops that never finish, and roboport coverage gaps for the remote oil site. Pipe-loan release is `untyped_stuck` in 8 runs, and the steel seed has no site in 5. |
| C3 | 15 | Long-handed inserters/chests with no producer. The inserter↔long-handed-inserter bootstrap cycle appears in 5 of 15. |
| C1 / C6 | 6 / 4 | Lender refusals early / site unbuildable. |

### Mechanisms, verified across all logs

1. **Slot claims are blocked by reserved materials, not a full pool.**
   - "pool X free" counts empty slots.
   - A new slot also needs assembling-machine-1 plus a dedicated passive provider (`_bootstrap_demand_cell_affordable`).
   - When those are reserved, the code falls through to borrowing. With no borrowable cell it logs "no borrowable assembler (0 active loans)" (`orchestrator/autonomous_builder.py` ~9148–9205).
   - **Counts:** reserved assembling-machine-1 blocks slot claims in 115/118 runs. The message appears in the last 400 lines of 31/55 C2 stalls.
   - The slot-claim shortage never reaches the menu. In the final 20 decisions, 81 runs needed assembling-machine-1, and **63 of them were never offered `op.produce.assembling-machine-1`**. The option exists: 89 runs saw it at some point.
2. **Deferral is not treated as failure.** `rationed_mall_borrow_deferred` leaves `unchanged_failure=False`, so the no-blind-retry rules cannot fire. Example: chest-s76713 had 10 deferrals and both options still sat at p≈0.4.
3. **Laya cannot see mall capacity.** 0 of roughly 8,000 decision briefs mention pool, lender or active loans.
4. **Chest arm regression (C2 7/36 vs 23/36).** f40ef39 bundled two factors.
   - Its "dense top/bottom halves admitted early" part makes long-handed inserters block slot claims in **36/36 chest runs at a median of +113 s**, against **+1050 s in 35/82 control and rung runs**.
   - Quad-mall cells appear at +393 s vs +1150–1390 s.
   - The chest-hold part is not separated by this cohort.
5. **Plastic↔passive-provider-chest gate.**
   - Plastic's bill needs a passive provider. That needs an advanced circuit, which needs plastic.
   - The deterministic answer is "establishing plastic-bar before passive-provider-chest", and it loops.
   - **Counts:** 41 runs; in the last 400 lines of 14 C2, 10 C3 and 9 C4 stalls.
6. **Chemical ladder churn.** Borrowed cells make temporary seeds (oil-refinery → pipe → offshore-pump → pumpjack), are pre-empted, and move to the next seed. Plastic is re-chosen at p≈0.9 with no completed rung.
   - Roboport coverage waits appear in the last 400 lines of 23/38 C4 stalls. Coverage targets are 87–297 tiles away against a 55-tile radius.
7. **Sites.** `district_expansion_unbuildable` appears in the last 400 lines of 30 stalls, and the steel seed has no site in 12. The same unbuildable district is re-offered up to 19 times.
8. **Tool defects.**
   - `release_bootstrap_loan_pipe` → `untyped_stuck` "blocked by real infrastructure": 15 runs, 8 at a C4 stall.
   - "power gap cannot be routed safely" `untyped_stuck`: 30 runs, early and non-fatal.
   - Inserter↔long-handed-inserter cycle, where the tool schedules a persistent inserter producer before consuming the reserve: 8 runs.
   - Candidate, unverified: an assembler loan holds at 2/3 crafts while reporting "producing".
9. **Misleading facts.**
   - The board reports `plastic-bar recipe_unavailable` (no record in the board's recipe lookup) even in runs that produced plastic: 175–219 lines in each C4-reaching run.
   - "NOTHING IS PRODUCING IT" also fires on benign stocked-seed fallbacks.
   - `SUMMARY accepted=` is *ever passed*, not final (`laya_ops_runner.py:652`). 13 runs later failed a phase listed there.

## Correlation with the helpers (seed 76900)

| Claim | Verdict across 118 runs |
|---|---|
| Sonnet: iron-plate starvation (gear/cable/brick cells drain plate) | **Real but secondary.** Tagged in 17 runs, primary in 7, all C1/C2. Most runs are rate-capped (iron 1.25/3, ore-limited), not stock-drained. Global plate stock is not logged, so it is under-tested. |
| Muse: no free lender / eligibility | **Right symptom, refined mechanism.** Free slots exist, but the *materials to claim a slot* are reserved. It is not a hidden eligibility filter. |
| Muse: single circuit cell / loan churn | **Seen in C4 ladder runs** (serial seed hops); minor at C2. |
| Both: policy cannot see pool occupancy | **Confirmed.** 0 briefs mention it. |
| Both: the needed option was missing (raise gate, reclaim lender, add iron) | **Confirmed and generalised.** The breaking option was missing in 88/113 runs; most often assembling-machine-1, long-handed inserter, a different site, or the plastic bootstrap chest. |

## Plan

Order follows AGENTS.md: facts first, then operations, then a quoted rule or weight as a variant. Tooling defects are fixed in the tool. Every step is verified with unit tests plus a replay over existing journals, then a matched cohort.

### Step 0 — evidence hygiene (small, unblocks reading results)
- Report final acceptance alongside ever-passed acceptance in SUMMARY and the batch report.
- Give chemical-plant recipes a board record so `recipe_unavailable` means what it says.
- Reword the stocked-seed "NOTHING IS PRODUCING" line.
- Launch tooling: units get `KillMode=process` (helper survival), and the sidecar ramp gets a shrink path.

### Step 1 — facts (largest bucket: 55 C2 stalls)
1. Propagate slot-claim blockers into demand. A reserved assembling-machine-1, long-handed inserter, inserter or chest that blocks a new slot becomes a bill item with its owner. Then the existing `op.produce.*` options appear. This is menu demand, not a priority branch.
2. Treat `rationed_mall_borrow_deferred` and `rotating_mall_prerequisite_handoff` as unchanged failures in the option features, so the existing no-blind-retry and unstick rules apply.
3. Add mall capacity to the decision state: slots free/limit, active loans, borrowable lenders, and the reserved slot materials with their owners.
4. Add plate facts: iron/copper plate stock, and the draw from standing cells against open bills. This is needed to test Sonnet's hypothesis properly.
5. Add ladder facts: current rung, its blocker, loan progress, and roboport distance against radius.

### Step 2 — operations (expose existing capabilities as choices)
1. Mall-cell gate control: raise a cell's stock gate to the open bill, and pause a cell accumulating unrequested intermediates while plate-bound.
2. Reclaim a lender cell (frees its slot, assembler and chest).
3. Plastic bootstrap as a choice: fund the plastic cell from a kit chest, instead of the chest arm's hard-coded hold. Also build the long-handed-inserter cell from stocked inserters instead of the tool's inserter-producer-first cycle.
4. Offer the next site when a district or steel seed is unbuildable, rather than re-offering the same one.
5. Extend roboport coverage toward the oil site as an explicit option.

### Step 3 — tooling defects (deterministic runtime fixes)
- `release_bootstrap_loan_pipe` blocked by infrastructure must route around or return a typed block.
- Type the power-gap failure.
- Remove the inserter↔long-handed-inserter reserve cycle.
- Investigate the assembler-loan "producing at 2/3" report.

### Step 4 — variants (matched cohorts; one factor per arm)
- **Now, before Step 1:** split f40ef39 into *chest-hold only* and *halves-first only* on top of rung, and test both against rung. Prediction: halves-first carries the C2 regression; chest-hold is neutral or positive at C4.
- **After Steps 1–2:**
  - Lower wait mass after repeated unchanged deferrals.
  - A plate-scarcity penalty on side builds.
  - Cap concurrent earmarked foundation bills to demonstrated batch throughput (Muse).

### Measurement
- Baseline is rung f1469e1, at 10–11 concurrent at speed 2 (the laptop ceiling).
- Size: 24 matched seeds × arms, about 20 min per run.
- Primary metric: proxy C2 rate (baseline 23/36) and median game-minutes to C2.
- Secondary metrics:
  - Final-acceptance C2/C3.
  - C4 rate (2/36).
  - Share of stalls where the breaking option was offered (baseline 25/113).
  - `untyped_stuck` count.
- Adopt only per `docs/rl/laya_variants/README.md`.
