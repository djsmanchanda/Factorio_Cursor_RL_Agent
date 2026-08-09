# Path: docs/32_training_curriculum.md
# Purpose: Define the isolated curriculum that trains reusable factory decisions before real-base authority.

# Training Curriculum

## Goal

Build a second, isolated runtime that learns reusable industrial decisions from
small Factorio tasks. The existing Nauvis/player builder continues developing
real production capabilities; the training runtime may reuse its observers,
planners, BuildPlans, and execution reports but never mutates the real base.

The training runtime does not replace deterministic planning. A planner
generates safe candidate plans; a policy learns which candidate to choose and
when to retry with another one.

## Safety boundary

- Every episode runs on a `training/*` surface and an isolated `training-*`
  force.
- Scenario fixtures belong to the environment, not the agent. They may include
  an electric-energy interface and an infinity chest used only as a measured
  item sink.
- The agent receives construction entities only. Production ingredients are
  not included in mining-scenario budgets.
- Fixture deconstruction is forbidden.
- Build actions remain schema-validated and authorization-gated.
- No training module imports or invokes the active real-base orchestrator.
- A learned policy cannot invent geometry; it chooses among deterministic,
  planner-produced candidates.

## Episode lifecycle

```text
generate scenario
  -> provision isolated surface and force
  -> observe
  -> generate candidate plans
  -> select one candidate
  -> execute and measure
  -> record transition and reward
  -> finish, fail, or select another candidate
  -> recycle the surface
```

The persisted transition is the learning boundary. Deleting a surface,
restarting Factorio, or restoring the real-base save must not delete prior
experience.

## Version 1 contracts

`schemas/training_scenario.schema.json` defines one complete environment:

- stable scenario id and seed;
- bounded surface and isolated force names;
- one resource patch;
- protected power-source and delivery-sink fixtures;
- construction-only inventory budget;
- target item rate and sustain duration in ticks;
- episode time limit, build boundary, allowed entities, and reward weights.

`schemas/training_transition.schema.json` records:

- initial and next observations;
- every available candidate with a stable action id and plan hash;
- the chosen candidate;
- structured success or failure classification;
- measured throughput, delivery, construction cost, and placement results;
- decomposed reward whose components sum to the stored total.

## First scenario family: mining delivery

The first curriculum asks the agent to mine iron ore, copper ore, coal, or
stone and sustain delivery to a protected sink. Seeds vary:

- resource type;
- patch width, height, and quadrant;
- sink position;
- target rate equivalent to 0.5 to 3.0 items per second, stored per tick;
- derived construction budget and route length.

All scenarios use electric drills and electric infrastructure. The generator
provides one machine of headroom above the theoretical base drill count so the
policy can compare compact and overbuilt candidates without receiving an
unbounded inventory.

Generate one hundred contracts without touching Factorio:

```powershell
python tools/generate_training_scenarios.py --count 100 --start-seed 0 --output-dir data/training/mining-delivery
```

## Curriculum order

1. Mining and direct item delivery.
2. Solid conversion from supplied precursor fixtures.
3. Fluid production and fluid-purity repair.
4. Electricity generation and stable satisfaction.
5. In-place capacity growth such as 2 to 6 to 24 furnaces.
6. Power and roboport connection efficiency.
7. Diagnosis and repair of deliberately damaged production chains.
8. Multi-stage science production and demand-driven expansion.

## Implementation milestones

1. **Contracts and generator**: complete offline in `training/`.
2. **Surface provisioner**: create, seed, validate, and recycle one training
   surface through explicit mod commands.
3. **Deterministic baseline**: reuse current layout primitives to emit at least
   two legal candidates per mining scenario.
4. **Episode runner**: execute candidates and write transition records outside
   the save.
5. **First policy**: contextual bandit over candidate features; the baseline
   remains available as fallback.
6. **Shadow evaluation**: compare policy choices against the deterministic
   real-base choice without granting mutation authority.
7. **Bounded real-base authority**: enable only decisions whose held-out
   curriculum score exceeds the baseline and whose confidence gate passes.

The contracts, separate training-lab mod, deterministic candidate compiler,
episode runner, durable store, contextual policy, evolutionary population,
promotion gate, scheduler, and bounded local-model proposal layer now exist.
They have no real-base authority. See `docs/33_training_architecture.md`.

Validate the default hundred scenarios and two candidates per scenario without
starting Factorio:

```powershell
python tools/run_training_batch.py
```

Model a thousand offline attempts:

```powershell
python tools/run_training_batch.py --count 100 --attempts-per-scenario 10
```

Live batches require explicit worker configuration, unique ports and
`script-output` directories, the separate training mod, and an RCON password in
an environment variable. Start with one worker; increase concurrency only after
UPS and report latency remain healthy.
