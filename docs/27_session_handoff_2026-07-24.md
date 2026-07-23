# Path: docs/27_session_handoff_2026-07-24.md
# Purpose: Compact resume handoff for the session that fixed live network fragmentation and ran the first full radial build attempt.

## State right now (read this first)

Latest commit: `fdf654a` "feat(M7): radial production construction + fix pole auto-wire +
substation-primary spine". A follow-up commit adding oil/crude-oil seeding (see below) should
land right after this doc — check `git log -1` to see if it's already there.

**THE FULL LIVE BUILD SUCCEEDED this session.** After the fixes below, a real end-to-end run
(`tools/build_processing_units.py --construction-mode radial --existing-topology reset`) placed
all 553 infrastructure entities atomically, all 6 production rings (4325 actions) via the
radial spidertron builder, and independently verified via `tools/verify_factory_invariants.py`:

- `energy_interface_count`: **PASS** (exactly 1)
- `electric_networks`: **PASS** (exactly 1, id `10018`, 998+ entities sampled)
- `logistic_networks`: **PASS** (exactly 1, id `3843`, 45 roboports, 0 without network)
- `pending_ghosts`: was PASS (0) right after the build; **now shows 1** (a pipe near
  (20.5, 84.5) out of roboport range) — this appeared during LIVE MANUAL PLAYER EDITS made
  after the build finished (see "User's live manual interventions" below), not from the
  automated build itself. Worth a quick look but not urgent.
- `fluid_machines`: **FAIL, but down from 6 violations to 4** after fixing oil seeding (see
  below). Remaining 4: chemical-plant at (91.5,111.5) and (95.5,111.5) (input box 1 empty),
  assembling-machine-2 at (221.5,223.5) and (224.5,223.5) (input box 1 empty). Every violation
  closer to the oil source (both oil-refineries) resolved once oil started flowing and some
  settle time passed. These four are further downstream (petroleum -> chemical plant; the very
  last processing-unit-stage assembler) and were still catching up when the session ended — my
  strong suspicion is these ALSO just need more settle time (the full pipeline is 400+ tiles
  long), not a structural defect, but this is NOT independently confirmed. **First thing to try:
  wait another 5-10 minutes of game time
  (`python -c "from tools.electronics_execution import wait_for_game_ticks; ..."` — see "The
  build command" section for the GameBridge construction pattern) and re-run
  `python -m tools.verify_factory_invariants --rcon-host 127.0.0.1 --rcon-port 27016
  --rcon-password planner_test`.** If still failing after a genuinely long wait, investigate
  the specific machine's missing ingredient (check `entity.status` and trace the ingredient back
  to its producer) the same way the oil issue below was diagnosed.
- `idempotency`: UNKNOWN (needs `--baseline`/`--compare`, not run this session — worth doing
  once fluid_machines passes, since idempotency was one of the original M6 acceptance criteria).

**This is by far the furthest this project has ever gotten.** The two structural fragmentation
bugs that blocked every previous session (pole auto-wire, missing oil seed) are both fixed and
live-verified. What's left is finishing verification of the last few fluid machines and the
ghost pipe, not more architecture work.

## How to run

- Factorio 2.0.77 at `E:\Games\Factorio`. **`factorio.exe` has "Run as administrator" forced in
  its Windows compatibility settings** — every launch AND every kill of it needs an elevated
  PowerShell; Claude cannot self-elevate and the user approving in chat does not bypass Windows
  UAC. Ask the user to run the launch command themselves, or ask them to uncheck "Run this
  program as an administrator" on `E:\Games\Factorio\bin\x64\factorio.exe` (Properties >
  Compatibility) to remove this friction permanently.
- This session's headless server data dir (NOT the original one from prior sessions, which is
  still running as a stray, unkillable PID and can be ignored/left alone):
  `C:\Users\djsma\AppData\Local\Temp\claude\C--Users-djsma-Downloads-Github-Desktop-Factorio-Cursor-RL-Agent\99f096c5-7d61-4a3f-8ddf-44da63ffdd89\scratchpad\fdata`
  (referred to below as `$fd`). Contains a copy of `1M_test.zip` with the surveyed world matching
  `tests/fixtures/electronics_world_spec.json`.
