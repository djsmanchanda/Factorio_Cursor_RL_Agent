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
