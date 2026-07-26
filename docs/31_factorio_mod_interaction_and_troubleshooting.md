# Path: docs/31_factorio_mod_interaction_and_troubleshooting.md
# Purpose: Authoritative runbook for safe Factorio mod interaction and troubleshooting.

# Factorio Mod Interaction and Troubleshooting

This is the authoritative runbook for interacting with this repository's Factorio mod.
Begin read-only, identify the exact server and data directory, and obtain explicit user
authorization before any live mutation, deployment, save/reset, or lifecycle action.

## Architecture

```text
operator/Python --console command over RCON--> Factorio server
operator/Python <--JSON from script-output---- Factorio mod
```

- The mod exposes 19 console commands through `commands.add_command`.
- It has no `remote.add_interface`; there is no Python-callable Factorio remote interface.
- `tools/rcon_client.py` sends raw console commands.
- `orchestrator.game_bridge.GameBridge` sends commands over RCON, then waits for a new,
  parseable JSON file in the server's own `script-output`.
- `tools/autonomous_run.py` is the high-level real-base production/research workflow.

Raw `/sc` is arbitrary Lua and can mutate the world. Review every character before
sending it. Unprotected Lua errors have historically killed the dedicated server in this
development setup; keep diagnostics small, wrap uncertain access with `pcall`, and print
compact output with `rcon.print`.

## Choose the Narrowest Interface

| Need | Use | State risk |
| --- | --- | --- |
| Connectivity, `/help`, compact query | `tools/rcon_client.py` | Depends on command |
| Supported export/report | `GameBridge` | Depends on method |
| Real-base item or research goal | `tools/autonomous_run.py` | State-changing |
| Inspect an existing JSON artifact | Offline validator/tool | None to server |

Do not use autonomous execution for diagnosis. Do not use `/sc` when a registered
read-only export already answers the question.

## Preflight

1. Confirm the intended server, RCON host/port, and allowed action.
2. Identify that server process's own `script-output` directory.
3. Probe with `/sc rcon.print(game.tick)`.
4. Check registration with `/help <command>`.
5. For a real base, explicitly use surface `nauvis` and force `player`.
6. Record repo and deployed-mod revisions before diagnosing drift.

There is a current default mismatch: `rcon_client.py` and `GameBridge` default to port
`27015`; `autonomous_run.py` defaults to `27017`. Always pass the port explicitly.
Examples use the overridable local development values `127.0.0.1:27017` and
`planner_test`; they are not universal credentials.

### PowerShell

```powershell
Set-Location "C:\Users\djsma\Downloads\Github_Desktop\Factorio_Cursor_RL_Agent"
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/sc rcon.print(game.tick)"
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/help snapshot"
```

PowerShell uses `$env:NAME=...`, `Start-Sleep`, and `Select-Object -Last`; it does not
interpret Bash-style `NAME=value command`, GNU `timeout`/`tail`, or shell syntax the
same way.

### Git Bash

```bash
cd "/c/Users/djsma/Downloads/Github_Desktop/Factorio_Cursor_RL_Agent"
MSYS_NO_PATHCONV=1 timeout 20 python tools/rcon_client.py \
  --host 127.0.0.1 --port 27017 --password planner_test \
  "/sc rcon.print(game.tick)" 2>&1 | tail -3
```

`MSYS_NO_PATHCONV=1` prevents Git Bash from rewriting slash-prefixed Factorio commands.

## Locate and Validate `script-output`

`GameBridge` must receive the `script-output` directory belonging to the same server as
the RCON endpoint. A client-side `%APPDATA%\Factorio\script-output` is wrong when the
dedicated server uses a separate config/data directory.

```powershell
$serverData = "C:\path\to\server-data"
$scriptOutput = Join-Path $serverData "script-output"
Test-Path -LiteralPath $scriptOutput -PathType Container
Get-ChildItem -LiteralPath $scriptOutput -Force
```

```bash
script_output='C:\path\to\server-data\script-output'
test -d "$script_output" && find "$script_output" -maxdepth 2 -type d
```

Validate the pairing by listing existing files, issuing a read-only file-producing
command, and confirming a new parseable JSON file appears:

