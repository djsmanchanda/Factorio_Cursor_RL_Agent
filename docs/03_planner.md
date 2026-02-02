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
