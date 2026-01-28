# System Invariants

Location: docs/20_system_invariants.md  
Purpose: Define the non-negotiable rules of the system.

This document specifies the **invariants** of the Factorio Cursor RL Agent.
An invariant is a rule that must always hold true, regardless of scale,
feature set, execution mode, or future extensions.

If an implementation violates an invariant, it is considered incorrect,
even if it appears to work.

---

## 1. Architectural Invariants

### 1.1 Separation of Concerns

- Planning is symbolic and deterministic
- Execution is reactive and time-optimized
- Supervision prioritizes stability over optimality

These concerns must never be merged.

Specifically:
- RL must never plan layouts, blocks, rails, or routes
- Lua must never perform optimization or long-horizon reasoning
- Supervisory logic must not issue low-level commands directly

---

### 1.2 Hierarchical Planning Boundaries

Planning layers are strictly ordered:

1. LocalLayoutPlanner
2. CityPlanner
3. PlanetPlanner
4. InterplanetarySupervisor

Rules:
- Lower layers may not override higher-layer constraints
- Higher layers may not inspect entity-level details
- Information flows upward; constraints flow downward

Cross-layer shortcuts are forbidden.

---

## 2. Determinism Invariants

### 2.1 Deterministic Planning

Given the same:
- Snapshot
- Goal
- Configuration

The planner must always produce the same output.

Forbidden:
- Randomized layout selection
- Non-seeded randomness
- Time-dependent logic in planners

---

### 2.2 Save/Load Safety

- All persistent state must live in Lua `storage`
- No mutable global Lua variables across ticks
- No reliance on load order side effects

If save/load breaks behavior, the implementation is invalid.

---

## 3. Layout & City Invariants

### 3.1 Layout Primitives

- Layouts are parametric programs, not images
- Layouts must declare throughput, footprint, and interfaces
- Layouts must be tile-aligned and replicable

Ad-hoc layouts are forbidden.

---

### 3.2 Block Immutability

Once a block is deployed:
- Its footprint cannot change
- Its rail interfaces cannot change
- Its internal layout cannot be partially modified

Scaling is achieved only by:
- Replication
- Addition of new blocks

Never by resizing or editing an existing block.

---

### 3.3 Inter-Block Transport

- No belts across block boundaries
- No bots across block boundaries
- All inter-block transport is rail-only

Exceptions are not allowed.

---

## 4. Rail Invariants

### 4.1 Rail-First Principle

At city scale and above:
- Rails are first-class infrastructure
- Belts are local only
- Bots are local only

Any design that depends on long-range belts or bots is invalid.

---

### 4.2 Rail Template Enforcement

- All rails use approved blueprints
- All signaling is template-based
- No free-form signal placement

The agent must never invent rail geometry.

---

### 4.3 Future Capacity Reservation

- Rail corridors must reserve space for future lanes
- Reserved space must remain clear
- Upgrades must not disrupt existing traffic

This applies even if the future lane is never used.

---

## 5. Execution Invariants

### 5.1 Execution Scope

The executor may:
- Move entities
- Place approved ghosts
- Craft required items
- Optimize timing

The executor may NOT:
- Change plans
- Modify layouts
- Alter rail infrastructure
- Bypass build phases

---

### 5.2 Phase Compliance

All execution must follow approved phases:
- Reservation
- Core infrastructure
- Attachment
- Activation

Skipping phases is forbidden.

---

## 6. Time Invariants

### 6.1 Time Representation

- All time is represented in Factorio ticks
- Any conversion must be explicit and reversible

Mixing time units implicitly is forbidden.

---

### 6.2 Latency Awareness

At planetary and interplanetary scale:
- Latency is a first-class constraint
- Throughput optimization must not ignore latency

The system must prefer predictability over raw throughput.

---

## 7. External Knowledge Invariants

### 7.1 Safety First

- External layouts are data, not code
- No executable code is ever imported
- All external layouts must be validated

---

### 7.2 Standards Supremacy

External layouts:
- May augment layout libraries
- May never override core standards
- May never violate block or rail invariants

Popularity does not imply correctness.

---

## 8. Supply Chain Invariants

### 8.1 Stability Over Optimality

When trade-offs exist:
- Stability wins over throughput
- Predictability wins over efficiency
- Resilience wins over speed

---

### 8.2 Failure Is Expected

The system must assume:
- Routes will fail
- Platforms will be lost
- Supply disruptions will occur

Designs that assume perfect operation are invalid.

---

## 9. Change Invariants

### 9.1 No Silent Changes

- Structural changes must be explicit
- Schema changes must be versioned
- Behavior changes must be documented

Silent fixes are forbidden.

---

### 9.2 Least Invasive Principle

When modifying the system:
- Prefer minimal change
- Preserve existing behavior
- Avoid cascading redesigns

---

## 10. Enforcement

If an invariant is violated:
- The agent must stop
- The issue must be reported
- The user must be consulted if ambiguity exists

Working code that violates invariants is considered incorrect.

---

## 11. Summary

These invariants define the identity of the system.

They ensure the agent behaves like:
- An industrial planner
- A logistics supervisor
- A civil engineer

Not a heuristic-driven bot.

If a feature cannot be built without breaking an invariant,
the feature must be redesigned or rejected.
