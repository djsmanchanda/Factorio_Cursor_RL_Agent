--- FILE: README.md ---

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
# Run repository tests
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

--- FILE: docs/architecture.md ---

<!-- Path: docs/architecture.md | Purpose: Define the RL-first architecture and the deterministic runtime boundary. -->

# Architecture

## Two cooperating systems

The repository deliberately keeps two runtimes:

1. **RL training system — primary focus.** It observes a disposable Factorio task, proposes structural and operational actions, receives measured outcomes, and improves policies across many attempts.
2. **Deterministic production runtime — reference and fallback.** It runs the current Nauvis factory using hand-written planners. Its mechanics are useful but incomplete; known problems in layout, transport, recovery, and scaling still require work.

The RL system may reuse stable mechanics such as schemas, recipe and prototype exports, legality checks, collision surveys, build-plan execution, and measurements. Reuse must sit behind explicit interfaces; the training runtime must not call the real-base orchestrator as a hidden policy.

## Shared boundary

```text
Factorio snapshot
      |
      v
normalized observation + live prototype facts
      |
      +----------------------+----------------------+
      |                                             |
      v                                             v
learned policy                             deterministic baseline
      |                                             |
      +---------------- candidate plan/action ------+
                            |
                            v
                 shared legality/safety validators
                            |
                            v
                  isolated execution + measurement
```

Shared contracts describe facts and actions. They must not force the learned policy to reproduce a deterministic layout recipe.

## Authority by artifact

- `AGENTS.md`: agent behavior and workflow.
- `docs/system_invariants.md`: isolation, safety, and evidence boundaries.
- `schemas/`: serialized contract shape and version.
- `docs/rl/` and `docs/deterministic/`: design intent for each system.
- Code and live evidence: what currently happens.

The latest explicit user direction controls product intent. When it changes an older design, update the relevant documentation, schema, test, and implementation together instead of treating stale prose as immutable.

## Promotion path

A learned policy progresses through offline validation, disposable live episodes, held-out evaluation, comparison with the deterministic baseline, and an explicitly authorized production trial. Every promoted version keeps its evidence, parent checkpoint, configuration, and rollback target.

--- FILE: docs/deterministic/planning.md ---

<!-- Path: docs/deterministic/planning.md | Purpose: Keep useful deterministic planning principles without making them RL constraints. -->

# Deterministic planning

## Current components

- `LocalLayoutPlanner`: entity placement and local connections.
- `CityPlanner`: larger zones, corridors, and interfaces.
- `PlanetPlanner`: planet-level production and transfers.
- `InterplanetarySupervisor`: global flows and recovery.

These names describe intended responsibilities, not permission to build speculative layers before current scenarios need them.

## Planning principles

- Plan machines, inputs, outputs, power, logistics, and expansion space together.
- Prefer a continuous direct belt over chest and inserter hops when both solve the same transport problem.
- Route around established infrastructure. A blocked endpoint should trigger another candidate or a safe failure.
- Diagnose supply, delivery, inserter throughput, machine speed, and machine count in that order.
- Repair existing capacity before duplicating it.
- Keep mall reserves distinct from sustained intermediate demand. Promote intermediates to full lines when measured demand justifies it.
- Treat fluids as type-safe networks; never mix fluids through an implicit shared pipe.
- Base capacity on rates and live game facts, not machine counts alone.

These are also useful RL priors and reward features. They are not a catalog the learned policy must copy.

--- FILE: docs/deterministic/README.md ---

<!-- Path: docs/deterministic/README.md | Purpose: Describe the current deterministic runtime honestly and define what RL may reuse. -->

# Deterministic runtime

## Role

The deterministic runtime is the current real-base implementation and a measurable baseline for learning. It is not the project's final architecture and is not assumed correct merely because it has broad test coverage.

Active path:

```text
tools/autonomous_run.py
  -> orchestrator/autonomous_builder.py
  -> orchestrator/stage_*.py
  -> planners/*.py
  -> factorio_mod build-plan executor
```

Use explicit `surface=nauvis` and `force=player` for real-base work.

## Semi-stable mechanics worth sharing

- Live recipe, technology, entity, and inventory exports.
- Versioned snapshots, goals, plans, and reports.
- Collision and occupancy surveys.
- Material preflight and legal build-plan execution.
- Measurements for power, logistics, machines, belts, fluids, and research.
- Safe failure reporting and lifecycle tooling.

Sharing means extracting a narrow interface. It does not mean importing the full orchestrator or its layout choices into training.

## Known weaknesses

- Layout and transport are often added after machine placement instead of planned as one structure.
- Recovery can encode one observed failure as a new deterministic branch.
- Supply-starved machines, finite mall demand, and true capacity shortages have been confused.
- Power, roboport, belt, pipe, and future-expansion corridors are not consistently reserved.
- Live deployment drift can make tested code differ from running behavior.

Fix these when they block the real runtime or when the fix produces a reusable primitive. Do not let deterministic cleanup displace the RL roadmap.

