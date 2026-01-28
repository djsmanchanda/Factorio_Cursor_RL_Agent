--- FILE: README.md ---

# Factorio Autonomous Planning Agent

An autonomous planning + execution system for Factorio that can:
- Understand high-efficiency grid and line layouts
- Scale production symbolically
- Auto-prioritize construction zones
- Execute plans efficiently (eventually via RL)

This is **not** an end-to-end RL bot.
It is a factory compiler with an execution agent.

Core philosophy:
> Planning is symbolic. Execution is learned.



--- FILE: docs/00_description.md ---

# Project Description

This project aims to build an autonomous agent for Factorio that can be
prompted with high-level production goals and execute them deterministically
and efficiently.

Example goals:
- Increase electronic circuit output by 1000/s in a given area
- Scale an existing smelting line vertically
- Replicate a high-efficiency grid layout N times
- Optimize for speed, power, or UPS

The system decomposes the problem into:
1. Goal interpretation
2. Factory planning and layout synthesis
3. Construction prioritization
4. Low-level execution



--- FILE: docs/01_architecture.md ---

# System Architecture

The system is split into three layers:

## 1. Lua Control Layer (Factorio Mod)
Responsibilities:
- Export world state
- Place ghosts and entities
- Track construction progress
- Maintain save/load safety

Lua is intentionally kept "dumb".

## 2. Planner / Compiler (Python)
Responsibilities:
- Parse factory state
- Compute production requirements
- Select layout primitives
- Generate build plans and blueprints

This layer is deterministic and testable.

## 3. Execution Agent (RL / Heuristic)
Responsibilities:
- Move player
- Manage inventory
- Place entities efficiently
- Optimize time to completion

This layer does NOT decide what to build.

## City-Scale Planning Layer

Once the factory reaches a defined scale threshold,
the planner switches to City Mode.

In City Mode:
- The planner operates on blocks, not entities
- Rail corridors become first-class objects
- Layout planning is hierarchical:
  City → Block → Local Layout

This layer sits above the factory planner and
feeds it block-level constraints.

## Planetary & Space Layer

Above City Mode, the system introduces:

- Planet-level planners
- Interplanetary logistics supervision
- Space platform management

Each planet is autonomous locally
but coordinated globally.

## Named Planning Components (Authoritative)

To avoid ambiguity, the following component names are canonical:

- LocalLayoutPlanner: operates on entities and layout primitives
- CityPlanner: operates on blocks and rail corridors
- PlanetPlanner: operates on planetary roles and imports/exports
- InterplanetarySupervisor: monitors and stabilizes global flows

Logic must not cross component boundaries.



--- FILE: docs/02_agent.md ---

# Agent Design

The agent is hierarchical.

## High-Level Planner
- Deterministic
- Symbolic
- Math-based

## Low-Level Executor
- Reactive
- Time-optimized
- Trained via imitation / RL

The agent never reasons about recipes or ratios.
Those are fixed, external knowledge.

## Supervisory Intelligence

At large scale, the agent shifts from planning to supervision.

Responsibilities:
- Detect supply chain stress
- Coordinate planetary roles
- Manage time-latency tradeoffs
- Maintain resilience over optimality



--- FILE: docs/03_planner.md ---

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



--- FILE: docs/04_layout_primitives.md ---

# Layout Primitives

Layouts are parametric programs, not images.

## Types

### Grid Layout
- 2D tiling
- High density
- Used for beaconed production

### Line Layout
- Linear repetition
- Constant cross-section
- Used for smelting, oil, buses

## Properties
Each layout defines:
- Throughput per unit
- Footprint
- Preferred scaling axis
- Input/output interfaces

## City-Level Primitives

Beyond local layouts, the system defines:

### Block Primitive
- Fixed footprint
- Rail interfaces
- Throughput contracts

### Rail Corridor Primitive
- Directional lanes
- Reserved future capacity
- Template-based signaling

These primitives are composed to form the city grid.



--- FILE: docs/05_lua_integration.md ---

# Lua Integration

The Lua mod exposes:

- Entity snapshots
- Construction APIs
- Zone prioritization
- Progress tracking

All persistent state is stored in `storage`.

Lua never:
- Performs optimization
- Computes ratios
- Makes long-horizon decisions



--- FILE: docs/06_execution_and_rl.md ---

# Execution and RL

