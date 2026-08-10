# Space & Multi-Planet Planning (Factorio 2.0 / Space Age)

This document defines how the system scales beyond a single planet
into a multi-planet industrial supply chain.

Each planet, platform, and space route is treated as a first-class
planning entity.

---

## 1. Core Abstraction Shift

The system evolves from:

Factory → City → Planet → Interplanetary Network

Each level is planned independently but supervised globally.

---

## 2. Planet as a Super-Block

A planet is modeled as a high-level block.

Planet properties:
- Resource availability
- Environmental constraints
- Hostile pressure
- Travel time to other bodies
- Import/export capacity

Example:

{
  "planet_id": "vulcanus",
  "role": "smelting_heavy",
  "exports": ["iron-plate", "steel"],
  "imports": ["circuits", "modules"],
  "threat_level": "high"
}

## 3. Planet-Level Zoning

Each planet internally uses City Mode:
- Block-based districts
- Rail-native logistics (if applicable)
- Local optimization

Interplanetary logistics NEVER bypass planet-level planners.

## 4. Space Platforms

Space platforms are mobile production and logistics nodes.

Platform roles:
- Transport
- Refining
- Defense
- Buffering
- Emergency response

Properties:
- Cargo capacity
- Fuel type
- Acceleration profile
- Defense loadout
- Autonomous vs assisted propulsion

## 5. Propulsion Models

The planner evaluates propulsion strategy:

### Fully Self-Propelled
- Independent fuel & power
- Long-range autonomy
- Higher build cost

### Partially Assisted
- Planet-launched boosts
- Refueling stations
- Lower mass efficiency

### Route-Dependent
- Gravity assists
- Fixed orbital lanes
- Lower fuel cost, higher latency

These are explicit planner decisions, not heuristics.

## 6. Interplanetary Logistics Graph

All logistics form a directed graph:

Nodes:
- Planets
- Space platforms
- Orbital stations

Edges:
- Routes with time, fuel, and risk costs

Planner objectives:
- Minimize supply latency
- Avoid bottlenecks
- Maintain buffer margins
- Survive hostile events

## 7. Time as a First-Class Constraint

Unlike planetary logistics:
- Space logistics is time-dominated
- Latency matters more than throughput

The planner reasons in:
- Ticks
- Transit windows
- Refill cycles
- Risk exposure duration

All time values are expressed in Factorio ticks.

Any conversion to seconds, minutes, or cycles
must be explicit and reversible.

## 8. Defense Planning

Defense is treated as logistics.

Defense constraints:
- Platform survival probability
- Escort availability
- Replacement lead time

The planner may:
- Reroute supplies
- Delay expansion
- Overbuild redundancy

## 9. Failure Modes & Recovery

Expected failures:
- Platform loss
- Route disruption
- Planet isolation

The system reacts by:
- Falling back to buffers
- Reprioritizing research
- Dispatching replacement assets

No failure is assumed catastrophic by default.

## 10. Supervisory Role of the Agent

At this level, the agent:
- Monitors flows
- Detects supply chain stress
- Issues corrective actions
- Escalates restructuring when needed

Execution remains local.
Supervision is global.
