<!-- Path: docs/rl/Laya_Session_Handoff_2026-10-08.md | Purpose: Handoff for the ops-runner A/B campaigns, decision history/brief and the active idle-loan campaign. -->

# Laya session handoff: 2026-10-07 to 2026-10-08

Scope: Laya over the deterministic runtime's operations (`tools/run_laya_clean.py --actions deterministic`,
`training/laya_ops_runner.py`). All work ran on disposable copies of `saves/mod_playground.zip`; no installed mods,
real saves or other runtimes were touched.

## Running now

- **Unit:** `laya-ops-loans-20261008b` (user systemd), launched 2026-10-07 23:52 IST. It adds seed rounds until
  2026-10-08 07:52 IST and stops itself (hard cap `RuntimeMaxSec` 8h05m).
- **Experiment:** `ops-loans-c2-20261008b`, window until C2 / 216,000 game ticks, game speed 2, 12 parallel worlds,
  rounds of 4 seeds from seed 301. Arms are the baseline (new default, see below) plus:
  - `ops-slots-others-1`: other mall slots x1 (default x6)
  - `ops-no-idle-loans`: mall slots whose last attempt only borrowed a cell, for items nothing is short of, x0.1
  - `ops-no-idle-loans-others-1`: both
  - `ops-mf30-history`: all three history rules (repeat check)
- **Code:** frozen copy at `data/laya-experiments/ops-loans-20261008b/code` (HEAD 19b0ef5 plus the dirty tree;
  `freeze.json`). Log: `data/laya-experiments/ops-loans-20261008b/campaign.log`.
- **Report** (works mid-run):
  `cd data/laya-experiments/ops-loans-20261008b/code && ../../../../.venv/bin/python tools/laya_experiment.py report --experiment ops-loans-c2-20261008b`
- **Stop early:** `systemctl --user stop laya-ops-loans-20261008b` (kills its runners and Factorio servers).
- **Aborted first launch:** `data/laya-experiments/ops-loans-20261008-aborted`. The brief crashed some runs; this
  launch is excluded from comparisons (crashed runs were never indexed, so it is biased).

## Results so far

| Campaign | Window | Result |
|---|---|---|
| `ops-ab-c1-20261007` | C1, 16 seeds, speed 4 | C1 reached: baseline 1/16, mall-first-10 4/16, mall-first-30 8/16 |
| `ops-ab-c2-20261007` | C2, 12 seeds, speed 2 | nobody reached C2; mall-first-30 got 3/12 to C1 |
| `ops-history-c2-20261007` | C2, 52 seeds, speed 2 | C1 reached: old baseline 2/52, mf30 20/52, +follow 19/52, +no-repeat 20/52, +unstick 22/52, +all history 24/52 (not significant); nobody reached C2 |

Reports: `data/laya-experiments/<campaign>/code/data/laya-saved-world/exp-<experiment>/report.json`. Every run keeps
`evidence/decision-states.jsonl` (state, options, choice, outcome), which can later be used to fit a learned policy.

## What changed (commits, oldest first)

- `cf6c387`: `tools/laya_experiment.py --actions`; first mall-first variants; `scripts/laya_ops_ab_campaign.sh`.
- `fd7b4a5`: decision history. Options carry `repeat_fails`, `after_success_same_family`, `tried`, `stuck`. The
  quoted rules `mission.ops-history-{no-repeat,follow-through,unstick}` are weightless by default.
  `scripts/laya_ops_history_campaign.sh` adds seed rounds until a deadline (`ARMS`, `SEED_BASE`, `EXPERIMENT`).
- `a79136b`, `cb7a9a7`: **the brief**, the first section of each decision state and in `latest-status.json`.
  - Lines: GOAL (`mining-productivity-4 via C0 > ... > S1`), NOW (phase targets vs measured), NEXT, MAJOR (phases
    reached, starters/foundations/extensions/steel), RECENT (last 8, repeats collapsed), TRIED (attempts + last
    code per operation, which replaces the old history block), SINCE LAST BUILD.
  - Budget: about 2k tokens reserved; the doc rules fill the rest of 16k chars; lowest-priority soft rules are
    dropped first.
  - Each line names in brackets the shown rules that act on it (`USES`). The brief carries values, the rules carry
    strategy. Documented in `Laya_Strategy_Guidelines.md`.
  - The guided policy does not read the prose; only the option features steer it.
- `30bd415`:
  - **Adopted mall-first-30** into `mission.ops-mall-slots` (gear/cable/circuit x30, other slots x6) and
    `mission.ops-foundation` (starter-stage x1), with a reworded doc sentence. The variants are in
    `laya_variants/archive/` with a README.
  - Added the `borrows` fact on mall-slot options and `mission.ops-no-idle-loans` (weightless by default), plus the
    three loan variants.
- `19b0ef5`: the brief now tolerates `details.required` as a bare count (`rotating_mall_prerequisite_handoff`).
  This crash killed runs in the aborted launch.

Not committed: `CURRENT_STATUS.md` entries (the file had pre-existing uncommitted changes), and this handoff.

## Diagnosis trail

1. The 31e031d run stalled before C1. The copper foundation's ghosts took the inserters before the circuit slot
   could be claimed, so there were no circuits, inserters or drills, and no stone starter. Mall-first ordering fixes
   most of this.
2. In mf30 runs, 60% of decisions re-picked an option that had already failed the same way twice. History rules
   reduce this in replay, but the outcome gain is not significant.