RL is used only for execution efficiency.

## State
- Player position
- Inventory
- Nearby ghosts
- Bot availability
- Zone completion

## Actions
- Move
- Craft
- Place entity
- Request items

## Reward
- Negative ticks to completion
- Penalty for rework
- Bonus for zone completion

## City Execution Constraints

The executor:
- Never edits rail corridors
- Never modifies deployed blocks
- Executes only approved build phases

RL is scoped strictly to execution efficiency,
never structural planning.



--- FILE: docs/07_instruction_language.md ---

# Instruction Language (FIL)

A minimal declarative language for factory intent.

Example:

goal electronic_circuit +1000/s
area rect(120,-40,260,80)
layout grid
scale vertical
priority speed
avoid trains

FIL is parsed into structured planner input.



--- FILE: docs/08_data_and_state.md ---

# Data and State Management

## Snapshot Data
- Exported as JSON
- Fully deterministic
- No functions or metatables

## Planner State
- Ephemeral
- Recomputed each request

## Execution State
- Stored in Lua `storage`
- Save/load safe

## Canonical Data Contracts

All inter-layer communication must use versioned schemas.

Documentation describes intent.
Schemas define truth.

If a mismatch exists:
- Schemas win
- Docs must be updated



--- FILE: docs/09_determinism_and_saveload.md ---

# Determinism and Save/Load Safety

Rules:
- All mutable state lives in `storage`
- No reliance on local Lua variables across ticks
- No random numbers without fixed seeds

This ensures:
- Multiplayer safety
- Replayability
- Debuggability



--- FILE: docs/10_checklist_todo.md ---

# Checklist / TODO

## Phase 0 – Foundation
- [x] Initial documentation suite
- [x] System invariants established
- [x] Canonical JSON schemas drafted
- [x] Agent operating instructions (agent.md)

## Phase 1 – MVP (LocalLayoutPlanner)
- [ ] Lua mod skeleton
- [ ] Area snapshot export (conform to `snapshot.schema.json`)
- [ ] Recipe DAG loader
- [ ] Single grid layout (Deterministic math)
- [ ] Blueprint replication (conform to `build_plan.schema.json`)

## Phase 2 – Execution (RL Optimization)
- [ ] Headless Factorio setup
- [ ] Instruction language (FIL) parsing (`goal.schema.json`)
- [ ] RL executor (Targeted at build efficiency)
- [ ] Reward function definition (Invariants-checked)

## Phase 3 – Scaling (CityPlanner)
- [ ] Block schema & template system
- [ ] Rail corridor automation (Invariants-based)
- [ ] Station-as-interface logic
- [ ] Incremental city migration scripts

## Phase 4 – Interplanetary (PlanetPlanner & Supervisor)
- [ ] Space platform orchestration
- [ ] Inter-planet logistics DAG
- [ ] Supply chain bottleneck dashboard
- [ ] Anomaly detection for supervision



--- FILE: docs/11_experiments.md ---

# Experiments

Planned experiments:
- Grid vs line UPS cost
- Vertical vs horizontal scaling
- Planner optimality vs human designs
- RL execution speed vs scripted execution



--- FILE: docs/12_future_work.md ---

# Future Work

- Multi-agent planners (power, logistics, science)
- Layout evolution and benchmarking
- Competitive speedrun agents
- Multi-objective optimization
- Visual factory diffing
- Adaptive city grid resizing
- Express rail lane activation
- Dynamic block relocation
- Multi-city federated factories
- Train priority classes (local vs express)
- Automated planet specialization discovery
- Adaptive platform routing under threat
- Interstellar supply chain optimization
- Self-healing logistics networks
- Cooperative multi-agent supervision

## Non-Goals (Permanent)

- End-to-end RL for planning
- Free-form rail routing
- Organic spaghetti growth
- Heuristic-only optimization
- Silent modification of deployed blocks



--- FILE: docs/13_city_planning.md ---

# City Planning & Rail-First Scaling

This document defines the transition from local factory scaling
(grids and lines) to city-scale planning using rail-based blocks.

City planning is activated once local scaling becomes inefficient
due to distance, congestion, UPS cost, or throughput targets.

---

## 1. City Mode Trigger

The planner enters City Mode when any of the following conditions are met:

