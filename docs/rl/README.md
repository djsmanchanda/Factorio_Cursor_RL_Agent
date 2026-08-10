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
