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