3. In the current blocker, runs stall at `temporary_mall_batch` (transport-belt 25, medium-electric-pole 17,
   assembling-machine 12 of 71 such stalls). A belt mall slot cannot stand (its assemblers are reserved in the
   material ledger), so the tooling borrows the **circuit cell**, makes 1 belt, preempts, restores and re-borrows.
   Circuits stop, so drills (3 circuits each) never come. Laya picked "mall slot for transport-belt" 10x in
   `ops-history-c2-20261007/ops-mall-first-30-s206`. Partly self-inflicted: adopted mf30 raised other slots x2 to
   x6. In replay, p(belt slot) is 0.18 by default, 0.08 / 0.13 / 0.06 under the three loan variants.

## Next steps

1. After 07:52 IST, run the report. Adopt a loan variant only if it beats the default at C1 with no more failures;
   C2 is still unreached, so state that the variants README's long-window rule is not met. Archive the adopted
   variant and record the result in `CURRENT_STATUS.md`.
2. If C1 holds but C2 stays at 0, inspect the next stall class: medium-electric-pole batches (steel and poles) and
   whether the 18,000-tick stall limit cuts working batches at speed 2.
3. Tooling-side option (deterministic, not done): stop lending a cell whose item a queued demand is short of (for
   example the circuit cell while drills wait on circuits). That logic lives in `orchestrator/autonomous_builder.py`
   (`_bootstrap_demand_cell_affordable`, `_start_bootstrap_loan`); the lender choice ignores the demands the batch
   serves.
4. Training is not wired yet: nothing fits a model on `decision-states.jsonl` from the ops runner. The brief and
   features are recorded for it.

## Lifecycle

Owned campaign only; it stops itself. No mod redeploy, Factorio restart or runner restart is needed.

## Update 2026-10-08 01:20 IST: audit follow-up (docs/rl/Laya_System_Audit_2026-10-08.md)

Corrections to the sections above:
- The adopted mall-slot preferences stacked: core slots were x180 (30 x 6), not x30. So `ops-slots-others-1`
  and `ops-no-idle-loans-others-1` in the running campaign also cut core slots to x30; they do not isolate the
  belt-loan change.
- `repeat_fails` counted successes as failures; the history arms of both campaigns carry that bug.
- The 60% repeat figure was an earlier cohort; the completed mf30 arm is 44.8% (audit).
- Every C1/C2 count above is a **rate proxy**. The deterministic C1 predicate (at least four working mall
  assemblers including gear and cable, plus three healthy starters) failed in a smoke run that reached the proxy
  C1.

Commits:
- `aba1bfb`:
  - Receipts: produce/mall_slot/steel are ok only when producing; a `None` return is
    `prerequisite_advanced`. Starters and foundations are `submitted`, the steel fence `reserved`.
  - `repeat_fails` and `since_build` read receipts.
  - Mall-slot preferences are exclusive (core x180, other x6; the matcher supports `{"not": [...]}`), and
    `ops-other-slots-1` changes only other slots.
  - The harness indexes every planned run once (crash, wall timeout, interruption, prepare failure), and the
    report shows `untyped_stuck`.
- `bcb88b7`: `DeterministicSession.economics()` exposes active material projects (reserved and missing) and
  live loans.
  - Options carry `bill` / `bill_known` and `cell_lent`; the state has provider and reserved stock.
  - The brief gains MALL and RESERVED lines. Facts only: no rule weighs them, so default choices are unchanged.
- `8b07bc3`: deterministic milestone predicates are evaluated live beside the proxies (start, each proxy
  phase, every 10 decisions, end).
  - The report shows proxy and deterministic counts side by side.
  - Fixed the shared `orchestrator/checkpoint_observer.py` furnace survey for Factorio 2.1 (`crafter_input` /
    `crafter_output`, record-shaped `get_contents()`); it errored whenever an electric furnace existed.
    **Lifecycle: any runner using the checkpoint monitor needs a restart to pick this up.**

Queued: `laya-ops-honest-20261008` (user systemd).
- It waits for `laya-ops-loans-20261008b` to end, then runs 8 hours (cap 17h).
- Experiment `ops-honest-c2-20261008`, seeds from 401. Arms: the default, `ops-other-slots-1`,
  `ops-no-idle-loans`, `ops-mf30-history`.
- Frozen at `data/laya-experiments/ops-honest-20261008/code` (HEAD 8b07bc3).
- Report: `cd data/laya-experiments/ops-honest-20261008/code && ../../../../.venv/bin/python tools/laya_experiment.py report --experiment ops-honest-c2-20261008`

Still open, by audit number:
- **2 (steel/power cycle):** a far foundation's power bridge needs 10-26 medium poles; poles need steel; the
  steel seed needs `replacement_submitted` and a powered iron belt, which only the unbuilt refinery would
  provide. A flag fix alone cannot break it. Proposed: a steel seed fed from the iron starter's output chest
  (planner change in `planners/steel_bootstrap.py` and `_build_compact_steel_seed`), and/or earmark the power
  bridge like the foundation so the refinery submission is recorded truthfully. This needs a design decision
  in the deterministic tooling.
- **5:** preview, then choose, then execute, then reconcile seams (loan lender and batch size first).
- **6:** goal-driven fluid, research and science actions, and a research terminal predicate.
- **9:** transaction-specific progress for the stall rule.
- **10:** fit a contextual scorer on the new facts with faithful alternatives.