```python
from pathlib import Path
from orchestrator.game_bridge import GameBridge, load_json

bridge = GameBridge(
    Path(r"C:\path\to\server-data\script-output"),
    host="127.0.0.1", port=27017, password="planner_test",
)
try:
    path = bridge.request_snapshot(surface="nauvis")
    print(path, load_json(path)["tick"])
finally:
    bridge.close()
```

Do not delete old reports. `GameBridge` snapshots existing filenames before the command
and waits for a new parseable file.

## Read-only Diagnostic Cookbook

The Lua below reads state, but `/sc` remains an arbitrary-code interface.

### Registration, surfaces, and forces

```text
/help snapshot
/help export_recipe_catalog
/help research_status
/help inspect_sandbox_topology
/help verify_electronics_execution
/sc local s={};for n,_ in pairs(game.surfaces) do s[#s+1]=n end;table.sort(s);local f={};for n,_ in pairs(game.forces) do f[#f+1]=n end;table.sort(f);rcon.print("surfaces="..table.concat(s,",").." forces="..table.concat(f,","))
```

### Supported exports

```text
/snapshot nauvis
/export_recipe_catalog player
/research_status {"force":"player","technology":"automation"}
/inspect_sandbox_topology
/verify_electronics_execution
```

The last two inspect `planner-sandbox`; a missing sandbox is an expected useful failure
on a real-base-only server.

