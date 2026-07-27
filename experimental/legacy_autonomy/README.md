# Path: experimental/legacy_autonomy/README.md
# Purpose: Define the quarantine boundary and explicit invocation contract.

# Legacy Autonomy Quarantine

This package preserves the superseded sandbox autonomy generation without
presenting it as part of the active Nauvis runtime.

The active path is:

```text
tools/autonomous_run.py
  -> orchestrator/autonomous_builder.py
  -> orchestrator/stage_*.py
  -> planners/*.py
```

The modules here are experimental and unsupported. Active production modules
must not import them. They may be inspected for reusable deterministic ideas,
but revival requires an explicit architecture review against `AGENTS.md` and
`docs/20_system_invariants.md`.

Legacy entry points remain available only through explicit module invocation:

```powershell
python -m experimental.legacy_autonomy.rl_advisor <rl_observation.json> [seed]
python -m experimental.legacy_autonomy.run_cycle --help
python -m experimental.legacy_autonomy.loop_daemon --help
python -m experimental.legacy_autonomy.expansion_daemon --help
```

These commands are not part of the operations dashboard or autonomous runner.