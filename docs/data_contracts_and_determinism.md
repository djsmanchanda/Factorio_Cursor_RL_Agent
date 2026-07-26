# Path: docs/data_contracts_and_determinism.md
# Purpose: Compact reference for state ownership, schemas, persistence, and deterministic behavior.

# Data Contracts and Determinism

## State ownership

### Snapshot data

The Lua mod exports deterministic JSON snapshots. They contain data only: no
functions, metatables, or runtime-only objects.

### Planner state

Planner state is ephemeral and recomputed from the current snapshot, goal, and
configuration. Progress and metrics are read-only derived views.

### Execution state

Persistent Factorio-side state lives in Lua `storage`. Python reports and
server-side `script-output` artifacts are external observations, not hidden
planner state.

## Canonical contracts

Inter-layer communication uses versioned schemas in `schemas/`, including:

- `snapshot.schema.json`: Lua to planner world state.
- `goal.schema.json`: instruction or user goal to planner.
- `build_plan.schema.json`: planner to Lua executable plan.
- `block.schema.json`: city/block planning contract.

Schemas define truth. Documentation describes intent. On mismatch, reject or
follow the schema and update the documentation; never silently widen a
contract.

## Determinism rules

Given the same snapshot, goal, and configuration, planners produce identical
output. They must not depend on wall-clock time, iteration order, unseeded
randomness, or incidental live-server state.

All time in planning contracts is Factorio ticks. Conversions to seconds or
other units must be explicit and reversible.

## Save/load rules

- Mutable Lua state belongs in `storage`.
- No mutable module/global state may be required across ticks or reloads.
- Load-order side effects are forbidden.
- Deploy/restart boundaries must be explicit when Lua changes are involved.

## Validation rules

Every boundary validates its input and output. Derived metrics must exist before
planning or supervisory decisions use them. If a required metric, capability,
schema field, or live fact is unavailable, the system fails closed and reports
the missing evidence.

## Change rules

Structural, schema, and behavior changes are explicit and documented. Prefer
the least invasive change that preserves existing behavior. External game
knowledge is data-only and must be validated before use; it cannot override
system invariants or approved standards.
