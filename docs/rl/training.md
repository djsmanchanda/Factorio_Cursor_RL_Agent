<!-- Path: docs/rl/training.md | Purpose: Describe training scenarios, workers, evolution, autoresearch, and observation. -->

# Training and autoresearch

## Current system

- `factorio_training_lab/` owns isolated `training/*` surfaces and `training-*` forces.
- `training/scenarios/` generates seeded tasks and budgets.
- `training/candidates/` currently supplies baseline candidates while the structural action space is expanded.
- `training/episode.py` executes, measures, rewards, and always recycles an episode.
- `training/store.py` preserves transitions, policy lineage, worker leases, and research evidence.
- `training/policies.py`, `population.py`, and `evaluation.py` select, mutate, and gate policies.
- `training/research/` accepts bounded proposals from a local OpenAI-compatible model.
- `tools/training_observer.py` exposes read-only progress, bottlenecks, lineage, proposals, and human guidance.

The current implementation is a starting point, not the architectural ceiling. Candidate selection should evolve toward parameterized structural generation and mutation while retaining validators and reproducibility.

## Episode contract

Each episode declares a seed, isolated surface and force, fixtures, inventory budget, allowed entities, objective rate, sustain duration, time limit, and reward weights. Electricity fixtures are role-tagged: generators must join a pole network, while accumulators are storage that must charge and discharge through that same network rather than being counted as generation. A transition records the observation, available or generated actions, chosen action, execution report, structured outcome, next observation, and decomposed reward.

Training evidence survives surface recycle, process restart, and save replacement.

## Curriculum

1. Mine one resource and deliver a target rate to a sink.
2. Produce one solid item from supplied precursors.
3. Produce and route one fluid without contamination.
4. Generate and sustain an electricity target.
5. Scale one coherent line, such as 2 -> 6 -> 24 furnaces, in reserved space.
6. Connect distant areas using efficient belts, poles, and roboports.
7. Diagnose and repair deliberately damaged production chains.
8. Build multi-stage intermediates, science, and demand-driven expansion.

Each family should randomize geometry, resources, demand, clutter, and budgets. Train and held-out seeds remain separate.

## Rewards and selection

Safety failures are disqualifying rather than tradeable for scalar reward. Among safe attempts, measure:

- objective completion and sustained throughput;
- time to first output and time to target;
- material, entity, route-length, energy, and infrastructure cost;
- idle/starved capacity, failed placements, and unnecessary transfers;
- successful reuse, repair, and expandable structure.

Store reward components separately so the observatory and autoresearch can identify why a policy changed.

Use seeded elitist selection with diversity protection. Preserve champions, mutate several dimensions, and periodically test novel populations so a locally successful layout does not collapse exploration.

## Parallel training slots

One Factorio process can simulate multiple isolated `training/*` surfaces concurrently. The default local setup therefore uses one WSL headless runtime and four logical training slots, all sharing one game/RCON endpoint and `script-output` directory while retaining unique worker IDs, surfaces, forces, episodes, telemetry files, and report request IDs. This avoids duplicating saves and server processes.

Slots belonging to the same Factorio runtime must declare the same `instance_id` and identical endpoint details. Different runtime instances must keep unique ports and `script-output` paths. Scale slots only while UPS, memory, report latency, and cleanup remain healthy.

For long runs, use the adaptive controller after configuring enough logical slots:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action configure -WorkerCount 1 -SlotsPerWorker 32
powershell -File scripts\run_wsl_adaptive_training_batch.ps1 --count 100 --attempts-per-scenario 20 `
  --initial-slots 4 --minimum-slots 4 --maximum-slots 32 --step 4
```

The adaptive controller runs short stages, samples tick advancement over the shared
Factorio RCON connection, and changes concurrency only at stage boundaries. It grows
by four after two healthy windows and shrinks by four after one unhealthy window.
The safety statistic is the lower-tail equivalent of a P98 UPS requirement: at least
55 UPS for 98% of samples. This avoids a high-tail percentile hiding short server
stalls. A missing or too-short UPS window is neutral and never causes a scale-up.

The preferred local worker uses the Linux headless build under WSL2:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action bootstrap -WorkerCount 1 -SlotsPerWorker 4
powershell -File scripts\manage_wsl_training_worker.ps1 -Action start -WorkerCount 1
powershell -File scripts\manage_wsl_training_worker.ps1 -Action status -WorkerCount 1
powershell -File scripts\run_wsl_training_batch.ps1 --count 100 --attempts-per-scenario 20
```

To find the safe concurrency limit on one laptop, run bounded live probes rather than assuming twenty surfaces are safe:

```powershell
powershell -File scripts\benchmark_wsl_training_slots.ps1 -MaximumSlots 20
```

The probe grows from 4 to 20 slots in steps of 4, testing one disposable episode per slot. It stops before the next level when a stage falls below its 75% completion gate, average host CPU exceeds 90%, available memory falls below 4 GiB, or wall time exceeds twice the initial stage. Ordinary candidate timeouts are reported but do not by themselves prove a laptop-capacity failure. Its database, checkpoint, and live telemetry are written beneath ignored `data/training-capacity/`, so the measurements are a local capacity gate rather than policy fitness evidence.

For an already configured worker file:

```powershell
python tools/run_training_batch.py --workers training-workers.json --count 100 --attempts-per-scenario 20
```

Generate contracts without Factorio:

```powershell
python tools/generate_training_scenarios.py --count 100 --start-seed 0 --output-dir data/training/mining-delivery
```

## Bounded autoresearch

The local LLM receives aggregate evidence and proposes changes inside an explicit experiment envelope. Today the safest envelope is allowlisted numeric policy, reward, and search settings. A later code-editing envelope may touch training-only files in an isolated worktree, but every proposal must pass focused tests, paired evaluation, resource limits, diff review, and automatic rollback before it can compete.

Do not run a large local controller model on the GPU at the same time as GPU-heavy training unless measurements show both remain healthy. Record model fingerprint, quantization, context, offload settings, prompt/decode timing, RAM/VRAM, and experiment result. Per-expert MoE optimization is a later measured optimization, not an assumption available from ordinary `llama.cpp` logs.

```powershell
python tools/run_autoresearch.py --model <model-id> --policy policy.json `
  --evidence evidence.json --parent-policy-id <policy-id>
```

## Observatory and guidance

```powershell
python tools/training_observer.py serve
# Open http://127.0.0.1:8765

python tools/training_observer.py snapshot

python tools/training_observer.py nudge --focus throughput `
  --message "Prefer experiments that sustain direct-belt delivery." `
  --expires-generation 12
```

The site may focus a connected spectator on the owned active training surface. It does not control Nauvis, modify plans, or bypass evaluation. Human nudges are typed, expiring research context; they do not directly rewrite rewards or promote policies.

## Lifecycle

- Python-only training change: restart the affected batch, controller, or observer.
- `factorio_training_lab/*.lua`: deploy the training mod and restart the affected training worker, then restart its controller if necessary.
- Training changes never require redeploying the deterministic mod unless shared Lua code was deliberately changed too.
