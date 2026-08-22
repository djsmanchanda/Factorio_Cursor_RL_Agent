<!-- Path: tools/README.md -->
<!-- Purpose: Describe how to run local tooling for snapshot validation. -->

# Tools

`python -m tools.deterministic_run_journal` reconstructs up to the latest ten
deterministic autonomous runs from the live and archived logs, compares live
milestones with the previous run for the same target, and preserves brief
change/lesson notes for crash-safe campaign continuity.

## Live Factorio Interaction

Use the [authoritative interaction runbook](../docs/factorio_operations.md) for RCON preflight, shell recipes, `GameBridge`, `script-output`, registered commands, and failure diagnosis.

```powershell
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/sc rcon.print(game.tick)"
python -m tools.verify_factory_invariants --rcon-host 127.0.0.1 --rcon-port 27017 --rcon-password planner_test --surface nauvis --json
```

The direct invariant-script form currently has a broken import path. Live mutation and server lifecycle actions require explicit authorization.

## Isolated RL training

`generate_training_scenarios.py` exports immutable scenario contracts.
`run_training_batch.py` validates 100 scenarios offline by default; supplying an
explicit worker file runs disposable episodes only on loopback training
servers. `report_training.py` summarizes the resulting SQLite evidence and
local-model runtime measurements. `run_autoresearch.py` asks a loopback-only
OpenAI-compatible endpoint for one allowlisted numeric experiment after a
measured plateau. `training_observer.py serve` exposes a loopback dashboard at
`http://127.0.0.1:8765`; its `nudge` subcommand records bounded guidance for
future autoresearch packets. Its **View in Factorio** control moves only the
configured connected observer to an active `training/*` surface in spectator
mode. These tools do not import or invoke the active Nauvis orchestrator.

## Snapshot Validator

### Install dependency
- `pip install -r requirements.txt`

### Run
- `python tools/validate_snapshot.py <path-to-snapshot.json>`

The validator loads [schemas/snapshot.schema.json](../schemas/snapshot.schema.json) and fails loudly on any schema mismatch.

## Fixture Smoke Tests

### Valid fixture
- `python tools/validate_snapshot.py tests/fixtures/sample_snapshot.json`

### Invalid fixture (expected to fail)
- `python tools/validate_snapshot.py tests/fixtures/invalid_snapshot.json`
