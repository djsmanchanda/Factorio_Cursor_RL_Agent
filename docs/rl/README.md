<!-- Path: docs/rl/README.md | Purpose: Define the RL system, including learned structural planning. -->

# Laya learning and decision system

## Goal

Build a fast local Laya director that tries, measures, learns from failure, and
retries without needing a human to encode each mistake as a new controller
branch. It must operate a self-expanding factory across unseen seeds and base
states, not merely rank deterministic blueprints or imitate the existing
cascade.

The canonical implementation direction is
`docs/rl/Laya_director_final_plan.md`.  The existing contextual-bandit policy
and deterministic controller are migration baselines.  The training runtime,
reward/evidence store, held-out evaluation, and promotion gates remain the
learning system around Laya.

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

Reuse live game facts, schemas, validators, geometry realization, execution,
and measurements where they are genuinely generic.  Keep the deterministic workflow usable until Laya outperforms it under matched
conditions. Frozen snapshots support evaluation and rollback. Inside Laya, the
reused toolkit must not be the hidden source of goal ordering, strategy, site,
scale, wait, or recovery choices.

Seed-specific tooling may compile legal alternatives.  Laya chooses among
those alternatives using the live state, goal stack, failure memory, and
versioned guideline pack.  Validators may reject an illegal choice, but they
may not replace it with their own preferred strategy.

The first implementation slice is available in `orchestrator/laya_operations.py`,
`orchestrator/laya_state.py`, `orchestrator/laya_guidelines.py`,
`orchestrator/laya_decision_telemetry.py`, `training/laya_policy.py`,
`training/laya_dataset.py`, and `training/laya_finetune.py`.  It is currently
disposable/shadow infrastructure; real-Nauvis authority remains disabled until
the M1 integration and M3 held-out gates pass.

The full-loop data seam is available in `training/laya_workflow.py`: workflow
traces can label goal, operation, site, scale, route, research, upgrade,
recovery, and wait/act decisions. The Factorio mod's **Laya Coach** window
exports bounded observations and optional human labels for future fine-tuning;
it is intentionally read-only and never executes a model action. Deterministic
disposable episodes also preserve staged decision traces as evidence; those
traces teach the model what happened without giving the deterministic cascade
strategic authority.

The RTX 4060 optimization decision and queued hypothesis matrix are documented
in `docs/rl/Laya_Optimization_Decision.md`.

The retired `experimental/legacy_autonomy/` implementation may supply ideas or small compatible utilities after review. It is never imported wholesale into the active training runtime.

## Production promotion

Promotion requires zero safety violations, held-out success, non-regression on earlier curriculum families, reproducible lineage, and a measurable advantage over simple and deterministic baselines. Real-base trials begin in shadow or narrowly bounded mode and retain rollback.

The active implementation and coexistence plan is
`Laya_Implementation_Plan.md`. Laya has a separate opt-in runner; the existing
deterministic entry points and presentation remain intact.

The [saved-world adapter](Laya_Saved_World_Adapter.md) adds isolated exact-copy
`mod_playground` observations and funded bot construction. Its live mechanics
replays are verified; saved-world Laya training and matched baseline comparison
remain separate gates.

The [supplied-input mall](Laya_Mall_Supply.md) provides a separate seeded economy,
queue-selected consumed-batch supply upgrades and long-inserter-funded quad
expansion. Its live mechanics are verified; it uses `mall-world-v1` and still
requires its own measured learning/calibration and baseline comparison.