- Launch command (paste into an elevated PowerShell):
  ```powershell
  $fd = "C:\Users\djsma\AppData\Local\Temp\claude\C--Users-djsma-Downloads-Github-Desktop-Factorio-Cursor-RL-Agent\99f096c5-7d61-4a3f-8ddf-44da63ffdd89\scratchpad\fdata"
  $exe = "E:\Games\Factorio\bin\x64\factorio.exe"
  $argList = @("--config", "$fd\config.ini", "--mod-directory", "$fd\mods", "--start-server", "$fd\1M_test.zip", "--server-settings", "$fd\server-settings.json", "--port", "34198", "--rcon-port", "27016", "--rcon-password", "planner_test")
  Start-Process -FilePath $exe -ArgumentList $argList -RedirectStandardOutput "$fd\launch_out.log" -RedirectStandardError "$fd\launch_err.log"
  ```
  Uses port 34198 / RCON 27016 (NOT the game defaults 34197/27015) to avoid colliding with the
  stray old-session server. Redeploy the mod into `$fd\mods\factorio_cursor_rl_agent` (copy every
  `*.lua` + `info.json` from `factorio_mod/`) before launching if any Lua file changed since the
  last launch — mods only load at server start.
- RCON: `MSYS_NO_PATHCONV=1 python tools/rcon_client.py --host 127.0.0.1 --port 27016 --password planner_test "/sc <lua>"`.
- Verify server is up: `/sc rcon.print(game.tick)`.
- Verify sandbox state before building: check ghost/pole/roboport counts are 0, or plan to pass
  `--existing-topology reset`.

## The build command

```bash
cd "C:\Users\djsma\Downloads\Github_Desktop\Factorio_Cursor_RL_Agent"
python tools/build_processing_units.py \
  --world-spec "tests/fixtures/electronics_world_spec.json" \
  --script-output "<fd>\script-output" \
  --rcon-host 127.0.0.1 --rcon-port 27016 --rcon-password planner_test \
  --construction-mode radial \
  --spidertron-bots 100 \
  --existing-topology reset
```

This is a **long-running, live, multi-minute-to-multi-hour** command (6 rings, ~4300 production
ghosts). Always run it with output redirected to a file and in the background (Bash tool
`run_in_background: true`), then tail that file with a persistent Monitor filtering for
`ring|mobile hub|infrastructure|scaffold|settl|verify|live:|RESULT|EXECUTION FAILED|Traceback|Error|STALLED|stall`.
Do not run it synchronously in the foreground — it will exceed any reasonable tool timeout.

If it fails with `planner-sandbox contains incompatible factory topology`, that just means stray
entities exist (commonly leftover construction-robots from an earlier spidertron test) — rerun
with `--existing-topology reset`.

## What was fixed this session (in commit order)

1. **Tech sync** (earlier commit `95a15ef`, already landed): the `planner` force didn't inherit
   the player force's research/bot-speed modifiers on creation, so bots ran ~10x too slow. Fixed
   in `factorio_mod/sandbox_shared.lua`.
2. **Ore seeding** (also `95a15ef`): `/seed_ore_patches` mod command + `tools/electronics_execution.py::_seed_ore`,
   called before any construction. Live-verified this session: seeds correctly (e.g. one run
   seeded 2137 tiles: 748 iron, 1298 copper, 91 coal).
3. **Radial production construction** (`fdf654a`, this session): `tools/electronics_radial_execution.py`
   drives the REAL composed bundle (not a synthetic test) through center-out ring construction.
   Infrastructure (power spine + roboports, ~550 entities) stays one atomic `place_entity` call
   as before -- it was never gradually built and never needed to be. Only the ~4300 PRODUCTION
   ghosts are partitioned into concentric rings around `CANONICAL_ROBOPORT_HUB = (-128, -128)`
   and built ring-by-ring via a self-powered construction spidertron (fusion reactor + 4x
   personal-roboport-mk2 + battery, carries its own materials, ~40-tile construction radius).
   A fail-fast check (`assert_infrastructure_networks`) runs immediately after infra placement
   and refuses to proceed to the (slow) production build if infra isn't already exactly 1
   electric + 1 roboport network -- this is what caught fix #4 below instead of wasting hours.
