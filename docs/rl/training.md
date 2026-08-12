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

A controller pins its scenario and transition schemas at process start. Repository edits therefore cannot change the contract beneath an active batch; activating a new contract requires an explicit controller restart and, when Lua changed, the matching training-mod deployment.

## Curriculum

1. Mine one resource and deliver a target rate to a sink.
2. Produce one solid item from supplied precursors.
3. Produce and route one fluid without contamination.
4. Generate and sustain an electricity target.
5. Scale one coherent line, such as 2 -> 6 -> 24 furnaces, in reserved space.
6. Connect distant areas using efficient belts, poles, and roboports.
7. Diagnose and repair deliberately damaged production chains.
8. Build multi-stage intermediates, science, and demand-driven expansion.

Each family should randomize geometry, resources, demand, clutter, fixture placement, and budgets. The mining-delivery source is an integral 2x2 producer sampled in clear space near the resource patch, not a special fixed origin. Train and held-out seeds remain separate.

## Rewards and selection

Safety failures are disqualifying rather than tradeable for scalar reward. Among safe attempts, measure:

- objective completion and sustained throughput;
- time to first output and time to target;
- material, entity, route-length, energy, and infrastructure cost;
- idle/starved capacity, failed placements, and unnecessary transfers;
- successful reuse, repair, and expandable structure.

Store reward components separately so the observatory and autoresearch can identify why a policy changed.

The first deployed profile is `mining-efficiency-v1`. Candidate geometry separates collection belts from the delivery route and records the orthogonal lower bound, actual route, excess tiles, route efficiency, poles, material cost, and occupied land. Factorio independently measures real poles, real footprint, distinct drills that worked, and cumulative working, blocked, idle, and available drill-ticks. Per-unit weights therefore have literal meanings, while scenario time, budget, and area limits cap their influence. The Observatory classifies excess routing and low productive capacity instead of showing only one scalar reward.

Policy promotion remains lexicographic. Safety, completion, and sustained output come first; productive capacity, route efficiency, time, materials, footprint, and poles distinguish otherwise successful policies. Existing immutable `mining-delivery-v1` checkpoints ignore newly added audit fields, while fresh checkpoints use the richer `mining-efficiency-v1` feature registry.

The next production curriculum should compare **upgrade versus expand**, not only repair versus duplicate. It should randomize assembler tier and quality, speed/productivity/efficiency modules, beacon support, inserter and belt tier, available footprint, capital budget, energy price, recipe demand, and research state. Rewards should use measured output and total lifecycle cost so an assembler-3, quality upgrade, module change, or added parallel machine wins only when its throughput, resource efficiency, energy, and land tradeoff is better on held-out scenarios. Repeatable productivity and mining research must enter observations as live modifiers; research cost and the downstream savings it creates are separate actions and reward evidence rather than hard-coded upgrade rules.

Use seeded elitist selection with diversity protection. Preserve champions, mutate several dimensions, and periodically test novel populations so a locally successful layout does not collapse exploration.

## Parallel training slots

Each WSL Factorio process can simulate multiple isolated `training/*` surfaces concurrently. The adaptive local setup uses five isolated headless runtimes, each starting with six active logical slots. Each runtime has its own game/RCON endpoint, save, `script-output` directory, worker IDs, surfaces, and forces. This removes the single-server simulation bottleneck while retaining shared policy and evidence storage.

Slots belonging to the same Factorio runtime must declare the same `instance_id` and identical endpoint details. Different runtime instances must keep unique ports and `script-output` paths. A stage uses one shared queue: scenario identity serializes access to its stable surface, but never permanently owns a slot. A slot that finishes immediately claims another unlocked scenario. Scale slots only while UPS, memory, report latency, and cleanup remain healthy.

For long runs, use the adaptive controller after configuring enough logical slots:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action configure -WorkerCount 5 -SlotsPerWorker 16
powershell -File scripts\run_wsl_adaptive_training_batch.ps1 --count 100 --attempts-per-scenario 20 `
  --initial-slots 6 --minimum-slots 1 --maximum-slots 16 --step 1 `
  --episodes-per-policy 100 --minimum-ups-p95 50 --minimum-ups-p98 45 `
  --stability-window-seconds 300
```

The adaptive controller starts at six logical slots on each server, samples tick advancement over each server's RCON connection every five seconds, and changes that server's capacity by one slot. Each server's capacity target is held for a complete five-minute rolling window. A healthy window opens one more gate on that server; an unsafe window closes one gate. A healthy window
requires both lower-tail safeguards: **P95 UPS >= 50** and **P98 UPS >= 45**, meaning
at least 95% and 98% of samples respectively meet those floors.

Capacity changes are graceful. The controller never interrupts a live episode for a
capacity decision. When one gate is closed on a server, its current episodes drain naturally;
their workers are not refilled, and the next stage starts only after the live count is
at or below the reduced target. This keeps completed episodes as valid fitness evidence
and prevents a scale-down from creating an abort/requeue storm.

Capacity stages and policy generations are deliberately separate. A policy remains
immutable through as many capacity stages as needed to obtain 100 terminal episodes;
only then is it updated into the next generation. Capacity changes never discard
completed episodes; only terminal evidence enters policy learning.

Live episode measurement is intentionally every five seconds rather than once per
second, keeping shared RCON/report-file work bounded. In the Observatory,
**Heartbeat / elapsed** distinguishes the last telemetry update from Factorio ticks
spent in the current episode.

The preferred local worker uses the Linux headless build under WSL2:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action bootstrap -WorkerCount 5 -SlotsPerWorker 16
powershell -File scripts\manage_wsl_training_worker.ps1 -Action start -WorkerCount 5
powershell -File scripts\manage_wsl_training_worker.ps1 -Action status -WorkerCount 5
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

## Optional CUDA learner

Factorio simulation, RCON, SQLite, JSON validation, and report I/O remain CPU work. The small online LinUCB selector also stays CPU-bound because each episode ranks only a few candidates; GPU launch and transfer overhead would make it slower. The optional reward/failure surrogate instead trains a batched model from completed transition evidence between controller stages. It is advisory only: it cannot write the database, select a live plan, or promote a policy.

Install its isolated Python 3.12 CUDA runtime without replacing the controller interpreter:

```powershell
powershell -File scripts\setup_rl_gpu_environment.ps1 -Action install
powershell -File scripts\setup_rl_gpu_environment.ps1 -Action status
```

Then train an external checkpoint after at least 32 terminal transitions:

```powershell
.\data\rl-gpu-venv\Scripts\python.exe tools\train_gpu_surrogate.py `
  --database data\training\experience.db `
  --output data\training\surrogates\mining-v1.pt --require-cuda
```

The workstation has 8 GB VRAM, so do not run the surrogate, a local LLM, and other GPU-heavy work concurrently. Run it between Factorio collection stages and require CUDA for experiments intended to inform policy research. If CUDA is unavailable, the training controller remains unaffected.

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
- `factorio_training_lab/*.lua`: deploy the training mod, synchronize `%APPDATA%\Factorio\mods\factorio_training_lab`, restart the affected training worker and GUI Factorio session, then restart its controller if necessary.
- Training changes never require redeploying the deterministic mod unless shared Lua code was deliberately changed too.
