# Path: docs/26_session_state.md
# Purpose: Compact resume handoff — current state, in-flight work, and how to run everything, as of 2026-07-23.

## Goal
Autonomous agent builds a Factorio factory from raw resources (ore/oil/water scattered over a 500x500 map)
up through advanced circuits / processing units, maximizing research. Deterministic symbolic planners +
authorization-gated execution. RL is currently a deterministic baseline policy — no learned model yet.
Charter: `AGENTS.md`. Hard constraint: files <=500 LOC.

## How to run
- Factorio 2.0.77 at `E:\Games\Factorio`. Mod: `factorio_mod/` (14 Lua modules). Deploy with
  `scripts/deploy_mod.ps1` (copies ALL `*.lua` + verifies — do not hand-copy just control.lua).
- Headless server: isolated data dir under scratchpad (`fdata/`), launched via
  `factorio.exe --config <fd>\config.ini --mod-directory <fd>\mods --start-server <fd>\1M_test.zip
  --server-settings <fd>\server-settings.json --rcon-port 27015 --rcon-password planner_test`.
  `server-settings.json` needs `auto_pause=false`. Surface `planner-sandbox`, force `planner`.
- RCON: `cd repo && MSYS_NO_PATHCONV=1 python tools/rcon_client.py --password planner_test "/sc <lua> rcon.print(...)"`.
  Steam build refuses redirected stdio — launch via plain `Start-Process`, not piped.
- **CRITICAL**: mutations are lost on restart unless saved (`/server-save` or `bridge.save_game`) —
  the on-disk `1M_test.zip` reloads otherwise. Caused repeated "cleared sandbox came back" confusion.
- Dashboard: `python tools/dashboard_server.py` (port 9137 — 8765 collides with user's AC).

## Architecture map
- `planners/` (18 modules): local_layout_planner, line_layouts, chain_layouts, fluid_layouts,
  fluid_routing, infrastructure, infrastructure_geometry, roboport_coverage, resource_layouts,
  electronics_block, electronics_world, electronics_contracts, item_routing, plan_validation,
  preflight, recipe_data, resource_survey, world_generation, sandbox_infrastructure.
- `core/`: fluid_systems, quality_modules, bottleneck_diagnosis, action_catalog, baseline_policy,
  metrics, progress_state, etc.
- `orchestrator/`: game_bridge, expansion_daemon, loop_daemon, run_cycle, chain_telemetry.
- `tools/`: build_processing_units, build_advanced_circuits, electronics_execution,
  survey_electronics_world, verify_factory_invariants, rcon_client, dashboard_server.
- `docs/`: 20 = invariants, 21 = external game knowledge, 22 = RL decision layer, 23 = fluid
  systems, 24 = M6 brief, 25 = M7 brief (spidertron + ore), 26 = this file.

## Verified live game facts
- Pipe connects at `target_position` (1 tile OUTSIDE footprint), not the connection position.
- Machine pitch varies to avoid fluid mixing: sulfur 4, refinery 5, others 3.
- Fluid network purity is a hard rule — mixing is unrecoverable.
- Pole wire reach: big=32, medium=9, substation=18 (poles bond within the SHORTER of two reaches).
  Supply area: big=2, medium=3.5, substation=9.
- Roboport `construction_radius`=55; logistic/link radius ~46.
- Spidertron supports fusion-reactor + personal-roboport + battery equipment, trunk cargo, autopilot
  waypoints — a self-powered mobile builder.
- `chemical-plant` in-N/out-S vs `oil-refinery` in-S/out-N (opposite-handed).
- `advanced-circuit` needs 3 solid ingredients — >2 belt lanes needs a 3rd feed path.

## Milestones done (committed)
- M1: unified power + roboport coverage.
- M2: raw → advanced-circuit → processing-unit chain incl. sulfur→acid.
- M3: 500x500 world spec.
- M4: survey-based resource allocation.
- M5: autonomy compiler (composes surveyed production programs).
- M6 (this session, committed): live execution wired — `build_processing_units` executes the composed
  2-infra + 40-production plan bundle via GameBridge (4856 entities placed, 0 placement failures);
  `planners/preflight.py` validates the composed bundle (power reach, duplicate tile, footprint,
  fluid mixing, underground, inserter, roboport coverage, electric-only); `tools/verify_factory_invariants.py`
  measures 6 live invariants (unknown never counts as pass); mod adds `/verify_electronics_execution` +
  `schemas/live_execution_report.schema.json`; `deploy_mod.ps1` ships all modules.

## CURRENT problem (M7, in progress)
First live builds produced a **fragmented, ore-less** factory. Measured live: `ore_tiles=0` (58 drills,
0 mining), 3 roboport networks, 8 electric networks, ~1371 ghosts stuck.

Root causes and status:
1. **Tech out of sync — FIXED.** Planner force was fresh (10 techs vs player's 252; bot speed +0% vs
   +955%; inserter capacity bonus 0 vs 3) → bots ~10x slower. Synced live to match the player (bot
   speed now 9.55) and encoded in `factorio_mod/sandbox_shared.lua`'s `get_or_create_planner_force`
   (syncs research + worker-robot speed/battery/storage modifiers from the player on force creation).
2. **No ore — code-complete, NOT live-verified.** `tools/electronics_execution.py` now seeds ore via a
   new mod command `/seed_ore_patches` (`factorio_mod/scaffolding.lua`) after reset, before build, with
   a fail-closed guard that every drill footprint is covered. `game_bridge.seed_ore_patches` added.
3. **Construction fragmentation when all ghosts are placed at once — IN PROGRESS** (spawned sub-agent
   task, ref `a68ff7bfe21865d75`). Pivoting to **center-out radial phased construction**: one central
   powered roboport with bots, build ring by ring outward, each ring fully built + powered before the
   next ring's ghosts are placed — never ghost outside live coverage. This structurally fixes the
   3-roboport/8-electric-network fragmentation because everything is built in coverage order.
   Spidertron (`factorio_mod/spidertron_builder.lua`) is an optional self-powered mobile center-seed.

## Durable lessons
- Every real defect was found by RUNNING the game, not by tests. Preflight passing != game truth
  (preflight passed while the live game showed 16 networks).
- An earlier "one logistic network" claim was a MEASUREMENT BUG (loop broke after the first roboport) —
  always measure ALL entities, never stop early.
- Verify invariants live with `tools/verify_factory_invariants.py`, not by assertion or plan inspection.

## Test / commit status
- Baseline: 400 passed, 1 skipped.
- NOT committed this session: tech-sync change in `sandbox_shared.lua`; ore seeding
  (`tools/electronics_execution.py`, `factorio_mod/scaffolding.lua`, `game_bridge.seed_ore_patches`);
  spidertron radial-builder work in flight, uncommitted. Preflight/harness/roboport_coverage/execution
  work partly landed earlier as commit `ff6764b`.

## Next steps
1. Finish the center-out radial builder.
2. Redeploy mod (`scripts/deploy_mod.ps1`), restart headless server.
3. Seed ore, build center-out, then verify with `tools/verify_factory_invariants.py`.
4. Target invariants: 1 electric network, 1 roboport network, ore present under every drill,
   0 stuck ghosts, fluid present in machines.
