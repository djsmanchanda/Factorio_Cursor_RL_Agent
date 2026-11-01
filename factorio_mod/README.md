<!-- Path: factorio_mod/README.md -->
<!-- Purpose: Document the Factorio mod files and snapshot export usage. -->

# Factorio Mod

For the complete 19-command inventory, RCON/GameBridge usage, output directories, real-base rules, and troubleshooting, use the [authoritative interaction runbook](../docs/factorio_operations.md).

## Files
- `info.json` (exception): Factorio requires strict JSON, so it cannot include comments.
- `control.lua`: Loads the modules that register the mod's console-command surface.

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

For a dedicated server, send `/snapshot` over RCON and read that server's own `script-output`, not a client-side directory. A snapshot is read-only, but raw `/sc` Lua and many other registered commands can mutate state.

## Runtime identity probe

`/research_lab_ownership <json>` reports robot-built entities whose ghost tags
carry the requested `episode_id`, `packet_id`, and `transaction_id`, within an
explicit `surface` and `force`. Supply a filename-safe `request_id`; responses
are written under `factorio_mod/research_lab_ownership/`. The recorder captures
the replacement unit number when the requester trash hook recreates a chest.
It retains at most 4,096 records in save storage and fails closed after that
capacity is exceeded; no automatic reset or pruning is provided. Lab packet
ghosts carry a persistent transaction identifier, and the Python bridge reads
the correlated report. Tags and request scope are caller assertions, not proof
of lane ownership or dispatch authority. An occupied position with only an
unbuilt ghost cannot satisfy the built-entity evidence required for retry.

## Rocket silo observation

`/rocket_silo_observation <json>` exports a read-only, request-correlated
observation of existing rocket silos for one explicit surface and force:

```json
{"request_id":"silo-check-1","surface":"nauvis","force":"player","unit_number":7}
```

`request_id` is required and must contain 1-64 characters from
`[A-Za-z0-9_-]`; it names the report file. `surface` and `force` must be
non-empty names of existing game objects. `unit_number` is an optional
non-negative integer filter; when omitted, every rocket silo owned by that
force on that surface is reported. A malformed payload with a safe
`request_id` writes a correlated `ok: false` report. A missing, malformed, or
unsafe `request_id` writes nothing and cannot be correlated.

Reports are written to
`script-output/factorio_mod/rocket_silo_observations/rocket_silo_observation_<request_id>.json`,
separate from the launch event and launch scope directories. Each silo entry
reports:

- its prototype name, save-lifetime `unit_number` and position
- `rocket_silo_status` and entity `status` names
- actual `rocket_parts` plus prototype `rocket_parts_required` and
  `rocket_parts_storage_cap`
- the current recipe, when `get_recipe()` returns one
- `send_to_orbit_automatically` under `auto_launch`
- the attached rocket and, only through a valid rocket, its cargo pod

Every optional fact is an object with `state: "available"` and a `value`, or
`state: "absent"` (with an optional reason), or `state: "unavailable"` with a
reason. The observer never converts a missing or invalid engine reference into
a normal value.

Reference API: `data/factorio-2.1.21/runtime-api-2.1.21.json` (application
version 2.1.21, API version 6). The module uses only attributes listed there.
Provenance limits: Lua mods cannot read the live engine build, the loaded save
identity, or a save's live runtime API version, so the reference version is
static provenance, not live verification. `auto_launch` maps to
`send_to_orbit_automatically` and is only meaningful for launch products whose
prototype has an automated `send_to_orbit_mode`. This command never launches a
rocket, mutates an entity, or grants execution authority.

`GameBridge.observe_rocket_silos` generates a request ID and returns the
schema-validated, scope- and filter-checked response as detached data. It writes
a report but does not prune this directory. Correlation and no-follow checking
of the final file do not authenticate the server, save, output writer, or parent
directories; readiness is neither a launch event nor authority to launch.
The prototype-name field is optional for older reports; readers with the older
strict schema must be updated before they consume reports from the new exporter.

## Recipe knowledge export

Run `/export_recipe_catalog player` to export every recipe known to the existing
`player` force. Each entry retains its current `enabled` state, so locked recipes
remain available for structural planning without becoming executable. The export
also records acquisition leaves from resource, plant, tree, and fish entities plus
the dedicated asteroid-chunk prototype collection. It is read-only and writes under
`script-output/factorio_mod/recipe_catalogs/`.
Version 1.3.0 additionally records the active mod names, versions, and count;
matching those declarations to another capture does not prove common server,
save, or capture time. Existing 1.0–1.2 catalogs remain supported by the schema.
The exporter records the complete sorted category set and retains its first
category in the legacy scalar field. Missing or malformed category data marks
the recipe unsupported rather than assuming `crafting`. Older captures without
the set remain usable, but can conservatively reject compatible machines.
For request correlation use `/export_recipe_catalog {"force":"player","request_id":"example"}`;
the ID appears in the report and filename. `GameBridge.export_recipe_catalog_capture`
requires an existing force and returns the collected bytes. It writes a report,
does not prune catalog reports, and does not authenticate their source. The legacy
plain-force command and `export_recipe_catalog` bridge method are unchanged.

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


The opt-in `/optimizer_build_evidence_v3` command includes tagged robot-build events for entities that have no unit number. Their current state is `identity_unavailable`: an entity later found at the same position could be a replacement. The original `/optimizer_build_evidence` v2 response omits those events for compatibility. Both versions share a bounded record store; overflow makes reports fail closed. These records do not authorize configuration or establish ownership.


The read-only optimizer_consumer_observation command queries one explicitly named entity, position and unit number on the requested surface and force. Its result reports the current recipe and the engine lifetime products_finished counter, or an explicit unavailable state. It does not establish ownership, a connection to an optimizer block, consumed fluid or commissioning. Deploying this command requires the usual separately authorized mod deployment and reload.
