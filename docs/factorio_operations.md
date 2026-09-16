<!-- Path: docs/factorio_operations.md | Purpose: Concise runbook for choosing the correct Factorio runtime and proving live changes. -->

# Factorio operations

## Choose the runtime first

Current paths:

- Real base: `tools/autonomous_run.py -> orchestrator/autonomous_builder.py -> orchestrator/stage_* -> planners/*`, using explicit `surface=nauvis`, `force=player`.
- Training: `training/`, `factorio_training_lab/`, isolated workers, disposable surfaces, and immutable episode evidence.
- Retired reference: `experimental/legacy_autonomy/`; inspect or reuse deliberately, never invoke it as an active runtime by accident.

| Work | Runtime |
|---|---|
| Real factory mission or diagnosis | Deterministic server, explicit Nauvis/player |
| Disposable episode or policy evaluation | Configured training worker and `training/*` surface |
| Offline contract, planner, or unit test | No Factorio process |

Never infer the server or port from a default. Confirm the process, listening game/RCON ports, save, loaded mod version, surface, force, and server-owned `script-output` path.

## Interface choice

- Use `tools/rcon_client.py` for direct commands and connectivity checks.
- Use `GameBridge` only for commands that write a report file; wait for a new parseable file in that server's `script-output` directory.
- Use `tools/autonomous_run.py` for the requested real-base mission.
- Use `tools/run_training_batch.py` or the native Linux worker manager for training episodes.

Raw console Lua and build commands can mutate state. Start diagnosis with read-only exports.

## Failure workflow

1. Start with the bounded [run context](deterministic/run_context.md), then inspect cited raw evidence to identify the first hard failure, not the last repeated symptom.
2. Verify the runner is connected to the intended server, port, save, surface, and force.
3. Verify the running mod copy matches the repository copy when Lua behavior appears stale.
4. Trace observation -> planner/policy decision -> submitted action -> Factorio result.
5. Fix the responsible code. Do not manually place entities to rescue the current base.
6. Run focused validation, perform the required lifecycle action, and rerun from the requested save or fresh episode.

Stop repeated retries after the first clear operational failure. Repetition without new evidence is not learning.

## Deployment boundary

| Changed files | Required action |
|---|---|
| Python/controller only | Restart the affected runner/controller |
| `factorio_mod/*.lua` or `factorio_training_lab/*.lua` | Synchronize both project mods to the GUI and each affected server, then restart the affected Factorio runtime and its Python consumer |
| Documentation/tests only | No runtime restart |

Repository, dedicated-server, GUI-client, and WSL mod copies are distinct. Compare hashes or timestamps when a client reports mismatched mods or the runtime behaves like old code.

### Native Linux training workers

`scripts/manage_linux_training_worker.sh` creates only disposable worker state
below `~/.local/share/factorio-rl/training/<index>/worker`. It uses a separate
Factorio `write-data` root plus the private headless runtime at
`~/.local/share/factorio-rl/runtime/factorio-2.1.17`, local RCON port,
worker-local `script-output`, save, mods, logs, and mode-600 RCON-secret file.
Its default ports are game
`35000 + index` and RCON `28000 + index`; RCON remains loopback-only.

### Native Linux deterministic server

`scripts/manage_linux_deterministic_server.sh` owns the isolated deterministic
root at `~/.local/share/factorio-rl/deterministic`, using the private
`factorio-2.1.17` runtime.  Its `bootstrap` action requires an explicit source
save and copies it once to the isolated root; it never writes to
`~/.factorio/saves`.  The default game/RCON endpoints are loopback-only
`34199/27017`, and the RCON secret is a mode-600 file under the server root.

Every `start` also snapshots the selected save into the tracked repository
artifact `saves/mod_playground.zip`. When `--source-save` is supplied, that
source is copied; otherwise the current isolated save is copied. This keeps
the latest reproducible base save available to collaborators without changing
the normal Factorio profile.

