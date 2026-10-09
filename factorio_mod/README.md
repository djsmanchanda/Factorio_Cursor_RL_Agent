<!-- Path: factorio_mod/README.md -->
<!-- Purpose: Document the Factorio mod files and snapshot export usage. -->

# Factorio Mod

For the complete 19-command inventory, RCON/GameBridge usage, output directories, real-base rules, and troubleshooting, use the [authoritative interaction runbook](../docs/factorio_operations.md).

## Two runtime mods from one shared source

This directory is the shared Lua source for **two** distinctly named mods:

- `factorio_cursor_rl_agent` (deterministic) — the original names, commands,
  remote interface and `factorio_mod/` script-output root.
- `factorio_laya_agent` (Laya) — built by `tools/build_runtime_mods.py` from the
  same source with a `laya_` command prefix, its own `factorio_laya_agent`
  remote interface and a `factorio_laya_mod/` script-output root.

`namespace.lua` resolves the runtime identity; the build stamps
`namespace_override.lua`, `settings.lua` and `info.json` for the Laya artifact.
Every server enables **both** runtime mods (plus `factorio_training_lab`), so
the client's enabled mod set is identical everywhere and switching servers in
the GUI never prompts "Sync mods with server". The runtime that drives a world
is selected by a runtime-global setting (`<mod_name>_active`), not by swapping
mod code or checksums; the inactive mod loads but registers no side-effect
events.

Python callers select the namespace with `tools/runtime_namespace.py` and pass
`runtime=` to `orchestrator.game_bridge.GameBridge` (`deterministic` default,
`laya` for Laya worlds).

## Files
- `info.json` (exception): Factorio requires strict JSON, so it cannot include comments.
- `control.lua`: Resolves the runtime namespace, shadows the engine `commands`
  and `helpers` globals so every module is namespaced, then loads the modules.
- `namespace.lua` / `namespace_override.lua`: runtime identity and wrappers.
- `settings.lua`: the runtime activation setting.

## Assumptions / Constraints
- Targets **Factorio 2.1** (`factorio_version: 2.1`, `base >= 2.1.0`); the current runtime is **2.1.21**. Uses the 2.1 API (`storage`, `helpers.*`, `event.entity`, `minable_flag`).
- Lua remains "dumb" and only exports raw state.
- All persistent state lives in the engine-provided `storage` table.
- Snapshot output is written via `helpers.write_file` to Factorio's `script-output` directory.

## Install / deploy
Run `scripts/deploy_mod.ps1` from the repo root — it copies this folder into
`%APPDATA%\Factorio\mods\factorio_cursor_rl_agent`. Factorio enables newly
discovered mods on next launch.

## How to run the snapshot export
1. Deploy the mod (above) and launch Factorio.
2. Load a save and open the in-game console.
3. Run:
   - `/snapshot`
4. The JSON file will be written to:
   - `script-output/factorio_mod/snapshots/snapshot_<tick>.json`

For a dedicated server, send `/snapshot` over RCON and read that server's own `script-output`, not a client-side directory. A snapshot is read-only, but raw `/sc` Lua and many other registered commands can mutate state.

## Recipe knowledge export

Run `/export_recipe_catalog player` to export every recipe known to the existing
`player` force. Each entry retains its current `enabled` state, so locked recipes
remain available for structural planning without becoming executable. The export
also records acquisition leaves from resource, plant, tree, and fish entities plus
the dedicated asteroid-chunk prototype collection. It is read-only and writes under
`script-output/factorio_mod/recipe_catalogs/`.

## GhostPlan sandbox rendering

This is a sandbox-only visualization tool. It renders ghosts in a dedicated
surface named `planner-sandbox` using deterministic spacing.

### Command
- `/apply_ghost_plan <json>`

### Behavior
- Parses GhostPlan JSON and renders ghosts only (no entity placement)
- Uses `planner-sandbox` surface with fixed spacing
- Tags are preserved from GhostPlan

### Notes
- No geometry inference
- No construction orders
- No upgrades or deletions

## Ghost observation export

Export a read-only snapshot of ghosts in the `planner-sandbox` surface.

### Command
- `/export_ghost_observation`

### Output
- `script-output/factorio_mod/ghost_observations/ghost_observation_<tick>.json`

### Notes
- Observation only (no placement, no deletion)
- Errors are reported if required tags are missing

## Deterministic planner world

Generate the versioned 500x500 contract offline with
`python tools/build_world_spec.py --output <path>`. The
`/create_planner_world <json>` command accepts that spec only with `confirm=true`
and the matching `CREATE_PLANNER_WORLD` or `RESET_PLANNER_WORLD` token. It only
targets `planner-sandbox`; reset is never implicit. Autoplace is disabled and
all resources and water are placed from the spec. The starter-kit contract
contains construction equipment only; production ingredients remain empty.

## Live resource and water seeding

`/seed_ore_patches <json>` idempotently creates the surveyed solid-resource and
crude-oil entities. `/seed_water_lakes <json>` independently lays bounded
Nauvis-style `water` tiles behind surveyed offshore pumps. Live electronics
execution invokes both commands after any topology reset and before construction;
water is terrain and is deliberately not represented as a resource entity.

## Resource survey

`/snapshot planner-sandbox` includes the persisted 500x500 bounds and seed,
neutral resource positions/amounts, and bounded water tiles. It remains a
read-only export. Compile it offline with
`python tools/survey_electronics_world.py <snapshot.json> --block-bounds -250 -250 250 250 --single-macro-block --output <world.json>`. The explicit bounds acknowledge one self-contained local macro district.
The compiler fails closed when local source footprints are unavailable; it
does not create inter-block belts or bot routes.