4. **THE ACTUAL ROOT CAUSE of months of "fragmented network" symptoms, finally found and fixed**:
   Factorio's `LuaSurface.create_entity` auto-wires nearby electric poles UNRELIABLY once dozens
   of poles already exist on the surface. Empirically verified live via RCON (see the session
   transcript if you need the exact repro): two poles 5-18 tiles apart always auto-connect in
   isolation, but the identical distance silently fails to connect once ~60+ poles already exist
   nearby. This is why every previous live build showed multiple disconnected electric networks
   despite the planner's geometry being provably correct (`planners/preflight.py`'s power-reach
   BFS always passed `ok=True` on these exact bundles -- the PLANNING was never wrong, only the
   EXECUTION). Fixed in `factorio_mod/layout_executor.lua`: every `place_entity`/`place_ghost`
   call now ends with `ensure_pole_wiring()`, an explicit O(n^2) pass (trivial at ~500-node scale)
   that wires every pole pair within `min(get_max_wire_distance(a), get_max_wire_distance(b))`
   using the modern `LuaEntity.get_wire_connector(defines.wire_connector_id.pole_copper, true):connect_to(...)`
   API (NOT the old `connect_neighbour`, which no longer exists on this entity type in Factorio
   2.0.77). Live-verified prototype reach values match `planners/infrastructure.py`'s `POLE_SPECS`
   exactly: substation=18, big-electric-pole=32 (not the commonly-quoted 30), medium=9, small=7.5.
   **Live result after this fix: infra converged to exactly 1 electric network, 1 roboport
   network** on the real 553-entity bundle. This is the single most important fix from this
   session.
