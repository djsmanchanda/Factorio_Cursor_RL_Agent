---
name: factorio-server-rcon
description: Safely choose and execute raw RCON, GameBridge, or autonomous-run interactions with this repository's Factorio server. Use for connectivity probes, registered console commands, script-output collection, and explicitly authorized real-base workflows. Do NOT use for mod deployment, server lifecycle, save/reset actions, or live mutation without explicit user authorization.
---
# Path: .agents/skills/factorio-server-rcon/SKILL.md
# Purpose: Guide safe selection and execution of Factorio server interaction paths.

# Factorio Server RCON

Use the narrowest interface and prove server/output identity before acting.

## When to Activate

- Probe an RCON endpoint or verify authentication
- Inspect registered Factorio mod commands
- Send a compact read-only Lua diagnostic
- Collect a mod-generated JSON report
- Choose between raw RCON, GameBridge, and autonomous execution
- Diagnose an RCON response versus a `script-output` result
- Run an explicitly authorized real-base production or research workflow

## Interface Decision

| Goal | Use | Requirement |
| --- | --- | --- |
| Tick, `/help`, compact query | `tools/rcon_client.py` | Explicit host, port, password |
| File-producing supported command | `GameBridge` | Exact server-owned `script-output` |
| Real-base item/research goal | `tools/autonomous_run.py` | Explicit mutation authorization |
| Existing JSON inspection | Offline tool | No live connection |

Read [the authoritative runbook](../../../docs/31_factorio_mod_interaction_and_troubleshooting.md)
before constructing commands.

## Safe Pattern

1. Identify the intended server and its launch/config record.
2. Pass the port explicitly; raw RCON defaults to `27015`, autonomous run to `27017`.
3. Probe with `/sc rcon.print(game.tick)`.
4. Check `/help <command>`.
5. Pair file-producing commands with the same server's `script-output`.
6. For real-base reads, pass `nauvis` and `player`.
7. Stop and request authorization before mutation or lifecycle actions.

BAD:

```text
python tools/rcon_client.py --password planner_test "/sc game.surfaces.nauvis.clear()"
```

GOOD:

```powershell
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/sc rcon.print(game.tick)"
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/help snapshot"
```

Raw `/sc` can mutate even when labeled a diagnostic. Wrap uncertain Lua access with
`pcall`; historical unprotected errors have killed the development server.

## Before You Ship

- [ ] Endpoint and server instance are identified
- [ ] Port is explicit
- [ ] Command classification was checked in the runbook
- [ ] Real-base surface/force are explicitly `nauvis`/`player`
- [ ] `script-output` belongs to the same server
- [ ] Mutation and lifecycle actions have explicit authorization
- [ ] Exact result or failure is reported without claiming unverified success
