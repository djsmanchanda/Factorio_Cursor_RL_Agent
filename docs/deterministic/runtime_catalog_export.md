<!-- Path: docs/deterministic/runtime_catalog_export.md | Purpose: Document the independent read-only runtime technology/prototype export and its limits. -->

# Runtime catalog export

Status: registered in the repository's `factorio_mod/control.lua`, with mock tests and a JSON schema. It has not been deployed or exercised on a live server or save.

## Files

- `factorio_mod/runtime_catalog.lua` - read-only exporter and `export_runtime_catalog` command.
- `schemas/runtime_catalog.schema.json` - strict success/failure report contract.
- `tests/test_runtime_catalog_lua.py` - focused Lua API stubs plus JSON-schema validation.

Recipes remain owned by `export_recipe_catalog`; the runtime catalog only lists recipe unlocks and their current force-enabled state. Recipe catalog version 1.3.0 now records the active mod map and count so consumers can compare the declared mod set and force with a runtime catalog capture.
The opt-in `GameBridge.export_recipe_catalog_capture(force)` collects the exact bytes of a request-ID-correlated recipe response. It requires an existing force, checks the response identity and mod envelope, and does not prune reports. Like the runtime-catalog bridge method, it does not authenticate a server, save, writer, or output tree; matching catalog declarations are not evidence of common runtime identity.

## Registration

`control.lua` requires the module for the command to exist:

```lua
require("runtime_catalog")
```

The module only registers a console command and has no event handler. A mod deployment and Factorio reload/restart are required to activate the repository change; none was performed here.

## Command

```text
/export_runtime_catalog
/export_runtime_catalog player
/export_runtime_catalog {"force":"player","surface":"nauvis","request_id":"catalog-1"}
```

- The parameter is optional. Plain text is treated as an existing force name; the default is `player`.
- JSON accepts only `force`, `surface`, and `request_id`.
- `request_id`, when supplied, must contain 1-64 filename-safe characters (`[A-Za-z0-9_-]`).
- The command never creates a force. An unknown force or surface produces an `ok=false` report.
- Output: `script-output/factorio_mod/runtime_catalogs/runtime_catalog_<request_id-or-tick>.json`.
- `GameBridge.export_runtime_catalog(surface, force)` sends an explicit scoped request and returns exact collected bytes after checking the response identity and provenance envelope. It does not validate the whole schema or authenticate the report, server, save, or output tree. It writes a report and intentionally does not prune catalog reports; callers must manage retention explicitly. Calling it requires authorization for the target connection and output tree.

## Paused research supply capture

For an explicitly authorized owned disposable saved world, the following
opt-in command captures both catalogs and computes a gross research science
balance at one paused tick:

```sh
.venv/bin/python tools/optimizer_research_capture.py --root <owned-root> \
  --target <technology> --horizon-ticks <ticks> --game-version <engine-version> \
  --boundaries '<JSON-array>' --preferences '<JSON-object>'
```

Targets may be repeated. The engine version is a caller assertion; catalog
scope, mod sets, tick and graph-read status must agree. The command writes
correlated reports under the disposable server's `script-output` and prints
their request IDs, hashes, research path and balance; it does not clean up the
reports. Stop other runners and exclude other commands during capture: the
layout lock coordinates cooperating writers only. Same-tick reports cannot
authenticate the server or save or rule out other commands while paused.
Disabled recipes remain unavailable to the solver; trigger/repeatable costs
remain unknown. The result neither selects research nor authorizes a build.

## Captured facts

Provenance:
- active mod names and versions from `script.active_mods`
- sorted mod signature (`name@version;...`)
- export tick, export command, requested force/surface scope
- explicit engine-version unknown and the base mod version
- explicit selected entity-type scope

Force and technologies:
- current/previous research, queue, progress, research-enabled flag
- documented numeric force bonuses from an explicit field list
- every force technology: researched/enabled/level/max level, upgrade flag, prerequisites, successors, effects
- `technology_graph` v1 records whether the force map and each prerequisite map and named reference could be read consistently, plus the exported technology count; false or missing cannot establish a complete research path
- research unit count, count formula, unit energy, optional unit time, and research ingredients with `kind: "item"`
- `unlock-recipe` effects with the recipe name and the force's current enabled state
- other technology effects preserved as scalar details and marked `handled=false`
- research triggers preserved as `type` plus scalar details

Selected entity prototypes:
- tile size, collision/selection boxes, direction support, underground reach, fast-replace group, next upgrade
- placement items and the first placement recipe's force-enabled state
- crafting categories, crafting speed, ingredient count, fixed recipe
- module inventory size, allowed effects/categories, effect receiver and base effects
- energy usage and documented electric/burner/fluid/heat/void source facts
- normal-quality electric-pole maximum wire distance and supply-area distance; if either is unreadable the pole records an unknown instead
- fluidboxes: index, production type, volume when available, filter identity as `kind: "fluid"`, temperatures, and pipe connections (four reported positions, direction, flow direction, connection type, category, underground reach)
- lab inputs/researching speed, beacon supply area/distribution, mining speed/resource categories, pump speed

Fluids:
- every loaded fluid prototype with default/max temperature, heat capacity, fuel value, gas temperature, emissions multiplier, and hidden flag.

All arrays are sorted or retain documented semantic order, and empty arrays are corrected from Factorio's `{}` serialisation to `[]`.

