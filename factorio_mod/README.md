<!-- Path: factorio_mod/README.md -->
<!-- Purpose: Document the Factorio mod files and snapshot export usage. -->

# Factorio Mod (Snapshot Exporter)

## Files
- `info.json` (exception): Factorio requires strict JSON, so it cannot include comments.
- `control.lua`: Implements the `/snapshot` command and writes deterministic JSON snapshots.

## Assumptions / Constraints
- Targets **Factorio 2.0** (`factorio_version: 2.0`); uses the 2.0 API (`storage`, `helpers.*`, `event.entity`).
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