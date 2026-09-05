<!-- Path: docs/archive/handoffs/35_run_observation_2026-09-05.md -->
<!-- Purpose: Record the post-budget-fix run and live diagnosis of bootstrap-loan starvation. -->

# Live run observation — post-budget-fix session (2026-09-05, 06:13 IST)

Target: `mining-productivity-4` (routes through the science ladder).
Runner PID 541323, server PID 540820, fresh isolated save.
Code: includes `d9c8b14` (plan-budget refund-to-mark) + `12816ef` (pipe dives)
+ `8fae36e` (corridor reservations) + `9d8b435` (splitter 12).
Prior run (03:29) died +4338s on BudgetExhausted after 2200s of
fast-inserter/belt rotation. This session tests whether productive
rotation survives and where it goes next.
Method: poll every ~2 min — log delta (game-time `+Ts`), RCON `game.tick`
(effective UPS), runner CPU, save mtimes. Game runs at 60 UPS unless noted.

## R0 baseline — wall 06:14, game +0s (RUN START 06:13:22)
- Fresh world, fresh runner. Prior 00:33 (pole STUCK +3971s) and 03:29
  (BudgetExhausted +4338s) runs archived in-log.

## R2 — wall 06:15 → 06:16:53 (~110s wall, +153s game, tick 248402)
- 1:1 pace. Bootstrap nominal, same seed shape as prior runs (gear mall
  (35,31), roboport chain, plate starter + power fixes in 2 rounds).
- Server side: runner CPU 1.3%, tick advancing at 60 UPS. No anomalies.

## R3 — wall 06:16:53 → 06:19:03 (130s wall, +129s game, 3025 → 3058 lines)
- Belt batch climbing cleanly 40 → 184/240 transferable; crafts advancing
  61 → 91. No preempt churn yet (single loan undisturbed).
- 60 UPS, runner 1.1%. Healthy early rotation.

## R4 — wall 06:19:03 → 06:21:19 (136s wall, +140s game, 3058 → 3180 lines)
- Belt hit 200 at +343s, preempted cleanly to copper-cable; AM1 loan chain
  with parallel e-circuit loan (+374s) — textbook rotation, no stuck waiter.
- Matches 03:29 shape almost tick-for-tick. Fix validation comes later
  (saturation phase ~+2000s); nothing to change yet.

## R5 — wall 06:21:19 → 06:24:36 (197s wall, +190s game, 3180 → 3249 lines)
- Drill/fast-inserter/e-circuit loans rotating; FI waiting on circuits
  with parallel loan covering (+653s). Standard mid-bootstrap contention.
- Error grep: all 7 STUCK/Budget/BLOCKER lines are pre-2807 (old runs).
  Current run: zero errors. 60 UPS.

## R6 — wall 06:24:36 → 06:26:50 (134s wall, +137s game, 3249 → 3356 lines)
- Standing e-circuit cell READY +725s (03:29 run: +752s), belt cell placed
  +726s. Tracking ~30s ahead of the last run — normal seed variance.
- No churn, no errors. Next watch: iron/copper foundations ~+850-1100s.

## R7 — wall 06:26:50 → 06:28:59 (129s wall, +125s game, 3356 → 3381 lines)
- Quiet rotation interval (25 lines, all repeats/deliveries). Iron
  foundation due ~+850s. Nothing to change.

## R8 — the 06:13 run died +977s: no_progress on splitter-0% (FIRST death)
- Shape: splitter loan (gear cell) 0 crafts waiting on circuits; e-circuit
  standing cell READY-latched at +725s produced nothing; no e-circuit batch
  ran after +660s; guard fired correctly.
- Forensics: the +725s "standing cell" was a transient loan machine
  (disabled_by_control_behavior gate signature; same cell reborrowed for
  splitter +788s; N=0 e-circuit assemblers post-mortem). Prep latched
  READY onto a loan cell — real hygiene bug, queued for a later loop.
- Deeper: rotation gridlock (belt→splitter→circuits→nobody). Two diagnostic
  restarts reproduced it in 137s/389s on the preserved world.

## R9 — live interrogation of the gridlock (decisive)
- Drove the REAL `_blocked_loan_to_yield` / `_feeder_waiter_preempts` /
  `_loan_blocked_inputs` against the dead world via RCON (read-only).
- Result: `blocked_inputs` correctly = [electronic-circuit] for all three
  loans, both waiters flowing — but yield/feeder returned NONE. Cause:
  every holder escaped via `completed_step_targets` (stale prerequisite
  credit: splitter/FI) or early crafts (AM1). Stale credit shielded present
  paralysis. (Lesson: my first two diag runs returned [] spuriously —
  the diag process lacked the live recipe catalog. Always install it.)
- Fix implemented (uncommitted, base itself uncommitted — see R11):
  current-step progress (`finished>baseline`, `minimum_fulfilled`) is now
  the only shield; stale prerequisite credit no longer protects.
  Plus: `_queue_starved_loan_ingredients` falls back to scanning active
  holders when the served item has no loan of its own (handoff waiters
  never queued their holder's orphans).
- Evidence: 5 new tests; 749 fast + 659 deterministic green.

## R10 — live verification on the dead world (fix works, frontier moves)
- Restart 1: yield fired pass 1 (`yielding its cell to transport-belt,
  which unblocks electronic-circuit`); belt 48 → 64+, rotation advanced.
- Restart 2: died +389s on inserter-33% (was belt-32%): belt completed,
  frontier moved one step.
- Restart 3: e-circuit queued+served (+35s/+137s); died +404s on
  e-circuit-0%. New frontier: belt cell never frees — belt capped ~64 by
  output blockage (machine `full_output`, output inserter
  `waiting_for_space`, provider (39.5,37.5) holds 48 belts + 1 FI mixed).
  Machine gate is fine (belt<148 fulfilled); feed path has stock.
- The yield/queue fixes are LIVE-PROVEN (three restarts, frontier moved
  twice). Remaining stall is cell plumbing/limits, not rotation logic.

## R11 — status and next plan
- Fix validation so far: rotation unsticks and advances; deaths moved
  belt-32% → inserter-33% → e-circuit-0% across restarts. Each death is
  further downstream with real output banked (belt +16, inserter 4/12).
- Commit situation: scoped commit INFEASIBLE — the loan/yield/rotation
  machinery my fix builds on was never committed (HEAD has zero trace of
  `_blocked_loan_to_yield`/`_feeder_waiter_preempts`; ~6k lines uncommitted
  across ~30 files). Options for user: checkpoint-all vs leave uncommitted.
- Next loop target: belt-cell output blockage (provider assignment for loan
  cells, limit freshness vs step growth, output-inserter routing, stale
  requester sections like the cable-x3 leftover, mixed-chest hygiene).
- Residual risks to watch: infinite "productive" rotation without goal
  movement (passes credited, plans net-zero) — the 2-min observation
  cadence is the backstop; prep-latch phantom READY still open.

## R12 — fresh loop iteration (RUN START 15:00:03, runner 94968)
- Campaign-fresh world+server+runner with all three fixes live
  (budget refund, yield stale-credit, handoff queue fallback).
- Baseline +37s: stone starter placed already, nominal. (A 14:47 run
  also appears in the log — superseded by this fresh start.)
- Observation cadence resumes; validation targets: splitter-trim timing,
  no BudgetExhausted, yield/dives lines if oil contention recurs, and
  the belt-provider frontier from R10.
