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

## Local Laya training

The optional `requirements-laya.txt` environment contains the upstream Laya,
Transformers, PEFT, SafeTensors, and CUDA PyTorch dependencies. Build typed
cases from disposable evidence with `tools/build_laya_dataset.py`, verify the
exclusive GPU window with `tools/laya_preflight.py`, then run the single-GPU
LoRA/RLCD profile with `tools/train_laya_4060.py`. The training path requires
an independent calibration split and never publishes or promotes a checkpoint
automatically. Set `LAYA_CASES_PATH` and `LAYA_CHECKPOINT_DIR` for the RL
Observatory's Laya page to display the active dataset and checkpoint.
For an unattended local run after the GPU preflight, use
`bash scripts/run_laya_training.sh`; it refuses missing model/data files and
writes to `logs/laya-4060-training.log`.
After a run, score the independent seed-held-out split with
`tools/evaluate_laya.py`; it loads the adapter and trained decision head,
reports exact accuracy/NLL/Brier/ECE/latency, and marks the checkpoint as
disposable-shadow eligible only when inference completes without failures.
The RL Observatory's `/laya` page can start or stop this disposable pipeline
with explicit confirmation and displays live loss, optimizer steps, gradient
norm, entropy, reward statistics, throughput, VRAM, loss history, and the
held-out/test benchmark reports. It cannot control Nauvis or the deterministic
production controller.
To queue an optimized retry behind an active run, use
`bash scripts/queue_laya_optimized.sh`; it waits for training and evaluation
to finish, preserves the first checkpoint, then starts a separate optimized
checkpoint with micro-batch 2 and gradient accumulation 16.
`tools/laya_console_server.py` (`http://127.0.0.1:9138/`) is a loopback console
to start/stop `scripts/laya_overnight.sh` and watch its rounds, labeller
progress, and evaluations; see `docs/rl/training.md`'s "Laya Operations
Console" section.

The in-game **Laya Coach** window writes bounded observations and optional
human-labelled workflow choices under Factorio's
`script-output/factorio_mod/laya_guidance/`. Merge reviewed records into a
future dataset with `tools/import_laya_coach_data.py`; guidance without a
chosen option is kept as context and is never treated as a target. Pass that
directory to `tools/run_laya_shadow_batch.py --guidance-path` to inject the
latest high-level guidance into disposable shadow decisions.

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
