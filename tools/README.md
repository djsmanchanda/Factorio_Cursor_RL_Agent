<!-- Path: tools/README.md -->
<!-- Purpose: Describe how to run local tooling for snapshot validation. -->

# Tools

## Live Factorio Interaction

Use the [authoritative interaction runbook](../docs/31_factorio_mod_interaction_and_troubleshooting.md) for RCON preflight, shell recipes, `GameBridge`, `script-output`, registered commands, and failure diagnosis.

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
local-model runtime measurements. `run_autoresearch.py` asks a loopback-only OpenAI-compatible endpoint for one allowlisted numeric experiment after a measured plateau. These tools do not import or invoke the
active Nauvis orchestrator.

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