## Explicit unknowns and limits

The report carries a global `unknowns` array and per-technology/per-entity unknown lists. The main limitations are:

- **Engine version is unavailable.** Factorio does not expose the engine version to Lua. `base_mod_version` is the `base` mod version and must not be presented as the executable version.
- **Research time is not fully exposed.** `LuaTechnologyPrototype` exposes research unit count and energy but does not document `unit.time` at runtime. `unit_time_seconds` is included only when a runtime probe finds `prototype.unit.time`; the exporter never derives seconds from energy.
- **Research graph read is a scoped check.** `technology_graph.complete` checks the exported force map's keys, names, prerequisite maps, named references and count. It does not authenticate the runtime or save, prove the engine returned every technology, or validate successor edges. The new optional field preserves schema validation for older exports; older strict schema readers must update before accepting new reports. Old reports cannot establish this read check.
- **Energy units are raw runtime doubles.** The exporter does not assert joule/second semantics or convert values.
- **Effect interpretation is limited.** Only `unlock-recipe` is interpreted. Modifier and other effect types are preserved as scalar details with `handled=false`. Nested/non-scalar effect fields become explicit unknowns.
- **Quality variants are not exported.** Pole wire and supply distances explicitly request normal quality; other `get_*` facts may use the engine's default quality.
- **Fluid geometry can be incomplete.** The local 2.1.21 API does not expose fluidbox volume. Connection positions are emitted only when all four coordinates are available; otherwise the entity records an explicit unknown.
- **Entity scope is selected, not exhaustive.** Only the explicit type list in the module is exported. Unlisted modded or vanilla entity types are not claimed.
- **Surface is a request scope, not a data partition.** Prototypes and force technologies are global to the loaded mod set. An unrequested surface is recorded as `"unspecified"` with `surface_requested=false`.
- **Placement availability is narrow.** It reflects only the first placement item with an existing force recipe; it is not a full buildability, research, or inventory check. Version 1.1.0 preserves placement-item order and emits an empty list with an unknown when the source list is malformed, rather than promoting a later item. It also includes container, logistic-container, and roboport prototypes.
- **Pinned mapping is not authenticated.** `core/runtime_placement_mapping.py` derives entity-only artifact mappings from version 1.1.0 catalog bytes, requiring an explicit digest, scope, mod set, and a first placement item with count one. It rejects missing prototypes and quantities the artifact cannot represent. The digest and scope are caller-pinned, not proof of capture origin, freshness, save ownership, inventory, or construction authority. Tile ghosts and multi-item placements are unsupported; older 1.0.0 captures cannot establish correct first-item ordering.
- **Block integration remains offline.** `derive_ghost_artifact_from_runtime_catalog` derives its prototype list from converted ghost geometry rather than a caller's list. The oil builder accepts either that capture or a legacy caller mapping, not both. Capture-backed oil requires a version 1.3.0 recipe catalog whose declared force and mod set match the placement capture; binding also requires capture surface/force to match the requested construction scope. Matching declarations do not prove the exports came from the same server, save, tick, or unmodified game state. Legacy catalogs remain usable with explicit caller mappings. The generic artifact helper alone does not bind or verify a construction scope. Neither path contacts a server or grants execution authority.
- **Recipes are not duplicated.** Use `export_recipe_catalog` for ingredients, products, recipe energy, and machine categories used by the existing deterministic planner.
- **Fluid-machine compatibility is model-checked.** The bounded fluid adapter checks the exported machine type, speed, category and ingredient count before sizing. New recipe exports include all categories; older scalar-only captures can still conservatively reject compatible machines. These are captured prototype facts, not proof a built machine is configured, powered or producing.
- **Unsupported mechanics remain visible.** Probabilistic/variable products, catalysts, temperature-limited recipes, and other unsupported recipe mechanics remain the recipe catalog's concern and are not silently converted here.

## Verification

The focused test loads the module through Lua with stubbed Factorio globals and validates the emitted JSON against the schema. It covers technology levels/formulas, research ingredients and energy, unlock enabled state, unsupported effects, trigger research, machine geometry, electric energy, module facts, fluidbox filters/connections, fluid identity, request scoping, and the no-force-creation failure path.

```sh
.venv/bin/python -m pytest tests/test_runtime_catalog_lua.py -q
```

No live server, save, or `control.lua` change is part of this implementation.


The runtime export may include `recipe_fluid_boxes` with engine-order fluid ingredients and products, including fluidbox indices where supplied. When every recipe can be read, `recipe_scope` is `unlocks_and_fluid_ports`; if any recipe is unreadable, the entire section is omitted, the scope is `unlocks_only`, and `unknowns` records the omission. The ordering and indices are prototype facts, not proof of a particular world connection; the exporter and collector require disposable-runtime verification.


### Underground pipe terrain facts

The runtime connection prototype does not expose its underground collision mask. The optional 2.1.21 Space Age mask facts come from the pinned data-stage source audit in core/underground_tile_blocking.py, with an explicit caller attestation for every non-vanilla mod. A separate same-tick, bounded tile scan records runtime tile collision layers and pending tile changes. The validator checks strictly-between tiles against both endpoint masks; masked endpoints, pending changes, incomplete scans, other mod sets and unaudited prototypes remain unverified. A clear model result is not proof of a live connection, runtime identity or dispatch authority. Base-only defaults remain unknown.