- Total production blocks > threshold (e.g. 200)
- Any belt exceeds max length (e.g. 300 tiles)
- Average travel time between blocks exceeds limit
- Logistic bot network saturation detected
- Target science rate >= megabase tier (e.g. ≥1000 SPM)
- UPS cost per item increases beyond tolerance

Once triggered, the planner:
- Freezes organic expansion
- Switches to block-based zoning
- Enforces rail-only inter-block transport

This is a **mode switch**, not a gradual change.

---

## 2. City Abstraction

At city scale, the factory is treated as:

- A grid of production blocks
- Connected by reserved rail corridors
- With strict separation of concerns

Blocks are self-contained.
Rails are the only inter-block interface.

---

## 3. Block Types and Scales

### 3.1 Large Blocks (Macro Districts)

Used for:
- Smelting
- Circuits
- Oil processing
- Science production
- Mall / infrastructure

Typical size:
- 256×256 or 512×512 tiles

Properties:
- Train-native
- Internal belts and bots allowed
- Fixed station interfaces
- No belt connections outside the block

---

### 3.2 Small Blocks (Micro / Support)

Used for:
- Power
- Buffers
- Outposts
- Temporary or auxiliary production

Typical size:
- 64×64 or 128×128 tiles

Properties:
- May be attached to large blocks
- Limited rail access
- Often single-purpose

---

## 4. City Grid Layout

The city is laid out as a Manhattan-style lattice.

Example abstraction:

[B] = Block  
[R] = Rail Corridor

[B][R][B][R][B]
[R][ ][R][ ][R]
[B][R][B][R][B]

Key rules:
- Rail corridors are reserved even if unused
- Blocks never touch directly; rails separate them
- Grid spacing is fixed and global

This guarantees future scalability.

---

## 5. Rail Corridor Standard

Rail corridors are designed for long-term expansion.

### 5.1 Track Allocation

Canonical corridor cross-section:

[ Future Rapid ][ Outbound ][ Inbound ][ Service / Buffer ]

Initial construction:

[ EMPTY ][ ➡ ][ ⬅ ][ EMPTY ]

Properties:
- Fully directional tracks
- Tile-aligned
- Expandable without demolition
- No bidirectional signaling

---

### 5.2 Signaling Rules

Signaling is **template-based**, not inferred.

Rules:
- Chain signals before intersections
- Rail signals after intersections
- No mixed-direction tracks
- No stations on mainlines

Each corridor uses a pre-approved signal pattern.

---

## 6. Stations as Block Interfaces

Stations act as APIs between blocks.

Each block exposes:
- Input items
- Output items
- Max throughput
- Train length
- Max concurrent trains

Example:

{
  "block_id": "GC_DISTRICT_03",
  "inputs": ["iron-plate", "copper-plate"],
  "outputs": ["electronic-circuit"],
  "train_length": 4,
  "max_trains": 6
}

Stations are placed off the mainline
using standardized sidings.

---

## 7. Block Assignment & Zoning

Blocks are assigned positions based on:
- Dependency graph centrality
- Fan-in / fan-out
- Fluid requirements
- Pollution profile
- Expected growth rate

Examples:
- High fan-in → central city blocks
- Pollution-heavy → downwind edge
- Fluids → near water sources

This creates a block-level DAG.

---

## 8. Rail Routing Model

Rail routing is not free-form.

- Trains follow corridor graph edges
- No ad-hoc pathfinding
- Overtake lane reserved for future express traffic
- Routing decisions are planner-controlled

This minimizes congestion and UPS cost.

---

## 9. Construction Phases (City Build)

City construction happens in strict phases:

### Phase A — Reservation
- Clear land
- Place rail corridor ghosts
- Place corridor power

### Phase B — Core Rail
- Build mainlines
- Place signals
- Validate loops

### Phase C — Block Attachment
- Build blocks one at a time
- Validate throughput
- Only then allow replication

---

## 10. Internal Representations

City planning introduces three spatial layers:

1. Block Graph (abstract)
   - Nodes: blocks
   - Edges: rail flows

2. City Grid (geometric)
   - Tile coordinates
   - Reserved corridors

3. Local Layouts
   - Grid / line primitives inside blocks

Each layer is planned independently but executed together.

---

## 11. Why This Matters

This system allows:
- Predictable scaling to massive size
- Zero rework when expanding
- Express lanes without downtime
- Automatic congestion avoidance
- Deterministic rail behavior

