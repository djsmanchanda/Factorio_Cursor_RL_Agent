# Path: docs/22_rl_decision_layer.md
# Purpose: Architecture of the RL decision layer: what RL decides, over what action space, with what rewards. User-defined 2026-07-18.

# RL Decision Layer
## Current implementation status

This document defines a future execution-policy boundary, not the active
runtime. The disconnected first-generation advisor and sandbox daemons are
quarantined under `experimental/legacy_autonomy/`. The active Nauvis builder
remains deterministic and does not import that package. Revival requires an
explicit architecture review and must preserve the invariants below.

The second-generation training path now begins under `training/`. Its first
offline milestone defines strict scenario and transition contracts plus a
seeded mining-delivery curriculum. It does not yet provision Factorio surfaces,
execute episodes, persist experience, or update a policy, and it has no
real-base authority. See `docs/32_training_curriculum.md`.

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

## Force isolation (REQUIRED for the reward to exist)

The agent's factory MUST run on its own Factorio force with its own tech
tree. Measured on a completed megabase save, research-per-time is identically
zero — every finite technology is already researched, and a researched tech
cannot even be un-researched (Factorio reverts it to keep prerequisites
consistent with its researched successors). The terminal reward would be
silently dead.

Bootstrap (verified live 2026-07-18): create the force, friend it with
"player", reassign every sandbox entity to it (electric networks are
per-force, so power scaffolding must convert too), and mark the
`automation-science-pack` trigger technology researched — Space Age gates the
whole tree behind it, and a fresh force cannot queue anything without it.
Result: 15 automation-pack technologies available, and infinite technologies
beyond them, so the reward is attributable and unbounded.

## Training mechanics (implementation plan)

1. Log transitions from isolated episodes: (observation, candidate catalog,
   chosen action, staged reward, next observation). The versioned contracts now
   exist; surface execution and durable transition storage are the next slice.
2. Start with a deterministic baseline policy (greedy bottleneck-relief) so
   the system works before learning does; RL must beat it to earn trust.
3. First learned policy: contextual bandit / linear over the observation
   vector (small, inspectable). Neural policies only when the catalog and
   observation stabilize.
4. RL output remains a proposal into the existing authorization gates.