## Real-base rule

Observe the live factory, identify the planner decision that caused the failure, fix the code and focused tests, deploy deliberately when Lua changed, then rerun from the requested save. Never manually repair the base to conceal a planner defect.

--- FILE: docs/factorio_operations.md ---

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
- Use `tools/run_training_batch.py` or the WSL wrapper for training episodes.

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
| `factorio_training_lab/*.lua` | Deploy training mod, restart affected training worker, restart batch/controller if needed |
| Documentation/tests only | No runtime restart |

Repository, dedicated-server, GUI-client, and WSL mod copies are distinct. Compare hashes or timestamps when a client reports mismatched mods or the runtime behaves like old code.

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

--- FILE: docs/README.md ---

<!-- Path: docs/README.md | Purpose: Route readers to the smallest relevant active document set. -->

# Documentation map

Read only the route relevant to the current task.

| Task | Documents |
|---|---|
| Project structure or shared boundaries | [Architecture](architecture.md), [System invariants](system_invariants.md) |
| RL policy, structural planning, rewards | [RL system](rl/README.md) |
| Episodes, workers, evolution, autoresearch | [Training](rl/training.md) |
| Existing deterministic runtime | [Deterministic runtime](deterministic/README.md), [Planning](deterministic/planning.md) |
| RCON, deployment, restarts, live failures | [Factorio operations](factorio_operations.md) |
| Current priorities | [Roadmap](roadmap.md) |
| Game mechanics used by validators/rewards | [Factorio mechanics](reference/factorio_mechanics.md) |

`docs/archive/` contains superseded designs and historical handoffs. It is evidence, not current instruction.

--- FILE: docs/reference/factorio_mechanics.md ---

<!-- Path: docs/reference/factorio_mechanics.md | Purpose: Keep high-value Factorio facts used by planners, validators, and rewards. -->

# Factorio mechanics reference

Treat live prototype and force exports as authoritative for the running game and mod set. External guides are hints until represented in a verified observation or contract.

## Rates and capacity

- Compare demand and supply in items per Factorio tick or per second with an explicit conversion.
- Machine count alone is not capacity. Include recipe time, crafting or mining speed, productivity, inserter delivery, belt lanes, power, and actual working state.
- Belt and inserter nominal rates are ceilings; geometry, hand movement, lane use, and pickup/drop positions affect realized throughput.
- The measured inserter reference is kept in `docs/reference/inserter_throughput_factorio_2_0_26.txt`.

## Logistics

- Prefer a continuous belt when two endpoints can be connected without an operationally useful buffer.
- Splitters may merge, split, balance, prioritize an input/output, or filter one output. Their value should be measured against cost and throughput need.
- Logistic coverage and construction coverage are different. A powered roboport can still be outside one required radius.

## Fluids

- One connected pipe network carries one fluid type.
- Validate fluid-box type, connection position, direction, rotation, and existing contents before planning.
- Pumps are directional and can separate or control networks; they do not justify mixing incompatible fluids.
- Long or heavily branched paths can reduce practical throughput. Measure delivery at the consumer.

## Electricity

- Connectivity to a pole is not proof that its electric network has generation.
- Reward stable satisfaction and low infrastructure cost, but treat disconnected required entities as failure.

## Research and recipes

- Use the live player-force recipe and technology exports.
- Displayed repeatable research levels may not equal prototype identifiers; resolve exact names before ranged forms.
- A locked recipe may inform future structure but is not currently executable.

Detailed historical game notes remain in `docs/archive/legacy-canonical/21_external_game_knowledge.md` and `23_fluid_systems.md`.

--- FILE: docs/rl/README.md ---

<!-- Path: docs/rl/README.md | Purpose: Define the RL system, including learned structural planning. -->

# RL system

## Goal

Build an agent that tries, measures, learns from failure, and retries without needing a human to encode each mistake as a new recipe. It should eventually plan and operate a self-expanding factory, not merely rank deterministic blueprints.

## What RL may learn

Inside disposable training environments, the policy may decide:

- functional zones and reserved corridors;
- machine count, orientation, spacing, and phased expansion;
- belt, splitter, chest, inserter, pipe, pole, and roboport topology;
- direct delivery versus justified buffering;
- repair, upgrade, reroute, or expansion actions;
- ordering and timing under material, energy, space, and time constraints.

The action space should grow from bounded parameterized structures and edit operators into more compositional planning. It must not remain a finite menu of hand-authored complete layouts.

## Hard constraints and soft principles

Hard validators reject actions that are illegal or unsafe: wrong surface/force, collisions, unavailable entities, invalid fluid mixing, out-of-bounds placement, budget violations, protected fixture changes, or malformed contracts.

Everything else should normally be learned through observations, priors, and rewards:

- group related functions into readable zones;
- reserve corridors and future expansion space;
- prefer short, continuous, direct transport;
- penalize unnecessary chest/inserter hops and throughput bottlenecks;
- match supply rate to demand recursively, back to ore, fluids, or power;
- repair usable capacity before duplicating it;
- minimize completion time, materials, infrastructure, and energy while sustaining output.

