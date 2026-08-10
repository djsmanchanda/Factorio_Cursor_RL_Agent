# Path: docs/planner_and_execution.md
# Purpose: Compact reference for deterministic planning, layout primitives, execution, and RL boundaries.

# Planning and Execution

## Planner inputs and outputs

The planner consumes a snapshot, goal/intent, target delta, area constraints,
and explicit optimization preferences. It emits validated layout/build plans
and construction dependency graphs.

Planning is deterministic: the same snapshot, goal, and configuration produce
the same result. Planning readiness must be explicit; no plan is emitted when a
required capability or prerequisite is unavailable.

## Recipe knowledge and execution scope

`/export_recipe_catalog` exports every known force recipe in stable order and
preserves each recipe's `enabled` state. `core.science_recipe_graph` uses that
catalog to retain structural dependency knowledge for all twelve Space Age
science packs, including locked, coproduct, probabilistic, and alternative
routes. Structural knowledge does not imply a deterministic throughput promise.

Executable exact-rate graphs remain stricter: disabled, unsupported,
probabilistic, unbalanced coproduct, and unresolved ambiguous paths fail closed.
A same-named recipe is the deterministic primary unless an explicit recipe
preference selects another producer; structural graphs retain all non-recovery
alternatives.

The current real-base builder is Nauvis-only. All six Nauvis sciences are in
planning scope, but only targets whose complete stage chain exists in
`planners.recipe_data.LINE_RECIPES` are executable (currently automation, logistic, and chemical science).
Chemical science uses a compact real-base oil cell with water-safe pipe routing. Production,
utility, and military science remain structurally known but fail readiness until
their missing stages are implemented.
Space, planet-specific, and promethium science remain knowledge-only until the
relevant PlanetPlanner or interplanetary capability exists.

## Progress and phasing

`ProgressState` is a read-only summary derived from snapshots, metrics, and
prior intents. `BuildIntent` describes the ultimate target. A `GhostPlan` or
phase plan materializes that target incrementally.

Phasing changes construction order, not the target or geometry. Typical phases
are reservation, infrastructure, attachment, and activation. Phase advancement
requires authorization and measured readiness.

## Layout primitives

Layouts are parametric, tile-aligned programs rather than images. Every layout
declares throughput, footprint, scaling axis, and input/output interfaces.

- Grid layouts tile repeated production with a fixed footprint.
- Line layouts repeat a constant cross-section for smelting, fluids, buses, or
  other linear chains.
- Blocks package local layouts behind fixed rail/station interfaces.
- Rail corridors are approved templates with reserved future capacity.

Local layouts may use belts and bots. Inter-block transport is rail-only once
city rules apply. Deployed blocks are immutable; scaling adds blocks or
replicates them rather than editing them in place.

The current real-base extraction layout operates in local mode: drills plus a
short ore egress remain on the resource patch, while the electric-furnace line
gets an independent footprint whose full entity bounds and five-tile apron are
live-checked as resource-free. Furnace count includes the force's measured
mining-productivity bonus. Each refinery evaluates eastbound and westbound flow:
the input interface first faces its mine, while the output interface favors the
declared downstream reference; plate output uses one powered side collector on
a continuous belt. Mine-to-smelter links are capped at 300 generated route
tiles and preflighted before the smelter is submitted; a farther legal
site fails closed for a future CityPlanner rail handoff rather than leaving a
disconnected block. Built, ghosted, and partially constructed extraction
stages are reconciled on retry. Extraction capacity is phased across the
entire resource system at 6, 20, 50, and 100 total drills. Each
intervention builds the largest next-phase batch that fits the current
reserved corridor. A new mine inherits the remaining system target instead
of restarting at six; if a patch is smaller, later corridors retain the unmet
target. This is not City Mode.

Compact parts-mall providers use item-group inventory bars: production items
retain at least four stacks, logistics items five, and intermediate products
ten. A larger stock target expands the same provider using the item's live
stack size; it does not allocate an unrestricted chest. Blueprint stock targets
and live requester demand are combined. Whenever that demand would occupy more
than half the allowed inventory, capacity grows in two-stack steps until at
least half remains as shock reserve.

## Observer, supervisor, and executor

1. Observer exports state and computes deterministic metrics.
2. Supervisor detects stress, validates prerequisites, and emits intents.
3. Named planners compile intents into approved structure.
4. Executor materializes only approved actions and reports actual results.

RL is advisory or execution-focused. It may choose when to expand and which
planner-produced action to execute, but it never designs layouts, blocks, rails,
routes, or structural repairs.

## Action catalog

Planner-produced actions may include line extension, parallel expansion, tier
upgrade, or opening a resource chain. Each action has predicted effects and
must pass authorization. A deterministic bottleneck-relief baseline should
work before any learned policy is trusted.

## Instruction boundary

FIL or another goal interface must compile into structured schema input. It is
not allowed to bypass intent routing, capability resolution, readiness checks,
schema validation, or authorization.

## Failure behavior

Preflight is useful but does not prove Factorio acceptance. Live execution must
distinguish attempted from successful actions, surface entity-level failures,
and fail closed on ambiguity. A real-base executor routes around existing
player infrastructure and clears only approved neutral clutter.
