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

1. Read the complete latest log and identify the first hard failure, not the last repeated symptom.
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
| `factorio_mod/*.lua` | Deploy deterministic mod, restart deterministic Factorio runtime, restart runner |
| `factorio_training_lab/*.lua` | Deploy training mod, synchronize the GUI training-mod copy, restart the affected training worker and GUI Factorio session, then restart the batch/controller if needed |
| Documentation/tests only | No runtime restart |

Repository, dedicated-server, GUI-client, and WSL mod copies are distinct. Compare hashes or timestamps when a client reports mismatched mods or the runtime behaves like old code.

### Native Linux training workers

`scripts/manage_linux_training_worker.sh` creates only disposable worker state
below `~/.local/share/factorio-rl/training/<index>/worker`. It uses a separate
Factorio `write-data` root plus the private headless runtime at
`~/.local/share/factorio-rl/runtime/factorio-2.1.14`, local RCON port,
worker-local `script-output`, save, mods, logs, and mode-600 RCON-secret file.
Its default ports are game
`35000 + index` and RCON `28000 + index`; RCON remains loopback-only.

### Mod-copy synchronization and GUI restart

Factorio does not hot-reload Lua scripts. Any changed mod script must be
synchronized to every runtime that will load it before joining or starting a
mission:

- `factorio_training_lab/` -> the WSL training worker and
  `%APPDATA%\Factorio\mods\factorio_training_lab` for GUI observation.
- `factorio_mod/` -> the deterministic server copy and the matching GUI client
  copy when the client joins that server.

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
