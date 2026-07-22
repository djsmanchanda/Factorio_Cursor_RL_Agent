# Path: docs/24_next_milestone_brief.md
# Purpose: Work brief for the next milestone (M6): close the execution gap so planned factories are actually built.

# M6 — Close the execution gap

## Verified state (audited 2026-07-23, commit aca006f)

Confirmed good, do not redo:
- 318 tests pass, worktree clean, M1–M5 committed (78e21ea..5b0151e).
- The force bug is fixed: `sandbox_shared.get_or_create_planner_force()` is used
  by construction, layout_executor, scaffolding and the rest.
- Charter satisfied: 13 Lua modules, 18 planner modules, all under 500 LOC.
- Surveyed builders fail closed without a WorldSpec instead of conjuring
  infinity sources.
- `build_processing_units.py --plan-only --world-spec tests/fixtures/electronics_world_spec.json`
  composes **2 managed infrastructure plans + 40 production plans** and passes
  bundle validation.
- `scripts/deploy_mod.ps1` now ships every Lua module (it shipped only
  control.lua after the split, which broke save loading).

## The problem

**Nothing has executed against a live game since M1.** Every new path is
plan-only:

| path | state |
|---|---|
| `build_processing_units.py --world-spec` | `parser.error("surveyed electronics execution is disabled")` |
| `build_advanced_circuits.py` | prints `live execution: disabled`, zero GameBridge calls |
| `compile_autonomy_goal.py` | plan-only by design |
| `--legacy-fluid-only` | the only executable route — the old disconnected-islands build |

Forty validated production plans have never met the game's placement rules.
This matters because **every serious defect in this project was found by
running it, not by testing it**:

- a substation 9.5 tiles from a 9-reach medium pole → whole line silently `no_power`
- two belts emitted on one tile → connector dead-ends, consumer starves
- pipe placed at `position` instead of `target_position` → connects to nothing
- sulfur row at 3-tile pitch → water and petroleum gas orthogonally adjacent
- a chained line has no terminal chest → false `drain_limited` verdict forever

None were caught by the suite. All were caught in-game within minutes.

## Goal

`build_processing_units.py --world-spec <spec>` (no `--plan-only`) builds the
composed factory on a live server and **proves the invariants by measurement**.

## Acceptance criteria (all measured live, report actual numbers)

1. `#find_entities_filtered{name='electric-energy-interface'}` on the sandbox == **1**.
2. Sampling machines, drills, inserters and roboports across every stage yields
   exactly **one distinct `electric_network_id`**.
3. Every roboport reports the **same `logistic_network.network_id`**.
4. **Zero `entity-ghost` remain** after bots settle, or the report names each
   stuck ghost and why (missing material / blocked tile / out of construction range).
5. Every fluid machine's input fluid box holds its fluid with **amount > 0**, and
   refineries report status `working`.
6. The run is **idempotent**: executing twice does not duplicate or orphan
   entities.

## Tasks, in order

1. **Make execution real.** Remove the `--plan-only` gate on the surveyed path;
   send the composed bundle through `GameBridge.build_layout` behind the existing
   `build_layout_authorization`. Execute infrastructure plans before production
   plans so bots have power and coverage while building.
2. **Report placement truthfully.** `layout_executor.lua` must distinguish
   *attempted* from *succeeded*: return per-action failures with entity name,
   position and reason, and surface them in the Python report. A build that
   places 0 of 40 entities must not read as success.
3. **Run it, fix what the game rejects.** Expect geometry defects. Each fix gets
   a regression test naming the live symptom.
4. **Close the survey round trip.** `survey_electronics_world.py` currently makes
   no bridge calls. Take a live `/snapshot`, cluster real resource entities, emit a
   WorldSpec, and build from *that* rather than from
   `tests/fixtures/electronics_world_spec.json`.
5. **Delete `--legacy-fluid-only`** once (1)–(4) pass. A convenient fallback to
   the broken path will get used.
6. **Do not shut down a server you did not start.** Build tools currently send
   `/quit` on exit, which killed a server a human was connected to. Make shutdown
   conditional on the tool having launched the server.

## Environment notes (save time)

- Headless server must run with `auto_pause=false`, or mod commands queue forever
  with no players connected.
- **Mutations are lost unless saved.** `/server-save` after changing the world;
  otherwise the next restart reloads the old save. (This caused repeated
  confusion — a "cleared" sandbox kept coming back.)
- The sandbox is a flat lab-tile surface: no water, no oil, no ore. Resources are
  seeded via `/ensure_sandbox_scaffolding` `ore_patches`; water and crude come from
  infinity pipes. That is legitimate for the sandbox, but it is *starter kit*, not
  production — production ingredients must come from the surveyed world.
- A megabase `/snapshot` takes ~60 s; RCON needs a long socket timeout.
- Deploy with `scripts/deploy_mod.ps1` (now copies all modules and verifies them),
  then restart the server for Lua changes to take effect.

## Out of scope for M6

Learned RL, remaining catalog executors (`open_new_mine`, tier upgrades,
parallel lines), and new recipes. They all depend on execution working first.