These principles guide exploration without prescribing exact coordinates.

## Improvement loop

```text
sample scenario and seed
  -> observe facts and prior outcome
  -> propose or mutate a structure
  -> validate hard constraints
  -> execute in disposable Factorio
  -> measure throughput, cost, time, failures, and bottlenecks
  -> assign decomposed reward
  -> preserve elites and diversify offspring
  -> evaluate on held-out scenarios
```

Failures must remain structured evidence. A timeout, no delivery, blocked route, starved machine, low power, or insufficient sustain should identify what happened and which decision caused it so later attempts can change meaningfully.

## Relationship to deterministic code

Reuse live game facts, schemas, validators, execution, and measurements where they are genuinely generic. Use the deterministic planner as a baseline and occasional bootstrap, not as the hidden source of every candidate. A policy has learned little if all structural choices were already made for it.

The retired `experimental/legacy_autonomy/` implementation may supply ideas or small compatible utilities after review. It is never imported wholesale into the active training runtime.

## Production promotion

Promotion requires zero safety violations, held-out success, non-regression on earlier curriculum families, reproducible lineage, and a measurable advantage over simple and deterministic baselines. Real-base trials begin in shadow or narrowly bounded mode and retain rollback.

--- FILE: docs/rl/training.md ---

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

Each episode declares a seed, isolated surface and force, fixtures, inventory budget, allowed entities, objective rate, sustain duration, time limit, and reward weights. A transition records the observation, available or generated actions, chosen action, execution report, structured outcome, next observation, and decomposed reward.

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

## Parallel workers

One Factorio process executes one physical episode at a time. Ten concurrent attempts require ten isolated workers with unique game ports, RCON ports, saves, and `script-output` paths. Scale worker count only while UPS, memory, report latency, and cleanup remain healthy.

The preferred local worker uses the Linux headless build under WSL2:

```powershell
powershell -File scripts\manage_wsl_training_worker.ps1 -Action bootstrap
powershell -File scripts\manage_wsl_training_worker.ps1 -Action start
powershell -File scripts\manage_wsl_training_worker.ps1 -Action status
powershell -File scripts\run_wsl_training_batch.ps1 --count 100 --attempts-per-scenario 20
```

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

--- FILE: docs/roadmap.md ---

<!-- Path: docs/roadmap.md | Purpose: Record current priorities without freezing speculative architecture. -->

# Roadmap

## Primary: make learning real

1. Build small scenario families for mining, solid crafting, fluids, power, transport, repair, and phased expansion.
2. Give policies enough observations and actions to discover structure rather than select only prebuilt layouts.
3. Improve rewards and bottleneck attribution so failures teach the next population.
4. Run diverse attempts in parallel, evaluate on held-out seeds, and preserve reproducible lineage.
5. Use bounded local autoresearch to propose changes to policy, reward, curriculum, and model configuration.
6. Promote only when learned policies beat simple and deterministic baselines without violating validators.

## Supporting: improve the deterministic runtime

- Finish fuels, oil, chemical production, and remaining science dependencies.
- Replace fragile transport afterthoughts with coherent mine, refinery, and production layouts.
- Improve recursive supply diagnosis, repair-before-duplicate behavior, and recovery from blocked placements.
- Extract reusable observation, validation, execution, and measurement primitives for training.

## Later

City, rail, planet, and interplanetary planning remain useful research directions, not present-day invariants. Their previous detailed designs are archived until evidence justifies restoring them.

--- FILE: docs/system_invariants.md ---

<!-- Path: docs/system_invariants.md | Purpose: Protect runtime isolation, production safety, and truthful evidence without forbidding RL planning. -->

# System invariants

These are boundaries, not layout recipes.

## 1. Runtime isolation

- Real Nauvis/player, disposable training workers, and legacy experiments are separate runtimes.
- Training may not mutate the real save, consume its command channel, or silently replace its planner.
- Surface, force, server, port, save, mod set, and policy generation are explicit in evidence.

## 2. Structural learning with protected production

- RL may plan zones, entity layouts, belts, pipes, power, logistics, and phased expansion inside disposable training environments.
- Candidate plans must pass shared hard validators for Factorio legality, collisions, available resources, fluid purity, and scope.
- Design preferences such as short routes, direct belts, zoning, expansion room, and stability are rewards or priors unless explicitly promoted to a hard safety rule.
- Real-base authority requires an explicitly promoted policy, bounded action scope, observable results, and rollback. Training success alone grants no production authority.

## 3. Evidence and lifecycle truth

- A unit test proves only the tested behavior.
- A repository change is not deployed code.
- A deployed mod is not active until the correct runtime reloads it.
- A running episode is not successful until its measured outcome is recorded.
- Reports state the exact validation, deployment, restart, surface/force, and live observation performed.

When an older document conflicts with these boundaries, archive or update it. Historical documents do not regain authority because code still references their names.
