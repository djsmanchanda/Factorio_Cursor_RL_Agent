# Path: docs/archive/handoffs/31_session_handoff_2026-08-22.md
# Purpose: Resume handoff for the 2026-08-22 session: the mining-productivity-4
# bootstrap-circle fixes (four live runs), what each validated, and the two
# issues still between the run and mission completion.

## State right now

Deterministic server RUNNING (PID 226032, game 34199 / RCON 27017, isolated
root `~/.local/share/factorio-rl/deterministic`); console on 9137 running;
runner STOPPED after run 4 ended at 01:21:41. World state: iron system built
and productive (provider at (19.5,43.5)); copper mine built with belt-only
output; TWO orphaned logistic-bootstrap cells ((80.5,-10.5/-4.5) and
(87.5,-17.5/-11.5)) plus their intake chests; copper plates still zero.
A fresh restore before the next run is fine and is the established cycle.

## What this session changed (all Python + tests; NO Lua changed)

1. **Roboport chains spread out** (`stage_services.roboport_chain`). Every hop
   now travels as far as its link to the previous port allows; only the LAST
   hop is shortened to just past coverage entry. Previously every hop was
   capped there, so a gap barely past the radius stacked full supply areas on
   top of each other -- the user-flagged waste at (45,-1)/(47,-5)/(49,-9).
   Validated live: run 2 chained (55,-9) sensibly.
2. **Cold-start belt shortage bootstraps a beltless smelter**
   (`_cold_start_belt_shortage`, `_bootstrap_logistic_plate_line`,
   `build_logistic_smelter`). The first plate system's bill is mostly belts,
   belts are made FROM that plate: queuing `transport-belt=53` as mall demand
   fed the demand back into itself until the livelock guard killed run 1.
   Now a genuine cold-start belt-only shortage opens the temporary
   requester-fed cell instead. Deliberately NOT cached in
   MANAGED_INTERMEDIATE_SOURCES so the BOOTSTRAP UPGRADE can later replace it.
   Validated live: runs 3-4 opened the cell with no phantom mall loop.
3. **Stale unbacked-draw notes expire** (`UNBACKED_DRAWS.discard` on mining
   success). A saturated copper provider (4,800 plates, furnaces `full_output`)
   kept being reported as "Nothing is producing copper-plate" in stall text.
4. **The logistic-coverage remedy waits honestly** (`_wait_for_logistic_service`,
   90 s bound, 3 s polls). Run 2 died because six "waiting for the covering
   roboport to finish powering up" rounds snapped past in ~2 seconds.
   Validated live: run 3 waited the full 90 s once (then passed quickly).
5. **Belt-only mines get a logistic intake** (`live_base.belt_run_end`,
   `_mine_logistic_intake`, `build_logistic_smelter(... ore_pickup=...)`).
   Since the 2026-08-05 direct-belt change mines expose NO logistic interface;
   the bootstrap chest's correct `copper-ore x50` request could never be
   served because the mine belt ended in open air, and bring_stage_up was
   demanding a network at ORE_OUTPUT -- a BELT TILE, an impossible wait.
   The intake side-taps the run's END (never stealing from the through-belt)
   and the coverage check now names the intake CHEST. Validated live:
   run 4 placed the intake and cleared coverage instantly.

Tests: `tests/test_plate_bootstrap_circle.py` (8), chain-spacing regressions in
`tests/test_logistic_coverage.py`, wait tests in `tests/test_ghost_diagnostics.py`.
Full suite after fix 5: **1638 passed / 30 skipped**.

NOTHING IS COMMITTED. Per charter these stay uncommitted until a live run
gets past the copper bootstrap; worktree carries the four modified files plus
the new test file (and an unrelated user file `:memory:.ses` -- left alone).

## The run-by-run evidence ladder

| Run | Result | What it proved |
|---|---|---|
| 1 (22:58) | livelock guard, 12 passes | circular belt shortage diagnosed |
| 2 (00:51) | died 98 s | iron BUILT end-to-end; fallback fired; spacing fixed; no-op rounds exposed |
| 3 (01:04) | starved cell | real waits; ports charged (E=100 MJ); requester correctly configured via sections API |
| 4 (01:18) | starved cell again | intake placed; impossible belt-tile network check gone |

Each failure class found by watching was fixed before the next run; none of
the earlier classes has recurred since its fix.

## Current issues, in priority order

1. **Logistic robots never reach the new networks (PRIMARY BLOCKER).** Live
   probe: flying logistic=6 total; port inventories hold logistic robots only
   in starter-area networks ((34,27): 44). Copper-area ports ((92,-40) empty,
   (78,-14) construction-only) form networks separated from the starter ones
   (logistic radius 25 < chain gaps), so the cell's requesters can never be
   served regardless of how correct their requests are. Fix direction: make
   `ensure_logistic_coverage` chain at LOGISTIC adjacency (<=25-tile gaps so
   intake and cell share ONE network) AND provision logistic robots into the
   new ports from force stock (starter provider holds logistic-robot x50;
   inserting into a roboport's `roboport_robot` inventory needs either a tiny
   Lua action/command or a scripted RCON insert -- decide which next session;
   Lua means redeploy + restart).
2. **The fallback rebuilds elsewhere instead of healing.** Run 4 pass 2
   re-surveyed a different `smelter_origin`, orphaned the first cell, and the
   second cell's health StuckError escaped `build_mining_stage`, ending the
   run. Fix direction: recognize an existing bootstrap cell
   (`logistic_smelter_origin` already recognizes the geometry) and repair/
   supply it rather than opening another; catch the health StuckError in the
   `_bootstrap_logistic_plate_line` path and raise
   `ProductionPrerequisiteDeferred` so prep defers instead of dying.
3. **Health verdict too fast for bots.** "no_ingredients and produced nothing
   in 20s" fires long before a bot can physically fly ~40 tiles, load ore,
   and return. Make the grace distance-aware (or wait for the first delivery
   event) for bot-fed cells only.
4. Cosmetic leftovers: `find_line.working_count==0` still misreads saturated
   lines in CAPACITY/PLATE SOURCE messaging (harmless since fix 3 above);
   PREP DEMAND/MALL DEMAND duplication of one item across passes remains
   noisy (known).

## What NOT to do

- Do not weaken the cold-start gate: non-belt shortages (drills etc.) must
  keep propagating as mall demands; only belt-family shortfalls on a base
  with no plate line take the bootstrap path.
- Do not cache the bootstrap cell in MANAGED_INTERMEDIATE_SOURCES.
- Do not restore the mine side-tap provider for normal belt-fed mines --
  belt-only output is intentional; the intake exists only for the bot-fed
  bootstrap path.
- No mod redeploy is required for any current change (all Python); the
  deployed mods already match the repo.

## Open / next session

1. Implement issue 1 (single-network guarantee + robot provisioning), then
   issue 2 (idempotent reuse + defer-on-health-failure), then issue 3.
2. Re-run the cycle: deploy (only if Lua touched), restore save, start runner
   `--technology mining-productivity-4`.
3. Expected success shape: intake -> requesters served -> copper plates ->
   belt stock fills -> initial iron refinery upgrade replaces the temp cell
   (BOOTSTRAP UPGRADE) -> science readiness stocks packs -> research queue
   completes mining-productivity-4.
4. Once a run clears the copper bootstrap, append CURRENT_STATUS.md entries
   and create scoped commits (`fix(deterministic): ...`) for the five fixes.
