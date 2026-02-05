<!-- Path: factorio_mod/README.md -->
<!-- Purpose: Document the Factorio mod files and snapshot export usage. -->

# Factorio Mod (Snapshot Exporter)

## Files
- `info.json` (exception): Factorio requires strict JSON, so it cannot include comments.
- `control.lua`: Implements the `/snapshot` command and writes deterministic JSON snapshots.

## Assumptions / Constraints
- Lua remains "dumb" and only exports raw state.
- All persistent state lives in `storage` (stored as `global.storage`).
- Snapshot output is written via `game.write_file` to Factorio's `script-output` directory.

## How to run the snapshot export
1. Enable this mod in Factorio (use this folder as the mod source).
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