### Machine status

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local names={};for k,v in pairs(defines.entity_status) do names[v]=k end;local o={};for _,e in pairs(s.find_entities_filtered{force=f,name={"assembling-machine-2","electric-furnace","electric-mining-drill"}}) do local ok,r=pcall(function() return e.get_recipe() end);o[#o+1]=((ok and r) and r.name or e.name).."@("..e.position.x..","..e.position.y..")="..(names[e.status] or "?") end;table.sort(o);rcon.print(table.concat(o," | "))
```

### Logistic coverage and inventory

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local o={};for _,c in pairs(s.find_entities_filtered{force=f,type="logistic-container"}) do local n=c.logistic_network;o[#o+1]=c.name.."@("..c.position.x..","..c.position.y..")net="..(n and "YES" or "NONE") end;table.sort(o);rcon.print(table.concat(o," | "))
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local total=0;for _,c in pairs(s.find_entities_filtered{force=f,type={"container","logistic-container"}}) do local inv=c.get_inventory(defines.inventory.chest);if inv then total=total+inv.get_item_count("automation-science-pack") end end;rcon.print("automation-science-pack in chests="..total)
```

### Belt movement

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local n,active,total=0,0,0;for _,e in pairs(s.find_entities_filtered{force=f,type={"transport-belt","underground-belt"}}) do n=n+1;local c=0;for i=1,e.get_max_transport_line_index() do for _,item in pairs(e.get_transport_line(i).get_contents()) do c=c+(item.count or 0) end end;if c>0 then active=active+1;total=total+c end end;rcon.print("belts="..n.." with_items="..active.." items="..total)
```

### Electric networks and closest gap

Discover network IDs dynamically; never assume names such as `net2` or `net4` remain stable across saves:

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local nets={};local poles={};for _,e in pairs(s.find_entities_filtered{force=f,type="electric-pole"}) do local id=e.electric_network_id or 0;nets[id]=(nets[id] or 0)+1;poles[#poles+1]={e=e,id=id} end;local o={};for id,n in pairs(nets) do o[#o+1]="net="..id.." poles="..n end;table.sort(o);rcon.print(table.concat(o," | "));local best=nil;for i=1,#poles do for j=i+1,#poles do if poles[i].id~=poles[j].id then local a,b=poles[i].e,poles[j].e;local dx=a.position.x-b.position.x;local dy=a.position.y-b.position.y;local d=math.sqrt(dx*dx+dy*dy);if not best or d<best.d then best={d=d,a=a,b=b,ai=poles[i].id,bi=poles[j].id} end end end end;if best then rcon.print("closest_gap="..best.d.." net="..best.ai.." "..best.a.name.."@("..best.a.position.x..","..best.a.position.y..") <-> net="..best.bi.." "..best.b.name.."@("..best.b.position.x..","..best.b.position.y..")") else rcon.print("closest_gap=NONE") end
```

This reports topology only. Bridging a gap is a separate, state-changing planning decision requiring authorization.

### Independent invariant measurement

```powershell
python -m tools.verify_factory_invariants --rcon-host 127.0.0.1 --rcon-port 27017 --rcon-password planner_test --surface nauvis --json
```

Module execution works. Direct `python tools/verify_factory_invariants.py` currently
fails with `ModuleNotFoundError` because of its import path.

## Registered Commands

Classification describes effect, not authorization. Do not test state-changing commands
on a live server without explicit approval and valid payloads.

### Read-only/export (6)

| Command | Purpose | Output directory |
| --- | --- | --- |
| `/snapshot [surface]` | Export deterministic surface state | `factorio_mod/snapshots/` |
| `/export_recipe_catalog [force]` | Export all recipes, unlock state, and environmental inputs | `factorio_mod/recipe_catalogs/` |
| `/research_status [JSON]` | Export force/research state | `factorio_mod/research_reports/` |
| `/export_ghost_observation` | Export sandbox ghosts | `factorio_mod/ghost_observations/` |
| `/inspect_sandbox_topology` | Inspect sandbox topology | `factorio_mod/topology_reports/` |
| `/verify_electronics_execution` | Measure sandbox invariants | `factorio_mod/live_execution_reports/` |

### State-changing (13)

| Command | Mutation | Output directory |
| --- | --- | --- |
| `/create_planner_world <JSON>` | Create/explicitly reset planner world | `factorio_mod/world_reports/` |
| `/apply_ghost_plan <JSON>` | Render sandbox ghosts | Console/status only |
| `/execute_ghost_plan <JSON>` | Place authorized ghosts | `factorio_mod/execution_reports/` |
| `/execute_construction <JSON>` | Let bots construct authorized ghosts | `factorio_mod/construction_reports/` |
| `/execute_upgrade_plan <JSON>` | Apply authorized upgrades | `factorio_mod/execution_reports/` |
| `/execute_deconstruction_plan <JSON>` | Mark authorized deconstruction | `factorio_mod/execution_reports/` |
| `/reconcile_sandbox_topology <JSON>` | Reconcile/explicitly reset topology | `factorio_mod/topology_reports/` |
| `/seed_ore_patches <JSON>` | Seed resource entities | `factorio_mod/ore_seed_reports/` |
| `/ensure_sandbox_scaffolding <JSON>` | Provision sandbox infrastructure | `factorio_mod/scaffold_reports/` |
| `/seed_water_lakes <JSON>` | Seed bounded water terrain | `factorio_mod/water_seed_reports/` |
| `/build_layout_plan <JSON>` | Build authorized positioned layout | `factorio_mod/layout_reports/` |
| `/set_research <JSON>` | Queue force research | `factorio_mod/research_reports/` |
| `/spawn_construction_spidertron [JSON]` | Spawn/equip sandbox spidertron | `factorio_mod/spidertron_reports/` |

`GameBridge` constants cover snapshots, ghost observations, execution, construction,
scaffolding, ore/water seeding, layouts, live verification, research, topology, and
recipe catalogs. It has no current collector for `world_reports` or
`spidertron_reports`. `apply_ghost_plan` writes no report.

## Real-base Rule

Legacy sandbox paths often default to `planner-sandbox` and force `planner`. Real-base
workflows must explicitly pass surface `nauvis` and force `player`. Confirm those names
exist before mutation.

```powershell
python tools\autonomous_run.py produce automation-science-pack `
  --surface nauvis --force player `
  --rcon-host 127.0.0.1 --rcon-port 27017 --rcon-password planner_test `
  --script-output "C:\path\to\server-data\script-output" `
  --reference-point 3 -1 --max-iterations 20
```

This example is state-changing and requires explicit authorization.

## Offline Belt-bridge Troubleshooting

`planners.belt_bridge._route_points` is an internal diagnostic helper, not a stable public API. At a corner, the corner belt must face the **outgoing** leg; the preceding tile continues to face the incoming leg.

```powershell
python -m pytest tests/test_belt_bridge.py -q
```

The current tests cover corner orientation and fail-closed `bridge_chest_to_chest` behavior: an obstacle run beyond the selected underground tier's reach raises instead of emitting an invalid tunnel; a blocked tunnel endpoint raises because both sides must be free; and emitted underground pairs validate against the BuildPlan schema. Run this offline before a live retry. Do not delete entities or increase reach constants without prototype evidence.

## Parameterized Server-launch Anatomy

This is anatomy, not authorization to launch. Use durable operator-controlled paths, not ephemeral Claude/temp-session directories:

```powershell
$factorioExe = "E:\Games\Factorio\bin\x64\factorio.exe"
$serverData = "C:\path\to\durable-server-data"
$args = @(
  "--config", (Join-Path $serverData "config.ini"),
  "--mod-directory", (Join-Path $serverData "mods"),
  "--start-server", (Join-Path $serverData "save.zip"),
  "--server-settings", (Join-Path $serverData "server-settings.json"),
  "--port", "34199",
  "--rcon-port", "27017",
  "--rcon-password", "planner_test"
)
$stdout = Join-Path $serverData "factorio-stdout.log"
$stderr = Join-Path $serverData "factorio-stderr.log"
Start-Process -FilePath $factorioExe -ArgumentList $args `
  -RedirectStandardOutput $stdout -RedirectStandardError $stderr
```

Launching/restarting is a lifecycle mutation and may require UAC. Obtain explicit user authorization first; do not silently add `-Verb RunAs` or start a second server.

## Failure Matrix

| Symptom | Likely cause | Safe next check |
| --- | --- | --- |
| Auth traceback | Wrong password | Verify launch settings; do not brute-force |
| `WinError 10061` | Server down/wrong port | Confirm process and explicit port |
| Tick works; `/help` fails | Mod absent/disabled/stale/wrong server | Compare loaded mod and deployed revision |
| Little/no RCON response | File-only report or silent rejection | Inspect expected new report; use `GameBridge` |
| Invalid snapshot surface is silent | Handler returned without RCON error | Enumerate surfaces, retry valid read-only export |
| `script-output` missing | Wrong data directory | Reconcile path with server launch/config |
| Wait timeout | Wrong tree, paused server, stale mod, error, slow export | Check log/subdir/`auto_pause`; do not rerun mutations |
| JSON schema failure | Drift, stale/partial file, serialization bug | Check newest tick, revision, matching schema |
| Sandbox reported missing | No `planner-sandbox` | Expected for real-base-only state |
| Direct invariant script import error | Broken direct-script path | Use `python -m tools.verify_factory_invariants` |
| Failure references code absent now | Stale import/process/worktree | Record provenance; rerun only if authorized |

Bad credentials and closed ports currently produce uncaught tracebacks from
`rcon_client.py`; the final exception is the useful signal.

## Deployment and Restart Boundary

Repository Lua is not automatically loaded by a running server. Command registration and
Lua changes require the intended mod deployment and a server restart/save reload.

Do not copy/redeploy a mod, replace a mod folder, run `/server-save`, `/quit`, reset or
reconcile state, or restart/launch a server without explicit user authorization. When
authorized, verify hashes and launch arguments, preserve the save, then repeat tick and
`/help` preflight after restart.

## Safe Escalation

1. Stop after the first clear failure; avoid repeated state-changing retries.
2. Capture the exact command with secrets redacted, endpoint, data path, code/deployed
   revisions, report path/tick, and final error.
3. Classify transport, registration, output-path, schema, live-state, or Python failure.
4. Propose the smallest read-only next check.
5. Ask before deployment, save/reset, lifecycle, or autonomous actions.

## Verified Snapshot: 2026-07-27

These are observations from one local development server, not permanent truth:

- Tick and `/help` probes for the five primary read-only commands worked.
- `GameBridge` exported a schema-valid `nauvis` snapshot with 99,441 entities.
- The schema-valid `player` recipe catalog had 640 recipes and 14 raw resources.
- Automation research status exported successfully.
- Topology reported no `planner-sandbox`; electronics verification produced the
  schema-valid expected failure for that missing surface.
- Module-form invariant verification worked read-only on `nauvis`.
- A read-only pole diagnostic found 24 poles split across two networks (19 and 5), with a closest cross-network gap of 15.5 tiles.
- Targeted tests reported 48 passed and 3 skipped, plus a pytest cache permission warning.
- A captured autonomous log reported `bring_stage_up` undefined while current source
  defines it, pointing to stale process/import provenance rather than a settled code bug.