Run `deploy-if-required` only while stopped, then `start`. It compares and, when changed, copies both project mods
(`factorio_cursor_rl_agent` and `factorio_training_lab`) into the isolated
server and configured Linux GUI mods directory (default `~/.factorio/mods`),
enabling both in the GUI `mod-list.json` without disturbing other entries. Lua
changes require this deployment and a deterministic Factorio restart. Restart
the GUI client before joining this server. Loading the training-lab mod does
not create a training surface or alter Nauvis; training remains isolated by its
separate worker roots, saves, ports, and explicit commands.

### Native deterministic control center

The control center is `tools/dashboard_server.py`; it is a Python server and
does not need Node.js or npm. It binds only to `127.0.0.1:9137`. Start it with
the isolated server root and all native managers:

```bash
uv run --with-requirements requirements.txt python tools/dashboard_server.py \
  --port 9137 \
  --server-data ~/.local/share/factorio-rl/deterministic \
  --source-save ~/.factorio/saves/mod_playground.zip \
  --rcon-secret-file ~/.local/share/factorio-rl/deterministic/rcon-password \
  --server-manager scripts/manage_linux_deterministic_server.sh \
  --runner-manager scripts/manage_linux_deterministic_runner.sh \
  --campaign-manager scripts/manage_linux_deterministic_campaign.sh \
  --runtime-root ~/.local/share/factorio-rl/runtime/factorio-2.1.17 \
  --gui-mods ~/.factorio/mods
```

When those managers are configured, every dashboard lifecycle control invokes
a bounded Linux command—never PowerShell:

| Dashboard control | Native command sequence |
|---|---|
| Restart controller on current world | `manage_linux_deterministic_runner.sh restart` (preserves the world) |
| Stop Python runner | `manage_linux_deterministic_runner.sh stop` |
| Stop Factorio server | runner manager `stop`, then server manager `stop` |
| Stop operations console | Gracefully shut down `tools/dashboard_server.py`; Factorio and the runner are unchanged |
| Redeploy mod | `manage_linux_deterministic_server.sh deploy` (both project mods to server plus GUI copy) |
| Restart server | `manage_linux_deterministic_server.sh stop`, then `start` |
| Start new 12h loop | User systemd supervisor; four aspect observers, primary fixer, independent review/tests and scoped commits; see [reliability workflow](deterministic/reliability_workflow.md) |
| Resume remaining time | Resume the recorded window, episode and pending fix with its original deadline |
| Stop loop | Stop owned controller/model processes; separately managed runner/server remain unchanged |
| Start fresh campaign | campaign manager `fresh`: runner stop, server stop, changed-mod deployment, verified reset, server start, manifest-gated controller start |

The live-output toolbar's **Copy last run** control copies the newest complete
runner session, including its `RUN START` and `RUN END` lines. It reads the
runner log directly rather than copying only the currently rendered log window.

The dashboard's **Research control** writes an ordered queue to
`<server-data>/logs/research-queue.json`.  **Set target** replaces that queue;
**Queue after current** appends unique technologies while preserving completed
and failed item state.  Both actions stop the current Linux runner and start it
in `research-queue` mode.  Each item reuses the existing research workflow:
preflight the technology, build or repair its science-pack production, then
call the mod's `/set_research` command.  Invalid, disabled, already-completed,
or prerequisite-ineligible targets fail closed and remain visible in the queue.

The technology picker is populated from the live force through the mod's
`/research_options` report. Locked technologies are not selectable. The next
repeatable level is selectable immediately, while a later level is exposed
only when each preceding level is already running or queued. For example,
`mining-productivity-5` can be appended while `mining-productivity-4` is
running, but cannot replace that active target by itself. This validation is
performed again against live `/research_status` before the queue file is
written.

This is an explicit queue, not autonomous technology-tree search.  The
deterministic runtime still does not maintain one authoritative production
manifest containing every item, measured rate, capacity, target, and expansion
location.  It reconstructs live requirements from Factorio reports, recipe
catalogs, snapshots, and planner state on each run; `autonomous-priorities.json`
is an operational priority snapshot, while the research queue is durable user
intent.  A future production-memory slice should add measured rate windows,
capacity targets, and owned production corridors before enabling automatic
rate-increase decisions.

