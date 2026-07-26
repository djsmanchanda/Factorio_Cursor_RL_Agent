# Path: docs/architecture.md
# Purpose: Compact durable description of the system layers, responsibilities, and runtime flow.

# Architecture

The system turns high-level production or research goals into deterministic,
authorization-gated Factorio build plans and then executes them against a
selected surface and force.

## Runtime flow

```text
goal -> instruction/goal schema -> observer -> metrics -> supervisor
     -> named planner -> validated BuildPlan -> authorization -> Lua/GameBridge
     -> snapshot/report -> next cycle
```

The observer and metrics layers are read-only. The supervisor generates intents
and chooses among planner-produced actions. Planners decide structure.
Executors perform approved actions and may optimize timing only.

## Layers

### Lua control layer

The Factorio mod owns snapshots, command registration, entity/ghost placement,
construction reports, and persistent state in `storage`. Lua does not optimize,
compute production ratios, or make long-horizon decisions.

### Python planning layer

Python validates inputs, computes requirements and derived metrics, selects
deterministic layout primitives, composes build plans, and enforces readiness
and authorization gates.

### Execution layer

`GameBridge` transports authorized commands to the mod. The execution agent,
heuristic, or future RL policy may move, craft, place approved entities, request
items, and schedule work. It may not change a plan, layout, rail standard, or
build phase.

### Scale hierarchy

- `LocalLayoutPlanner`: entity-level layouts inside a local block.
- `CityPlanner`: blocks, zoning, stations, and rail corridors.
- `PlanetPlanner`: planetary roles, imports, and exports.
- `InterplanetarySupervisor`: global flows, latency, risk, and recovery.

Constraints flow downward; observations and abstract metrics flow upward. A
lower layer cannot override a higher-layer constraint, and a higher layer does
not reach into entity-level details.

## Real-base and sandbox surfaces

The synthetic `planner-sandbox`/`planner` path is a deterministic proving
ground. The real-base path must explicitly use the intended surface and force,
normally `nauvis`/`player`, and must route around existing infrastructure.
Neither path may silently be substituted for the other.

## Scale transition

Local layouts use grids and lines. At city scale, production becomes immutable
blocks connected only through approved rail corridors. At planetary and space
scale, each planet or platform is a planning node and inter-node transport is a
supervised logistics graph. See the city, rail, block, and space documents for
those domain contracts.

## Authority

`AGENTS.md` defines how agents work in this repository. `docs/20_system_invariants.md`
defines non-negotiable behavior. Schemas define inter-layer data truth. This
document explains structure and intent; it does not override those sources.
