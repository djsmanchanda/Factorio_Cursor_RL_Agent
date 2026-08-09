# Path: docs/33_training_architecture.md
# Purpose: Define the isolated evolutionary training and bounded autoresearch runtime.

# Training Architecture

## Boundary

Training is a sidecar system. It does not replace, import, deploy into, or
silently influence the active `nauvis/player` runtime. The active builder stays
deterministic. Training may import pure planner functions and submit their
schema-valid BuildPlans to disposable `training/*` surfaces through the same
authorization gate used by the deterministic executor.

The dependency direction is one way:

```text
active runtime -> core + planners
training       -> core + planners + training-only runtime
active runtime -X-> training or experimental
training       -X-> autonomous_builder or stage orchestration
```

## Learning unit

One complete BuildPlan is one episode in version 1. The learner sees a bounded
catalog of plans produced by deterministic code, selects one, and receives the
result. It cannot add, move, or remove an entity action. A failed plan therefore
teaches candidate selection, timing, and resource tradeoffs without weakening
geometry, force, surface, budget, or fixture invariants.

Each immutable artifact carries a canonical SHA-256 identity: scenario hash
binds the environment and objective, plan hash binds every action, and policy
hash binds the feature contract and policy parameters.

## Runtime components

- `factorio_training_lab/` provisions, chunk-uploads, physically executes, measures,
  and recycles one isolated episode. It is a separate mod and never owns Nauvis state.
- `training/candidates/` compiles deterministic alternatives.
- `training/episode.py` binds selection, execution, reward, transition, and
  guaranteed recycle.
- `training/store.py` persists experience and metadata in SQLite WAL mode.
- `training/policies.py` provides a baseline and contextual bandit.
- `training/population.py` performs seeded elitist mutation.
- `training/evaluation.py` gates promotion on paired frozen holdout scenarios.
- `training/scheduler.py` assigns work to explicit training-only workers.
- `training/research/` accepts bounded settings proposals from a local model;
  it cannot edit or execute repository code.

## Parallelism

One Factorio process executes one episode at a time. Ten to twenty concurrent
attempts require ten to twenty explicitly configured headless training workers,
each with its own port, save, and `script-output` directory. The scheduler
leases a scenario to each worker and recovers expired leases. It never discovers
or connects to arbitrary Factorio processes.

Start with one worker and measure UPS, RAM, report latency, and cleanup. Increase
the worker count only while those measurements remain healthy. Thousands of
attempts come from repeated persisted batches, not hundreds of permanent forces
or surfaces inside one save.

The current batch and autoresearch commands are separate processes. The
in-process `ResourcePhaseLock` does not coordinate them across process
boundaries, so run local-LLM research between Factorio collection/evaluation
batches, not concurrently. A future unattended supervisor must own a
cross-process phase lease before it may overlap or alternate these commands
without operator scheduling.
The mining-layout curriculum completes finite primary technologies on its
disposable episode force before placement. It deliberately leaves repeatable
technologies unresearched. This baseline is training-only; future research
curricula will declare their own force technology state.

An explicit worker file has this shape (repeat the object with unique ports and
directories for 10-20 workers):

```json
{
  "workers": [{
    "worker_id": "training-01",
    "instance_id": "factorio-training-01",
    "host": "127.0.0.1",
    "game_port": 35001,
    "rcon_port": 28001,
    "script_output": "C:/Factorio-training-01/script-output",
    "surface_prefix": "training/",
    "force_prefix": "training-"
  }]
}
```

After dedicated workers are configured:

```powershell
$env:FACTORIO_TRAINING_RCON_PASSWORD = "<training-server-password>"
python tools/run_training_batch.py --workers training-workers.json --count 100
```
## WSL-native training worker

The preferred local worker runs the Linux headless package under the existing
Ubuntu WSL2 distribution. Its Factorio runtime, save, mods, and logs live under
`~/factorio-training-01` on the Linux filesystem. Only `script-output` is a
symlink to `%LOCALAPPDATA%\Factorio-training-wsl-01\script-output`, preserving
the Windows bridge and observatory without putting simulation data on DrvFS.

