# Path: docs/22_rl_decision_layer.md
# Purpose: Architecture of the RL decision layer: what RL decides, over what action space, with what rewards. User-defined 2026-07-18.

# RL Decision Layer

## Division of labor (invariant-compatible)

- **Deterministic planners** own all structure: layouts, geometry, ratios,
  belt routing, pipeline composition. They compile goals into build plans and
  publish an **action catalog** of executable expansion options with
  predicted effects.
- **RL** owns the *decision policy*: WHEN to expand and WHICH catalog action
  to take. It never draws a layout; it chooses among symbolically planned,
  authorization-gated actions.
- **Authorization gates remain** on every executed action.

## What RL decides

1. **When to increase production** — act now vs consolidate.
2. **How to increase production**, choosing among catalog actions:
   - Linear expansion: more machines on an existing line (X axis)
   - Area expansion: parallel lines (Y axis), new production blocks
   - Efficiency/density: belt tier, inserter tier, machine tier, quality
     tier upgrades on existing lines
   - Resource expansion: open a new mine, build the full pipeline
     (mine → smelt → assemble) for a missing input
3. **Which research to choose and when** (final-objective coupling).

## Observation space (deterministic diagnosis feeds the policy)

- Production rates per item (measured, per line and aggregate)
- **Bottleneck verdict per line**: machine-limited / feed-limited /
  drain-limited / input-starved / resource-depleted / space-limited
- Resource headroom: ore patch remaining, buildable area, material stocks
  (machines, belts, inserters available to bots)
- Research state: current tech, science pack consumption vs production
- Goal queue state (below)

## Goal stacking

Goals are declarative targets (product, rate), queued hierarchically:
step A → step B → step C. The planner compiles each goal into build intents
and catalog actions; RL sequences and schedules them. Sub-goals spawned by
resource constraints (need copper → open copper mine → smelt → cable line)
nest under their parent goal.

## Reward stages

1. **Stage 1 — throughput shaping**: delta in items/s for targeted products.
2. **Stage 2 — pipeline completion**: first production of a new product
   type; full chains (ore→product) worth more than infinity-fed lines.
3. **Stage 3 — science throughput**: science packs/s produced and consumed.
4. **Final — research maximization**: research completed per unit time,
   including the value of WHICH tech was chosen (tech-tree-aware).

Earlier stages shape; the final stage dominates as capability grows.

## Curriculum (user-defined ordering)

- **N1 (current): Nauvis core mechanics** — lines, chaining, mining
  pipelines, sideload feeding, X/Y scaling, tier upgrades.
- **N2: Full science chain** — ore to automation-science to labs, research
  actually progressing; first end-to-end reward signal.
- **N3: Multi-science, multi-pipeline Nauvis base** — goal stacking at scale.
- **S1: Space travel** — platforms, launches.
- **S2: Planet production** — per-planet ore and environment constraints
  (turbo belts from Vulcanus, stack inserters from Gleba, etc.).
- **S3: Interplanetary supply** — transporting finished/semi-finished
  products between planets; PlanetPlanner + InterplanetarySupervisor era.

## Training mechanics (implementation plan)

1. Log transitions from live cycles: (observation, catalog action, staged
   reward, next observation) — the daemon already emits observation-shaped
   status traces; rewards computed from measured rate deltas.
2. Start with a deterministic baseline policy (greedy bottleneck-relief) so
   the system works before learning does; RL must beat it to earn trust.
3. First learned policy: contextual bandit / linear over the observation
   vector (small, inspectable). Neural policies only when the catalog and
   observation stabilize.
4. RL output remains a proposal into the existing authorization gates.