This is not organic growth.
This is infrastructure planning.



--- FILE: docs/14_rail_blueprint_standard.md ---

# Rail Blueprint Standard

This document defines the canonical rail infrastructure used by the system.
All rail placement is template-driven. The agent never invents rail designs.

---

## 1. Design Goals

The rail system must be:
- Directional
- Predictable
- UPS-efficient
- Infinitely extensible
- Upgradeable without demolition

Rail infrastructure is treated as **permanent city infrastructure**.

---

## 2. Corridor Geometry

### 2.1 Canonical Cross-Section

From left to right:

[ Future Rapid Lane ][ Outbound Lane ][ Inbound Lane ][ Service / Buffer ]

Initial build:

[ EMPTY ][ ➡ ][ ⬅ ][ EMPTY ]

Tile width (example):
- Each lane: 2 rails + signals
- Total reserved width: fixed globally

Once defined, this width is never changed.

---

## 3. Directionality Rules

- Each rail lane has exactly one direction
- No bidirectional rails
- No lane switching outside junction templates
- Trains never reverse on mainlines

This simplifies signaling and routing logic.

---

## 4. Signaling Templates

Signaling is applied using **predefined templates**.

### 4.1 Straight Segment
- Rail signals at fixed block intervals
- Block length chosen for max train length

### 4.2 Intersection
- Chain signals before entry
- Rail signals on exit
- No station blocks inside intersections

### 4.3 Junction Types
- T-junction
- 4-way crossing
- Block entry/exit junction

Each junction has:
- A blueprint
- A known block graph effect
- A throughput estimate

---

## 5. Stations

Stations are **never placed on mainlines**.

Rules:
- Stations live on sidings
- Sidings reconnect after station
- No dead-ends on corridors

Station blueprints encode:
- Train length
- Stackers
- Entry/exit signaling

---

## 6. Upgrade Path (Rapid Lane)

The reserved rapid lane is activated by:
- Laying rails in the reserved space
- Applying express-only signal templates
- Restricting usage to high-priority trains

No existing traffic is interrupted.

---

## 7. Blueprint Naming Convention

Examples:

- RAIL_CORRIDOR_STRAIGHT_V1
- RAIL_JUNCTION_4WAY_V2
- RAIL_STATION_SIDING_L4_V1

Blueprints are immutable once released.
New versions are additive, never destructive.



--- FILE: docs/15_block_schema.md ---

# Block Schema

Blocks are the fundamental units of city-scale planning.
Each block is self-contained and interacts with the rest of the city
only through rail stations.

---

## 1. Block Definition

A block is defined by:

- Fixed footprint
- Internal layout
- Rail interfaces
- Throughput contract

Blocks are immutable once deployed.

---

## 2. Canonical Block Schema (JSON)

```json
{
  "block_id": "GC_DISTRICT_V1",
  "category": "production",
  "size": { "w": 256, "h": 256 },

  "inputs": [
    { "item": "iron-plate", "rate": 4000 },
    { "item": "copper-plate", "rate": 6000 }
  ],

  "outputs": [
    { "item": "electronic-circuit", "rate": 2000 }
  ],

  "stations": {
    "input": {
      "train_length": 4,
      "max_trains": 6
    },
    "output": {
      "train_length": 4,
      "max_trains": 6
    }
  },

  "power": {
    "mw": 420,
    "type": "electric"
  },

  "layout_type": "grid",
  "blueprint": "0eNq...",
  "internal_logistics": ["belt", "bot"],

  "constraints": {
    "no_external_belts": true,
    "no_external_bots": true
  }
}
```

## 3. Block Categories

- `production`
- `smelting`
- `fluids`
- `science`
- `infrastructure`
- `power`
- `buffer`

Category affects:
- Placement priority
- Zoning rules
- Rail access pattern

## 4. Block Interfaces

Blocks expose:
- Required inputs
- Guaranteed outputs
- Maximum throughput

The planner enforces contracts.
No implicit dependencies are allowed.

## 5. Replication Rules

Blocks can be:
- Replicated horizontally (new districts)
- Never resized in-place
- Never partially modified

Scaling = more blocks, not bigger blocks.

## 6. Failure Handling

If a block cannot meet its contract:
- Planner throttles downstream blocks
- No cascading deadlocks
- Optional buffer blocks inserted



