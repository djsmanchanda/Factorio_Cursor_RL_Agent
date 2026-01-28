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
