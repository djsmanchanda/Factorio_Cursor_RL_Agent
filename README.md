<!-- Path: README.md | Purpose: Entry point for the RL-first Factorio autonomy project. -->

# Factorio Cursor RL Agent

This repository develops a learning system that improves factory decisions through repeated Factorio episodes. The RL system is the main focus. A separate deterministic runtime supplies useful schemas, validators, planners, execution primitives, and a baseline, but it remains under active improvement.

## Start here

- [Documentation map](docs/README.md)
- [Architecture](docs/architecture.md)
- [RL system](docs/rl/README.md)
- [Training and autoresearch](docs/rl/training.md)
- [Deterministic runtime](docs/deterministic/README.md)
- [Live operations](docs/factorio_operations.md)

## Runtime boundaries

| Runtime | Purpose | Authority |
|---|---|---|
| `training/` + `factorio_training_lab/` | Disposable learning episodes, evolution, evaluation | Training surfaces only |
| `tools/autonomous_run.py` + `orchestrator/` + `planners/` | Deterministic real-base runtime and baseline | Explicit Nauvis/player operations |
| `experimental/legacy_autonomy/` | Historical RL ideas | Unsupported reference only |

The systems may share contracts and validated primitives. They must not share mutable runtime state or silently invoke one another.

## Common entry points

```powershell
# Run repository tests (compact output is configured in pytest.ini)
python -m pytest

# Generate offline scenarios
python tools/generate_training_scenarios.py --count 100

# Run a training batch using configured workers
python tools/run_training_batch.py --workers training-workers.json --count 100 --attempts-per-scenario 20

# Start the RL observatory
python tools/training_observer.py serve

# Start the local operations dashboard
scripts\launch_dashboard.ps1
```

Use explicit host, port, surface, and force for live operations. See the runbook before changing saves, deployed mods, or server processes.