The worker binds RCON only to `127.0.0.1:28001`. Its game UDP socket binds to
the private WSL virtual network on port `35001`, so the Windows Factorio GUI
can join through the current WSL IP. Bootstrap generates a local RCON secret
outside Git; the batch helper passes its path to the runner, so no operator
password entry is needed. RCON authentication remains enabled.

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action bootstrap
powershell -File scripts\manage_wsl_training_worker.ps1 -Action start
powershell -File scripts\run_wsl_training_batch.ps1 --count 100 --attempts-per-scenario 20
```

Before reusing these ports, stop the Windows training worker. The WSL helper
refuses to overwrite running worker mods and the WSL runtime has no authority
over the real-base server or its data directory.
## Evolution and promotion

Training uses train, validation, and frozen holdout seed partitions. Elites
survive and offspring receive seeded mutations. Promotion is lexicographic:

1. zero safety violations;
2. no regression in completion or earlier curriculum families;
3. better sustained objective rate;
4. faster completion;
5. lower material and infrastructure cost;
6. fewer failed placements.

A scalar reward helps learning but can never compensate for a safety failure.
The first release has no real-base authority. Future champions must first run in
shadow mode; mutation authority requires a separate explicit review.

## Bounded local autoresearch

The local LLM is a proposal generator, not a free-form code editor. It receives
aggregate evidence and may propose only allowlisted numeric settings. Proposals
are range checked, deduplicated, evaluated on paired seeds, and rejected when
worse. Prompts and responses are stored for audit.

Do not assume stock `llama.cpp` exposes per-expert routing counts:
`--n-cpu-moe` places whole expert layers on CPU. Initially record the model
fingerprint, offload configuration, prompt/decode timing, and peak RAM/VRAM.
Optimizing the most-used experts later requires a maintained instrumentation
patch and its own benchmark milestone.


After experiment scores plateau, request one allowlisted proposal from a local
OpenAI-compatible `llama.cpp` endpoint:

```powershell
python tools/run_autoresearch.py --model <model-id> --policy policy.json `
  --evidence evidence.json --parent-policy-id <policy-id>
```

The command records runtime/offload measurements and the proposed numeric
configuration. It does not edit source or automatically promote the result.

## RL observatory and human guidance

The observatory combines two evidence paths without becoming a controller:

- atomic per-worker JSON in `data/training/live/` shows the current episode,
  phase, measured rate, sustain progress, and worker error;
- read-only SQLite queries show queue/completion counts, policy lineage, recent
  rewards, structured bottleneck classes, autoresearch proposals, and local-LLM
  runtime measurements.

Start the loopback-only dashboard while a training batch is running:

```powershell
python tools/training_observer.py serve
# Open http://127.0.0.1:8765
```

For terminals or automation, print the same stable snapshot:

```powershell
python tools/training_observer.py snapshot
```

The HTTP surface is deliberately read-only. A nudge is an explicit CLI action:

```powershell
python tools/training_observer.py nudge --focus throughput `
  --message "Test whether direct-belt candidates sustain delivery more reliably." `
  --expires-generation 12
python tools/training_observer.py dismiss <guidance-id>
```

Guidance is capped, typed, auditable, and included only in future local-LLM
research packets. It cannot change reward weights, evaluators, schemas, planner
code, deployment, Factorio safety constraints, or the deterministic Nauvis
runtime. A proposal still passes the existing numeric allowlist and frozen
paired evaluation before it can become a training-policy candidate.

## Operational lifecycle

Python-only training changes require restarting training workers/controllers.
Changes under `factorio_training_lab/` require deploying that separate training
mod and restarting only the training Factorio server. The lab owns its bounded
physical execution subset, so this does not deploy the deterministic executor.
Neither action requires
redeploying or restarting the deterministic real-base mod/server.