5. **Substation-primary spine** (user-requested design change, `fdf654a`): the user looked at a
   screenshot of the (still-fragmented, pre-fix-#4) power grid and asked to make the spine
   primarily use substations (much bigger 18x18 supply area vs a big pole's tiny 4x4) rather than
   big-electric-poles, reserving big poles for legs over ~100 tiles where their longer 32-tile
   reach needs fewer hops than an 18-tile substation chain would. Implemented in
   `planners/infrastructure.py::plan_power_network` (new `relay_pole`, `relay_spacing`,
   `long_leg_threshold` params, defaults substation/16/100). This is orthogonal to fix #4 --
   fix #4 makes ANY pole mix wire correctly; this changes WHICH poles get used. On the real
   bundle it produced 386 big-electric-pole + 56 substation in the spine (the block spans
   ~450x450 tiles, so most hub-to-site legs genuinely exceed the 100-tile threshold) plus the 65
   substations each site already had -- if the user wants a more substation-heavy visual result,
   raising `long_leg_threshold` or `relay_spacing` are the knobs to discuss with them, not
   something to silently retune.
6. **Cascading routing fragility exposed by #5** (`fdf654a`): the denser substation spacing
   shifted spine node positions enough to collide with a hardcoded, non-adaptive item-route path
   (`cable_to_ec`) in `planners/electronics_block.py::_item_routes`. Item routes there are FIXED
   straight-line waypoints with no rerouting -- they just hard-fail on collision. Root-caused and
   fixed by (a) no longer treating the (relocatable) preview infrastructure as an obstacle for
   item routing, and (b) making the geometry-covering recompose pass in `_cover_emitted_geometry`
   always run (previously it short-circuited when no extra roboports were needed), so spine poles
   always get a chance to dodge the FINAL route tiles via the pre-existing
   `_resolve_spine_pole_overlaps` mechanism. This is a real but narrow fix; fluid routing
   (`_fluid_routes`) uses a different, seemingly more adaptive path-generation function
   (`generate_fluid_chain_link`) and was not observed to have the same fragility, so it was left
   untouched (verify if you touch spine geometry again).
7. Fixed one test (`tests/test_infrastructure.py::test_power_validator_rejects_a_disconnected_site`)
   that assumed "substation" was a unique entity name in a power plan, which stopped being true
   once the spine itself can emit substations. Not a functional bug, just test fragility.

## Fix #8: oil/crude-oil was never seeded (found and fixed live this session, AFTER commit `fdf654a`)

After the build above completed with 0 ghosts and 1+1 networks, `verify_factory_invariants.py`
still failed `fluid_machines` on 6 machines (both oil-refineries, both chemical-plants, and the
final processing-unit assembler). Diagnosed live: `pumpjack.status` was `no_minable_resources`
(decode any numeric status via `for k,v in pairs(defines.entity_status) do if v==N then
rcon.print(k) end end` over RCON) — **crude oil was never seeded**, only solid ore
(iron/copper/coal). `tools/electronics_execution.py::ore_seeding_payload` only ever read
`world.ore_patches`; it never touched `world.pumpjack_sites` (which already has everything
needed: `{"position": [20.5, -43.5], "resource": "crude-oil", ...}`).

Fixed (no Lua change, no redeploy needed — `/seed_ore_patches` is already entity-agnostic) by
extending `ore_seeding_payload` to also emit a small rectangle (`PUMPJACK_FOOTPRINT/2 + 1` tile
margin around each pumpjack site's position) with `item=site["resource"]`. Re-seeding this
against the ALREADY-BUILT live factory (no rebuild) immediately fixed both oil-refineries (both
now hold `crude-oil: 200` and status `working`) within a couple minutes of settle time. Updated
`tests/test_ore_seeding.py`'s two affected tests (they previously assumed the payload only ever
contained the WorldSpec's declared `ore_patches`, which stopped being true).

**This fix is NOT yet committed as of this doc being written — check `git status` / `git log`.**
If uncommitted, the diff is small (touches `tools/electronics_execution.py` and
`tests/test_ore_seeding.py` only) and the fix is proven live; commit it once you've confirmed
tests pass.

## Fix #9 (NOT DONE, next concrete task): no water source for offshore pumps

Found live right at session end: the offshore pump (`world.offshore_pump_sites[0] =
{"position": [20.5, 83.5], "output": [20, 80], "resource": "water", "capacity_per_second":
1200.0}`, from the WorldSpec fixture) has NO water tile beneath it and was never connected to
anything (the user removed it after finding it broken -- this is what produced the stray
`pending_ghosts` pipe near (20.5, 84.5) mentioned above; it should resolve once the pump is
rebuilt with an actual water source under it).

**This is a different class of fix than oil/ore seeding.** Ore and crude-oil are RESOURCE
ENTITIES (`surface.create_entity{name=item, position=..., amount=...}`, which is what
`/seed_ore_patches` already does generically). Water is TERRAIN, not an entity -- an offshore
pump needs to be adjacent to an actual water TILE, set via `LuaSurface.set_tiles({{name=...,
position=...}, ...})` with a water-family tile name (Nauvis-style: likely `"water"` and/or
`"deepwater"` -- confirm the exact valid tile names live via
`prototypes.tile` before assuming). The user's explicit direction: for now, replicate how Nauvis
looks (a lake), not Vulcanus-style steam generation or any other planet's water mechanic --
this is a deliberate, narrow scope decision, not a placeholder to generalize past.

**Concrete next step:** add a new mod command (e.g. `/seed_water_lake`) in
`factorio_mod/scaffolding.lua` (or a new small Lua module, mind the 500 LOC limit -- current
`scaffolding.lua` length should be checked before adding to it) that calls `set_tiles` to lay
down a small lake (a rectangle or simple blob shape, tile-name confirmed live first) covering the
tile(s) adjacent to `world.offshore_pump_sites[i]["position"]`, matching the same idempotent
"check before creating" pattern `seed_ore_patches` already uses. Wire a matching Python payload
builder into `tools/electronics_execution.py` (parallel to `ore_seeding_payload`, or extend it)
and a `GameBridge` method (parallel to `seed_ore_patches`). This needs a mod redeploy + server
restart to test (Lua change), unlike the oil-seeding fix which didn't.

## User's live manual interventions this session (informational, not bugs to fix in code)

The user was watching the build live in their own Factorio client and made a few manual fixes
directly in-game while I was working on the code-level fixes in parallel:
1. Fixed a belt that had a turn immediately after an underground-belt exit, disconnected from
   the rest of the line. **This IS worth investigating as a planner bug** — see task #6 in this
   session's task list ("Investigate belt-turn-after-underground defect"). Likely lives in
   `planners/item_routing.py`'s tunnel/underground-belt exit geometry (a turn placed right after
   a tunnel exit may not account for the exit's forced direction). No specific coordinates were
   captured for this one; if it recurs, get the exact position from the user or from a
   `belt_to_ground_type` mismatch in a live layout report.
2. The pumpjack "couldn't drill oil" (this was the seeding bug above, diagnosed independently)
   and the user also "restarted it and connected it to the pipe, it wasn't connected properly
   either" -- **the pumpjack's force is now `player`, not `planner`** (confirmed live:
   `s.find_entities_filtered{name='pumpjack', force=game.forces.planner}` returns empty, but
   `find_entities_filtered{name='pumpjack'}` finds it with `force=player`). This is a deviation
   from the project's force-isolation invariant, introduced by manual play rather than by any
   planner/execution code. It does not appear to block fluid flow (pipes connect across forces
   fine, only logistics/electric networks are force-scoped), but it means this one entity is now
   outside the `planner`-force-scoped topology reports and invariant checks. If you need a fully
   clean `planner`-force factory again, the pumpjack will need to be rebuilt via the normal
   automated path rather than left as this manually-placed one.

## Open mystery: the headless server keeps dying mid-build

This happened **three times** this session, always during a long-running live build (never at
idle): the server process vanishes with no crash trace in `factorio-current.log` (it just stops
mid-line, no shutdown/exception logged), and the Python side sees `ConnectionRefusedError
[WinError 10061]` (server gone) or `[WinError 10054]` (connection forcibly closed, server dying
mid-request). The user ruled out sleep/power-management as the cause this session ("that's not
happening, this system is up for the next 2 hours") after I found the machine's power plan slept
after 20 min on battery / 40 min on AC (`powercfg /query SCHEME_CURRENT SUB_SLEEP STANDBYIDLE`) --
so if it recurs, that specific theory is likely wrong and something else is killing the process
(antivirus/EDR flagging a long-lived server binding ports from a temp-path working directory is
the next most likely theory, followed by disk space on the temp drive during a large autosave
write). **If this recurs, capture `Get-WinEvent -LogName Application -MaxEvents 20` and Windows
Defender's protection history immediately after, before that evidence rotates out.** Each time
so far the fix has just been "relaunch and retry" and the build has gotten further each time, so
it is not obviously deterministic/reproducible at a fixed point.

## Architecture map (unchanged from prior sessions, still accurate)

- `planners/`: local_layout_planner, line_layouts, chain_layouts, fluid_layouts, fluid_routing,
  infrastructure, infrastructure_geometry, roboport_coverage, resource_layouts, electronics_block,
  electronics_world, electronics_contracts, item_routing, plan_validation, preflight,
  recipe_data, resource_survey, world_generation, sandbox_infrastructure.
- `tools/`: build_processing_units (CLI, both construction modes), electronics_execution (atomic
  path + shared helpers), electronics_radial_execution (radial path, new), spidertron_build
  (self-test CLI + RCON drive loop), spidertron_geometry (pure geometry, new),
  survey_electronics_world, verify_factory_invariants, rcon_client.
- `orchestrator/game_bridge.py`: RCON-out / script-output-in transport. `build_layout`,
  `seed_ore_patches`, `ensure_scaffolding`, `verify_electronics_execution`,
  `inspect_sandbox_topology`, `reconcile_sandbox_topology`, `save_game`.
- `factorio_mod/`: 15 Lua modules, `layout_executor.lua` now owns pole auto-wiring correctness.

## Verified live game facts (additions this session, in addition to prior sessions' notes)

- Pole auto-wire-on-creation is UNRELIABLE at ~60+ pole scale; always explicitly wire via
  `LuaEntity.get_wire_connector(defines.wire_connector_id.pole_copper, true):connect_to(other_connector, false, defines.wire_origin.script)`.
  `connect_neighbour` does not exist on `LuaEntity` for poles in Factorio 2.0.77 -- must use the
  wire-connector API.
- Real prototype wire reach (`prototypes.entity[name].get_max_wire_distance()`), confirmed live:
  substation=18, big-electric-pole=32, medium-electric-pole=9, small-electric-pole=7.5. These
  exactly match `planners/infrastructure.py::POLE_SPECS` -- the planner was never the problem.
- `type='electric-pole'` correctly covers both regular poles AND substations for querying/BFS
  purposes (substation's prototype type is `electric-pole`).

## Durable lessons (reinforced again this session)

- Preflight (`planners/preflight.py`) passing is NOT proof of live correctness. It passed
  `ok=True` on every fragmented bundle this session, every single time, because the planner's
  GEOMETRY was always correct -- the gap was in Factorio's own execution behavior (unreliable
  auto-wire), which no amount of offline geometry checking could ever catch. Only running the
  actual game against actual entity counts ever found this.
- When changing shared planning primitives (like spine pole types/spacing), re-run the FULL
  bundle through `preflight()` and the full test suite before assuming a change is safe -- this
  session's substation-spine change broke a completely unrelated hardcoded item route two
  modules away, which nothing but an end-to-end offline build (`build_electronics_block(...)`)
  would have surfaced.