--- FILE: docs/16_city_migration.md ---

# Migration to City Mode

This document defines how an existing factory transitions
into city-scale block-and-rail planning without breaking production.

---

## 1. Migration Philosophy

Migration is:
- Incremental
- Reversible
- Non-destructive

The system never tears down a working base blindly.

---

## 2. Migration Trigger

Once City Mode is activated:
- Organic growth is frozen
- New production must follow block rules
- Existing production is grandfathered

---

## 3. Step 1 — Snapshot & Classification

Planner:
- Takes a full factory snapshot
- Groups entities into proto-blocks
- Classifies production roles

Outputs:
- Candidate blocks
- External dependencies
- Throughput estimates

---

## 4. Step 2 — Rail Spine Deployment

Before touching production:
- Reserve city grid
- Lay main rail corridors
- Power the corridors
- Validate loops

No stations yet.

---

## 5. Step 3 — Shadow Blocks

For each major production area:
- Build a new block elsewhere
- Connect it via rail
- Ramp up output gradually

This avoids hard cutovers.

---

## 6. Step 4 — Traffic Migration

- Divert consumers to new blocks
- Drain old production naturally
- Verify stability

Only once stable:
- Decommission old layouts

---

## 7. Step 5 — Enforcement

After migration:
- External belts disabled
- Cross-block bots disabled
- All new production must be block-based

City Mode is now fully active.

---

## 8. Rollback Strategy

At any point:
- Stop migration
- Fall back to previous blocks
- Rail infrastructure remains usable

Migration failures are non-fatal.

---

## 9. Why This Works

This mirrors real infrastructure upgrades:
- Parallel systems
- Gradual load transfer
- Zero downtime

The agent behaves like a civil engineer,
not a speedrunner.



--- FILE: docs/17_space_and_multiplanet_planning.md ---

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



--- FILE: docs/18_supply_chain_supervision.md ---

# Supply Chain Supervision

This document defines how the agent supervises logistics across
cities, planets, and space routes.

The agent does not micromanage.
It enforces stability.

---

## 1. Supply Chain Model

The system maintains a global flow graph.

Nodes:
- Blocks
- Cities
- Planets
- Platforms

Edges:
- Belts
- Trains
- Space routes

Each edge has:
- Capacity
- Latency
- Reliability

---

## 2. Stress Detection

The agent continuously evaluates:
- Demand vs supply mismatch
- Rising buffer depletion rates
- Transport saturation
- Latency spikes

These trigger interventions.

---

## 3. Intervention Types

Possible actions:
- Reroute flows
- Increase buffer sizes
- Spin up new production blocks
- Dispatch platforms
- Delay downstream expansion

Interventions are prioritized by risk.

---

## 4. Optimization vs Stability

The system prefers:
- Stable supply
- Predictable flows
- Controlled expansion

Pure optimization is secondary to resilience.

---

## 5. Human-Level Analogy

The agent behaves like:
- A logistics supervisor
- A supply chain manager
- An infrastructure planner

Not a micromanaging worker bot.



--- FILE: docs/19_external_knowledge_and_layout_search.md ---

# External Knowledge & Layout Search

This document defines how the system leverages external optimized layouts
(similar to Cursor-style code search).

---

## 1. Motivation

Human-optimized layouts already exist:
- Megabase blueprints
- Speedrun designs
- UPS-optimized factories

The system should reuse knowledge, not rediscover it.

---

## 2. Layout Ingestion Pipeline

Sources:
- Blueprint strings
- Community repositories
- Curated datasets

Steps:
1. Import blueprint
2. Normalize orientation
3. Extract layout metadata
4. Benchmark in simulation
5. Store as layout primitive

---

## 3. Layout Evaluation

Each imported layout is scored on:
- Throughput
- Area efficiency
- Power usage
- UPS impact
- Compatibility with block rules

Only layouts passing constraints are promoted.

---

## 4. Layout Library

Layouts are versioned:

GC_GRID_COMMUNITY_V3
SMELTER_LINE_UPS_V2

The planner selects layouts based on context,
not popularity.

---

## 5. Safety Rules

The agent:
- Never downloads executable code
- Never executes unverified layouts
- Never modifies core rail standards

External knowledge augments, never overrides.



--- FILE: docs/20_system_invariants.md ---

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
