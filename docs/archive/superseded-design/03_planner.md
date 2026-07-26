# Factory Planner

The planner treats the factory as a graph:

- Nodes: production blocks
- Edges: item flows
- Constraints: space, power, logistics

Inputs:
- Current factory snapshot
- Target production delta
- Area constraints
- Optimization preferences

Outputs:
- Layout selection
- Blueprint replication plan
- Construction dependency graph

## Progress State

Progress State is a read-only summary of where the factory is versus target.
It is derived from snapshots, metrics, and prior intents.
Progress State does not modify plans; it provides context for deterministic planning.

## Capacity Phasing

Planning targets the ultimate capacity (e.g., 1000), while construction proceeds
in deterministic phases (e.g., 50 → 100 → 250 → 1000).
Phasing realizes a single plan without redesign or re-planning.

## BuildIntent and GhostPlan

BuildIntent represents the ultimate intent of what should exist.
GhostPlan represents the current materialization as ghost-only artifacts.
Early-game efficiency choices are encoded as policy, not heuristics.

## City-Level Planning

At city scale, the planner operates on blocks instead of entities.

Responsibilities:
- Block placement
- Rail corridor routing
- Station allocation
- Flow balancing

Local planners operate inside block boundaries only.

## Hierarchical Planning Stack

Planning occurs at multiple levels:

- Local Layout Planner
- City Planner
- Planet Planner
- Interplanetary Supervisor

Each level consumes outputs from the level below
and imposes constraints from above.

## Derived Metrics Layer

Before any planning or decision-making occurs, the system computes
derived metrics over the FactoryGraph.

Metrics include:
- Bot workload and density
- Production structure counts
- Power producer/consumer structure
- Resource extraction structure

Metrics are:
- Deterministic
- Read-only
- Used by supervisory policies

Planners must not make decisions without consulting metrics.

## Intent Routing

Planners do not act directly on intents.
Intents are first routed to determine which planning
capabilities are required and whether prerequisites exist.

## Capability Resolution

After intent routing, the planner resolves whether the
required planning capabilities are available and what
prerequisites are missing before planning can begin.

## Planning Readiness Gate

Before any planner generates plans, a readiness gate
verifies that required capabilities are available and
prerequisites are satisfied.

No planning occurs without an explicit READY decision.