A fresh episode writes an immutable-source manifest containing the source and
isolated SHA-256 hashes, baseline fingerprint, repository revision, dirty-file
count, episode ID, target, and lifecycle timestamps. The managed runner refuses
to start when either save hash or the repository revision differs from that
manifest. Reset backs up and replaces only the isolated copied save; it never
changes `~/.factorio/saves`. GUI deployment does not close a running GUI
client, so restart that client yourself before it joins after a Lua change.

For an explicitly authorized fresh deterministic campaign,
`scripts/manage_linux_deterministic_campaign.sh fresh` performs the atomic
lifecycle in one bounded command. `cycle` is a compatibility alias. Use
`--dry-run` to inspect the exact resolved sequence. Never use a controller
restart as a substitute for a fresh episode.

### Native deterministic checkpoint fleet

The checkpoint fleet is owned by one persistent user-systemd coordinator. Its
state and generated bundles live below
`~/.local/share/factorio-rl/deterministic/checkpoint-fleet`; it never writes a
lane into the repository save or GUI profile. Initialize the local catalog
from the tracked C0 input once:

```bash
scripts/manage_linux_deterministic_fleet.sh init \\
  --source-save "$PWD/saves/mod_playground.zip"
```

This copies the source into an immutable local C0 bundle and registers later
milestone definitions without claiming that they are runnable. The source
save is read-only. Install and start the persistent watcher with:

```bash
scripts/manage_linux_deterministic_fleet.sh install
scripts/manage_linux_deterministic_fleet.sh start
scripts/manage_linux_deterministic_fleet.sh status
```

The service runs `tools/deterministic_fleet_coordinator.py watch --execute`.
By default it gives frontier helpers the loopback Operations Console endpoint
`http://127.0.0.1:9137`; override that with `--helper-api-url` when the console
uses another local port.
Direct `status` and `run-once` calls are read-only by default; only the
service unit supplies `--execute`. The coordinator watches immutable `HEAD`
objects even when the operator checkout contains unrelated dirty work. Only
descendant commits enqueue an automatic default suite; rewinds and divergent
histories do not. The dashboard and coordinator share
`checkpoint-fleet.json`, its lock, and `coordinator-heartbeat.json`, so queue
mutations cannot be lost to a second facade schema.

### Mod-copy synchronization and GUI restart

Factorio does not hot-reload Lua scripts. Any changed mod script must be
synchronized to every runtime that will load it before joining or starting a
mission:

- Both `factorio_training_lab/` and `factorio_mod/` -> every participating
  native training worker, the deterministic server, and the matching GUI
  profile. The Linux managers use `scripts/sync_linux_gui_mods.sh` for the GUI
  copy while preserving unrelated GUI mods.

After synchronizing a script change, restart the affected Factorio server and
restart the GUI Factorio session before joining. A Python runner restart alone
is insufficient for Lua changes. The handoff must state which copies were
updated, which hashes were checked, and that the GUI restart is still required
or has been completed.

## Real-base rules

- Use `surface=nauvis`, `force=player` unless the user explicitly requests another target.
- Existing infrastructure is authoritative: go around it or fail safely.
- A machine count is not readiness. Check power, logistics, input delivery, throughput, working state, stock, and upstream supply.
- Prefer direct continuous transport; buffers require a measured reason.
- Preserve fluid purity and explicit separation.
- A live mission request means launch and observe the mission, not merely plan it or run tests.

## Training observation

Training multiplayer viewing is observational. The Observatory's **View in Factorio** control targets only the active owned training surface and spectator. Client and server training-mod scripts must match exactly before joining.

## Reporting

For live operations, report:

```text
command -> important output -> conclusion
```

Then state whether the user must restart a runner, redeploy a Lua mod, restart a Factorio runtime, or do nothing.
