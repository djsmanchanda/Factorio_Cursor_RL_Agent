--- FILE: README.md ---

# Factorio Autonomous Planning Agent

An autonomous planning + execution system for Factorio that can:
- Understand high-efficiency grid and line layouts
- Scale production symbolically
- Auto-prioritize construction zones
- Execute plans efficiently (eventually via RL)

This is **not** an end-to-end RL bot.
It is a factory compiler with an execution agent.

## Live Mod Operations

- Start with the [Factorio mod interaction and troubleshooting runbook](docs/31_factorio_mod_interaction_and_troubleshooting.md) for RCON, `GameBridge`, autonomous-run boundaries, command inventory, and failures.
- Repository-local Codex guidance is inventoried in [`.agents/skills/README.md`](.agents/skills/README.md).
- Live mutation, mod deployment, saves/resets, and server lifecycle actions require explicit user authorization.

Core philosophy:
> Planning is symbolic. Execution is learned.

Additional principles:
- Progress State is read-only and derived from snapshot + metrics + prior intents
- Capacity Phasing realizes a fixed target capacity in deterministic stages
- BuildIntent represents ultimate intent; GhostPlan represents current materialization
- Early-game efficiency choices are policy-driven, not heuristic

Supervision:
- Capacity phasing is a supervisory policy that selects the next allowed capacity phase
	without executing or placing anything.
- Progress reconciliation compares observed ghosts with planned progress state
	and emits a read-only reconciliation status.
- Execution readiness proposes permitted next actions without executing them.
- Execution authorization is mandatory before any execution actions are allowed.
- Authorized ghost execution is limited to ghost placement only and remains sandboxed.
- Bot-assisted construction is limited to building sandbox ghosts and never places real entities directly.
- Construction progress updates current capacity without advancing phases.
- Phase advancement requires explicit proposal and authorization.
- Authorized upgrades are limited to planner-sandbox entities and require explicit approval.
- Authorized deconstruction is bot-mediated only and limited to planner-sandbox entities.

## Tooling

- Inspect Progress State:
	- `python tools/inspect_progress.py <snapshot.json> <metrics.json> <build_intent.json>`
- Phase-aware Ghost Projection:
	- Requires BuildIntent + ProgressState + CapacityPhasing (no CLI yet)
	- Target-aware slicing is read-only and deterministic: `GhostSlice` focuses projection by `target_block` and `target_recipe` using capacity allocation.
	- Projection emits only delta ghosts for the current slice; no geometry synthesis or Lua changes are introduced.
	- Deterministic sandbox zoning assigns stable per-block regions (fixed spacing by sorted block id) so different blocks project into separate planner-sandbox zones.
	- Zone fill telemetry (`ZoneFill`) reports deterministic per-block zone capacity estimate, projected ghost count, and fill ratio in GhostPlan metadata.
- Observe GhostPlan sandbox:
	- `python tools/ghost_observer.py <ghost_observation.json>`
- Progress reconciliation:
	- `python -c "from core.progress_reconciler import reconcile_progress_state; import json; print(reconcile_progress_state(json.load(open('progress_state.json')), json.load(open('ghost_observation.json'))).to_dict())"`
- Execution readiness:
	- `python -c "from core.execution_readiness import propose_execution; import json; print(propose_execution(json.load(open('progress_state.json')), json.load(open('capacity_phasing.json')), json.load(open('build_intent.json')), 'OK').to_dict())"`
- Execution authorization:
	- `python -c "from core.execution_authorizer import authorize_execution; import json; proposal=json.load(open('execution_proposal.json')); print(authorize_execution(proposal, proposal['allowed_actions'], 'test').to_dict())"`
- RL advisor (non-authoritative):
	- `python rl_advisor.py <rl_observation.json> [seed]`
	- The RL advisor is advisory only and cannot execute actions.
	- Every RL proposal sets `requires_authorization = true` and must flow through readiness, authorization, and then execution.
	- Safety boundaries: read-only inputs, no state mutation, no Lua calls, and no bypass of human/bot authorization.
	- Enriched observation fields now include `bot_utilization_ratio`, `power_stress_ratio`, `construction_backlog_estimate`, `phase_completion_ratio`, and `factory_density_score`.
	- Spatial awareness now includes `spatial_pressure_index` (normalized `[0,1]`) to indicate crowding/expansion pressure from bounds area, entity count, and density.
	- Throughput awareness now includes `throughput_stress_index` (normalized `[0,1]`) to indicate production pressure from assembler distribution, lab/assembler balance, concentration, and phase progress.
	- Block-level attribution now includes `pressure_attribution_map` (`block_id -> [0,1]`) for localized pressure visibility.
	- Production shortfall awareness now includes `production_gap_estimate` (`recipe_name -> integer`) for conservative per-recipe gap estimation.
	- Expansion target selection now includes an `ExpansionTarget` (`target_block`, `target_recipe`, `confidence`, `rationale`) chosen deterministically from pressure and gap telemetry.
	- Phase budget allocation now includes `CapacityAllocation` (`phase_capacity`, `allocated_now`, `reserved_for_later`) computed conservatively from current headroom and throughput stress.
	- Zone saturation shaping uses `metadata.zone_fill` to produce a deterministic `ZoneSaturationSignal` for the dominant block and dampens `project_more_ghosts` confidence as zone fill rises.
	- Construction pressure shaping computes deterministic backlog pressure from ProgressState and dampens `project_more_ghosts` confidence when committed work outpaces current progress.
	- Bot capacity shaping reads `metrics_summary.bot_utilization_ratio` and applies deterministic expansion damping as robot utilization rises.
	- Material supply awareness adds deterministic heuristic damping from backlog, throughput stress, and dominant production gaps; this remains advisory-only and does not perform recipe solving.
	- These enrichment values are deterministic and derived from existing metrics/progress (see `core.metrics.derive_rl_observation_health`, `core.metrics.derive_spatial_pressure`, `core.metrics.derive_throughput_stress`, `core.metrics.derive_block_pressure_attribution`, `core.metrics.derive_production_gap_estimate`, `core.target_selector.select_expansion_target`, and `core.capacity_allocator.allocate_phase_capacity`).
	- Future training hook points: replace the deterministic scoring policy in `rl_advisor.py` with a trained policy/value model while preserving schema validation and authorization gating.
- RL feedback builder (pre-training instrumentation):
	- `python rl_feedback_builder.py <progress_state.json> <metrics_summary.json> [construction_report.json] [execution_report.json]`
	- Builds deterministic, schema-validated RL feedback telemetry from read-only artifacts.
	- Closes the observational loop by attributing outcomes of authorized execution/construction without granting any control authority.
	- This is signal plumbing only for future training; no learning, no policy updates, and no state mutation are performed.
- Execution report validation:
	- `python tools/execution_reporter.py <execution_report.json>`
- Construction report validation:
	- `python tools/construction_reporter.py <construction_report.json>`
- Upgrade report validation:
	- `python tools/execution_reporter.py <upgrade_report.json>`
- Deconstruction report validation:
	- `python tools/execution_reporter.py <deconstruction_report.json>`
	- Deconstruction actions support both named targeting and position-only targeting.
- Construction progress update:
	- `python -c "from core.construction_progress_updater import update_progress_from_construction; import json; print(update_progress_from_construction(json.load(open('progress_state.json')), json.load(open('construction_report.json')))[0].to_dict())"`
- Phase advance proposal:
	- `python -c "from core.phase_advance_evaluator import propose_phase_advance; import json; print(propose_phase_advance(json.load(open('progress_state.json')), json.load(open('construction_progress.json'))).to_dict())"`
- Phase advance authorization:
	- `python -c "from core.phase_advance_evaluator import authorize_phase_advance; import json; proposal=json.load(open('phase_advance_proposal.json')); print(authorize_phase_advance(proposal, True, 'test', 'approved').to_dict())"`



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



--- FILE: docs/10_checklist_todo.md ---

# Checklist / TODO

Reconciled against actual code state on 2026-07-18 (see CURRENT_STATUS.md).

## Phase 0 – Foundation
- [x] Initial documentation suite
- [x] System invariants established
- [x] Canonical JSON schemas drafted
- [x] Agent operating instructions (AGENTS.md)

## Phase 1 – MVP (LocalLayoutPlanner)
- [x] Lua mod skeleton (`factorio_mod/control.lua` — targets Factorio 1.1, needs 2.0 port)
- [x] Area snapshot export (conform to `snapshot.schema.json`)
- [ ] Recipe DAG loader
- [ ] Single grid layout (Deterministic math) — `local_layout_planner.py` is a read-only inspector only
- [ ] Blueprint replication (conform to `build_plan.schema.json`)

## Phase 2 – Execution (RL Optimization)
- [ ] Headless Factorio setup
- [ ] Instruction language (FIL) parsing (`goal.schema.json`)
- [ ] RL executor (Targeted at build efficiency) — advisory-only `rl_advisor.py` exists; no learned policy
- [x] Reward function definition (Invariants-checked) — `rl_feedback_builder.py` (telemetry only, nothing consumes it yet)

## Phase 2.5 – Metrics & Policies
- [x] FactoryGraph-derived metrics (`core/factory_graph.py`, `core/metrics.py`)
- [x] Bot saturation detection (`core/bot_capacity_policy.py`, supervisor policy evaluator)
- [x] Power structure analysis (power stress ratio in metrics/observation)
- [x] Resource structure analysis (material supply, production gap, pressure attribution)
- [x] Policy threshold definitions (`policies/bot_thresholds.json`)
- [ ] Economic safety signal — source lost in bytecode-only commit d47af00; re-implement

## Phase 2.6 – Progress & Phasing
- [x] ProgressState schema (`core/progress_state.py`, `progress_reconciler.py`)
- [x] Capacity phasing policy (deterministic) (`core/capacity_phasing_policy.py`, `phase_advance_evaluator.py`)
- [x] Ghost-only incremental expansion (`core/ghost_slice_planner.py`, sandbox zoning, Lua ghost sink)
- [x] Deferred execution/RL (explicit future) — authorization gates enforced throughout

## Phase 2.7 – Game Bridge (added 2026-07-18; prerequisite for everything below)
- [x] Port `factorio_mod` to Factorio 2.0 (info.json, global→storage, game.*→helpers.*, created_entity→entity, get_recipe type gate)
- [x] Deploy script → `%APPDATA%\Factorio\mods` (`scripts/deploy_mod.ps1`)
- [x] Verified live snapshot from Factorio 2.0.77 validates against `snapshot.schema.json` (headless server + RCON, 2026-07-18)
- [x] RCON transport, Python→game commands (`tools/rcon_client.py`)
- [x] script-output watcher (game→Python file ingestion) (`orchestrator/game_bridge.py`)
- [x] Top-level orchestrator loop: snapshot → metrics → supervisor → planner → authorization → execution (`orchestrator/run_cycle.py`, one-shot cycle; recurring loop still TODO)
- [x] Fix capacity model: committed = current + pending ghosts; fill-delta semantics in ghost projection and execution readiness; snapshot-derived current capacity (verified live: cycle 1 projects 50 ghosts from pure observation, cycle 2 holds at delta 0)
- [x] First automated test suite: `tests/test_capacity_model.py` (9 tests; run `python -m pytest tests/`)
- [ ] Broaden test coverage beyond the capacity model (planners, executors, bridge)

## Phase N1 – Nauvis Core Mechanics (added 2026-07-18; see docs/22 curriculum)
- [x] Production lines: belts, inserters, machines with recipes (verified live, 9.6/s circuit line)
- [x] Miner-fed smelting chains; electric-only invariant; demand-scaled feeders/collectors
- [x] T-junction sideload feeding (measured: needs lane rate >= per-ingredient demand)
- [x] Line-to-line chaining incl. two-producer lane junctions (north/south entry)
- [ ] Machine tier parameter (assembling-machine-1/2/3) and quality tiers
- [x] Full science chain: ore → automation-science-pack → labs, 4 techs researched at ~0.4 units/s (Phase N2 gate CLEARED)
- [ ] Loop daemon builds lines (not placeholder grids); bottleneck diagnosis per line
- [ ] Action catalog + deterministic baseline policy (greedy bottleneck relief)
- [ ] Transition logging for the RL decision layer (docs/22)

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

Stress detection is driven by derived metrics, not heuristics.

Examples:
- Bot saturation thresholds
- Power margin erosion
- Resource depletion projections
- Transport utilization ratios

Supervisory interventions are triggered by metric thresholds,
not reactive failures.

Progress awareness is derived from snapshot + metrics + prior intents.
It provides context about current capacity phase versus ultimate targets.

## Intent Generation

Policy signals are translated into high-level intents.
Intents describe what kind of change is required,
not how or when it is executed.

Intents are consumed by planners,
not by executors.

Intents conform to [schemas/intent.schema.json](schemas/intent.schema.json).

## Intent Validation

All intents must validate against intent.schema.json.
Fixtures are used to lock semantics and prevent regression.

## Capacity Phasing Context

Supervisory policies reason about phased realization of a fixed target.
Phasing does not change the target; it sequences safe construction steps.

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

### 1.3 Analysis Layer Exemption

FactoryGraph construction and LocalLayoutPlanner inspection may read
raw entity snapshots for analysis only.

These analysis layers must:
- Remain read-only
- Produce abstract outputs (metrics, graphs, summaries)
- Never emit plans, layouts, or execution instructions

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

### 2.3 Phased Realization

Planning may define an ultimate capacity while construction is realized
in deterministic phases. Phasing must not rewrite the target plan.

Reserved capacity is permitted, even when unbuilt, as long as it is explicit.

Ghost-only control is allowed before execution.

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

## 10. Metrics Invariant

All planning and supervisory decisions must be based on
explicitly computed derived metrics.

Heuristic or intuition-based decisions are forbidden.

If a metric is not available, the system must compute it
before acting.

---

## 11. Enforcement

If an invariant is violated:
- The agent must stop
- The issue must be reported
- The user must be consulted if ambiguity exists

Working code that violates invariants is considered incorrect.

---

## 12. Electric-Only Equipment (user standard, 2026-07-18)

All planned and executed equipment MUST be electric:

- No burner mining drills — electric mining drills only
- No fuel-burning furnaces (stone/steel) — electric furnaces only
- No burner inserters — electric inserter family only
- No coal-based electricity (boilers / steam engines) — electric grid sources only

Any plan containing fuel-burning equipment is invalid and must be rejected
at validation time, not silently corrected.

---

## 13. Summary

These invariants define the identity of the system.

They ensure the agent behaves like:
- An industrial planner
- A logistics supervisor
- A civil engineer

Not a heuristic-driven bot.

If a feature cannot be built without breaking an invariant,
the feature must be redesigned or rejected.



--- FILE: docs/21_external_game_knowledge.md ---

# Path: docs/21_external_game_knowledge.md
# Purpose: Data-only external game knowledge (wiki-derived). Never overrides system standards (docs/19, docs/20).

# External Game Knowledge (Factorio Wiki)

Source: wiki.factorio.com Tutorial:Quick_start_guide + Tutorials index. Ingested 2026-07-18.
Per docs/19: reference data only — validated before use, never authoritative over our standards.

## Early-game progression order (Quick Start Guide)
1. Resources: coal, copper ore, iron ore, stone near spawn; water for steam.
2. Burner phase: burner drill → stone furnace direct-insert; paired coal drills fuel each other.
3. Belts + burner inserters (self-fuel from coal belts) for transport.
4. Electricity: offshore pump → boilers → steam engines; ratio **1 pump : 20 boilers : 40 steam engines**; replace burner drills with electric.
5. Research Automation (assembling machine 1, long inserters), then Logistics (splitters, underground belts, fast inserters).
6. Automate science: gear assembler + red-science assembler → inserter into labs.

## Ratios / heuristics
- Miners: ~2:1 iron:copper early game.
- Belt lanes: keep items split across both lanes of a belt for throughput ("ore split more or less evenly on each side").
- Leave room to expand around every production area.

## Relevant tutorials for later phases
- Main bus (base organization standard for mid-game) — candidate input for CityPlanner block design.
- Applied power math, Nuclear power (PlanetPlanner-era power planning).
- Train signals (rail standard alignment check for docs/14).
- Circuit network cookbook (control logic, far future).

## Logistics tiers (user-provided, 2026-07-18)
- Belts (items/s): transport-belt 15, fast-transport-belt 30,
  express-transport-belt 45, turbo-transport-belt 60 (**Vulcanus-only
  production** — must be imported off-planet).
- Inserters: fast-inserter baseline; bulk-inserter high hand capacity
  (upgradable +11); stack-inserter (**Gleba-only production**) stacks items
  4-high on belts, effectively quadrupling belt throughput.
- Planet-sourcing constraints are supply-chain facts for PlanetPlanner-era
  planning; on the test sandbox all tiers are available via scaffolding.

## Throughput physics (user-provided, validated live 2026-07-18)
- Inserter swings are rotation-bound: 180° to load, 180° to unload. One
  feeder cannot supply a hungry line; the first machines strip the belt and
  downstream machines starve (observed: 6-machine circuit line at 2/s of a
  9/s cap, machines 3-6 idle).
- Slower belts worsen unload time. Belt tier + inserter tier upgrades
  measured: fast-inserter + transport-belt 0.6/s → stack-inserter +
  express-belt 7.68/s on the same 6-machine circuit line (12.8x).
- Planner consequence: feed points scale with per-ingredient demand
  (feeders = ceil(demand / feeder_rate); LINE_RECIPES amounts × craft rate).

## Advanced feeding patterns (user-provided, 2026-07-18 — next to implement)
- **Dedicated belt per ingredient**: fill the entire input belt (both lanes)
  with the high-demand ingredient; run a second parallel belt for the other
  ingredient, reached by long-handed inserters (2-tile reach, slower swing).
- **T-junction sideloading**: a belt can empty onto one lane of another belt.
  Feeder belts running perpendicular continuously top up a lane; gaps on one
  lane are compensated by the other. Belt-fed lanes beat chest+inserter
  feeding because the belt buffer absorbs inserter swing gaps.

## Production growth axes (user standard)
Throughput grows over time along these axes, in roughly this order:
1. Faster belts (yellow → red → blue → turbo)
2. Stacked items (stack inserters, 4-high belt stacking)
3. More machines per line (X) and parallel lines (Y, LINE_PITCH_Y)
4. Better machine tiers (assembling machine 1 → 2 → 3)
5. Quality tiers (normal → uncommon → rare → epic → legendary)

## Feed-style tradeoffs (measured live, 2026-07-18)
Same 6-machine electronic-circuit line (27/s cable + 9/s plate demand),
stack inserters throughout, steady-state collector rates:
- chest-fed, express belts: **9.6/s** — chest feeders spray the high-demand
  ingredient onto BOTH lanes, so no lane cap; but limited feeder buffer.
- sideload-fed, express belts: **8.27/s** — each ingredient gets ONE dedicated
  lane; cable capped at an express lane's 22.5/s < 27/s demand (lane-limited).
- sideload-fed, turbo belts: **9.07/s** — turbo lane (30/s) clears the 27/s
  demand; residual ~0.5/s vs chest is junction/hop latency.
Planner rule of thumb: sideload feeding needs lane rate >= per-ingredient
demand; otherwise use chest feeding, a dedicated both-lane belt for the hot
ingredient, or a higher belt tier. These are exactly the tradeoffs the RL
decision layer (docs/22) will weigh as catalog actions.

**Capacity headroom (user standard, 2026-07-18):** provision feed and drain
capacity with a 20-25% buffer over raw demand (FEED_HEADROOM = 1.25 in the
planner) — feeder counts, collector counts, and the belt-tier suggestion in
the sideload lane check all use it. Hard lane-check failure only below raw
demand; the suggested tier always meets demand x headroom. Two purposes:
supply never runs at the ragged edge, AND the slack pre-pays expansion — a
line can grow ~25% (more machines in X) before its feed/drain infrastructure
needs rework, which the RL decision layer should count when costing
"extend_line_x" against other catalog actions.

## Roboport ranges (user-provided, verified live 2026-07-22)
Two distinct radii, and confusing them wastes materials or strands builds:
- **construction area**: `construction_radius = 55` -> 110x110 tiles. This is
  where construction bots may place ghosts. Overlapping construction areas do
  NOT merge networks.
- **supply / logistic area**: `logistic_radius = 25` -> 50x50 tiles. This is
  what links roboports into ONE network; in practice roboports must be within
  **~46 tiles** of each other to connect.
Consequence for scaffolding anchors: anchors further apart than ~46 tiles are
separate logistic networks, so each needs its own materials and bots (verified
live: anchors at (10,92) and (40,158), 68 tiles apart, report
`same_network=false`). Either space anchors <= 46 apart to form one network and
stock it once, or keep them separate and stock every anchor - the planner must
choose deliberately rather than assume.

## Quality system (wiki + user, 2026-07-18)
Encoded in `core/quality_modules.py`.

- Tiers and strength (effects are per-strength, additive): normal 0,
  uncommon 1, rare 2, epic 3, legendary 5.
- Per-entity quality effects, per strength point: assembling machines +30%
  crafting speed; inserters +30% rotation speed; electric poles +1 tile
  supply reach and +2 wire reach; beacons -16.67% power; modules +30%
  positive effects. Transport belts and walls gain health only — no
  throughput effect, so they are planner-irrelevant and excluded from the
  effects table.
- Crafting: quality modules give a chance to upgrade output one tier (then a
  repeated 10% chance per further tier). Ingredient quality match is exact,
  not minimum — a recipe set to a quality tier requires ALL item ingredients
  at exactly that tier; fluids have no quality and are exempt. Recyclers
  return 25% of inputs.
- **Safety rule (user-mandated):** mixed-quality items on a shared line jam
  production irrecoverably. Quality production requires dedicated, sorted
  lines per tier. `validate_uniform_quality()` rejects any line/feed spec
  whose ingredient quality tiers are not uniform and equal to the recipe's
  tier.
- Planet notes: none needed — quality tiers and effects are planet-agnostic.

## Modules (wiki, 2026-07-18)
Encoded in `core/quality_modules.py`.

- speed-module 1/2/3: speed +20/+30/+50%, energy +50/+60/+70%.
- productivity-module 1/2/3: productivity +4/+6/+10%, energy +40/+60/+80%,
  speed -5/-10/-15%.
- efficiency-module 1/2/3: energy -30/-40/-50%.
- quality-module 1/2/3: quality chance +1/+2/+2.5%, speed -5% each.
- Stacking rule: machine properties (speed/energy/pollution) cannot drop
  below 20% of their original value regardless of how many modules stack —
  `apply_modules()` enforces this floor.
- Productivity modules only apply to intermediate-product recipes. In our
  LINE_RECIPES world (`planners/local_layout_planner.py`) the intermediates
  are: iron-gear-wheel, copper-cable, iron-stick, electronic-circuit,
  iron-plate, copper-plate — plus automation-science-pack, since science
  packs accept productivity in Factorio. Productivity modules are never
  allowed in beacons.
- Module slots (verified live against 2.0.77 prototypes via RCON,
  2026-07-18): assembling-machine-1: 0, assembling-machine-2: 2,
  assembling-machine-3: 4, electric-furnace: 2, beacon: 2.
- Planet notes: none needed — module effects and slot counts are
  planet-agnostic.

## Implications adopted (validated against our invariants)
- Two-lane belt feeding supports 2-ingredient recipes on a single input belt
  (inserters only pick up items their destination accepts).
- Science automation chain (gears → red science → labs) is the first
  multi-line dependency target for LocalLayoutPlanner chaining.



--- FILE: docs/22_rl_decision_layer.md ---

# Path: docs/22_rl_decision_layer.md
# Purpose: Architecture of the RL decision layer: what RL decides, over what action space, with what rewards. User-defined 2026-07-18.

# RL Decision Layer

## Division of labor (invariant-compatible)

- **Deterministic planners** own all structure: layouts, geometry, ratios,
  belt routing, pipeline composition. They compile goals into build plans and
  publish an **action catalog** of executable expansion options with
  predicted effects.
- **RL** owns the *decision policy*: WHEN to expand and WHICH catalog action
  to take. It never draws a layout; it chooses among symbolically planned,
  authorization-gated actions.
- **Authorization gates remain** on every executed action.

## What RL decides

1. **When to increase production** — act now vs consolidate.
2. **How to increase production**, choosing among catalog actions:
   - Linear expansion: more machines on an existing line (X axis)
   - Area expansion: parallel lines (Y axis), new production blocks
   - Efficiency/density: belt tier, inserter tier, machine tier, quality
     tier upgrades on existing lines
   - Resource expansion: open a new mine, build the full pipeline
     (mine → smelt → assemble) for a missing input
3. **Which research to choose and when** (final-objective coupling).

## Observation space (deterministic diagnosis feeds the policy)

- Production rates per item (measured, per line and aggregate)
- **Bottleneck verdict per line**: machine-limited / feed-limited /
  drain-limited / input-starved / resource-depleted / space-limited
- Resource headroom: ore patch remaining, buildable area, material stocks
  (machines, belts, inserters available to bots)
- Research state: current tech, science pack consumption vs production
- Goal queue state (below)

## Goal stacking

Goals are declarative targets (product, rate), queued hierarchically:
step A → step B → step C. The planner compiles each goal into build intents
and catalog actions; RL sequences and schedules them. Sub-goals spawned by
resource constraints (need copper → open copper mine → smelt → cable line)
nest under their parent goal.

## Reward stages

1. **Stage 1 — throughput shaping**: delta in items/s for targeted products.
2. **Stage 2 — pipeline completion**: first production of a new product
   type; full chains (ore→product) worth more than infinity-fed lines.
3. **Stage 3 — science throughput**: science packs/s produced and consumed.
4. **Final — research maximization**: research completed per unit time,
   including the value of WHICH tech was chosen (tech-tree-aware).

Earlier stages shape; the final stage dominates as capability grows.

## Curriculum (user-defined ordering)

- **N1 (current): Nauvis core mechanics** — lines, chaining, mining
  pipelines, sideload feeding, X/Y scaling, tier upgrades.
- **N2: Full science chain** — ore to automation-science to labs, research
  actually progressing; first end-to-end reward signal.
- **N3: Multi-science, multi-pipeline Nauvis base** — goal stacking at scale.
- **S1: Space travel** — platforms, launches.
- **S2: Planet production** — per-planet ore and environment constraints
  (turbo belts from Vulcanus, stack inserters from Gleba, etc.).
- **S3: Interplanetary supply** — transporting finished/semi-finished
  products between planets; PlanetPlanner + InterplanetarySupervisor era.

## Force isolation (REQUIRED for the reward to exist)

The agent's factory MUST run on its own Factorio force with its own tech
tree. Measured on a completed megabase save, research-per-time is identically
zero — every finite technology is already researched, and a researched tech
cannot even be un-researched (Factorio reverts it to keep prerequisites
consistent with its researched successors). The terminal reward would be
silently dead.

Bootstrap (verified live 2026-07-18): create the force, friend it with
"player", reassign every sandbox entity to it (electric networks are
per-force, so power scaffolding must convert too), and mark the
`automation-science-pack` trigger technology researched — Space Age gates the
whole tree behind it, and a fresh force cannot queue anything without it.
Result: 15 automation-pack technologies available, and infinite technologies
beyond them, so the reward is attributable and unbounded.

## Training mechanics (implementation plan)

1. Log transitions from live cycles: (observation, catalog action, staged
   reward, next observation) — the daemon already emits observation-shaped
   status traces; rewards computed from measured rate deltas.
2. Start with a deterministic baseline policy (greedy bottleneck-relief) so
   the system works before learning does; RL must beat it to earn trust.
3. First learned policy: contextual bandit / linear over the observation
   vector (small, inspectable). Neural policies only when the catalog and
   observation stabilize.
4. RL output remains a proposal into the existing authorization gates.



--- FILE: docs/23_fluid_systems.md ---

# Path: docs/23_fluid_systems.md
# Purpose: Fluid mechanics knowledge (wiki + live prototypes). Data-only per docs/19; encoded in core/fluid_systems.py.

# Fluid Systems

Sources: wiki.factorio.com/Fluid_system and the live game's own prototypes
(Factorio 2.0.77, queried via RCON 2026-07-18). Numbers marked *verified live*
came from `prototypes.entity[...]`, not from memory.

## The hard rule: one fluid per network

A fluid segment can hold exactly **one** fluid type. If two fluids meet, all
but one are deleted, and the network must be flushed (pipe GUI trash icon) or
deconstructed. There is no throughput penalty to recover from — it is a
correctness failure, worse than a belt jam because it silently destroys
product and cannot be cleared by waiting.

**Planner consequence:** `validate_network_purity()` is a hard validator, the
fluid analogue of the quality jam guard. Any layout that puts two fluids in
one connected pipe network is invalid and must be rejected, never "fixed up".

The sanctioned way to run different fluids near each other is a **pump**: a
pump separates networks (and prevents backflow), so pipes of different fluids
may meet only through one.

## Entities (verified live: footprint, fluid boxes, connections)

| entity | tiles | boxes | connections | notes |
|---|---|---|---|---|
| pipe | 1x1 | 1 | 4 | N/E/S/W; holds 100 |
| pipe-to-ground | 1x1 | 1 | 2 | one normal, one underground, **max span 10** |
| pump | 1x2 | 1 | 2 | directional; separates networks; electric |
| storage-tank | 3x3 | 1 | 4 | capacity **25 000** |
| offshore-pump | 1x1 | 1 | 1 | fluid source; must sit on water |
| chemical-plant | 3x3 | 4 | 4 | inputs (-1,-1) (1,-1); outputs (-1,1) (1,1) |
| oil-refinery | 5x5 | 5 | 5 | inputs (-1,2) (1,2); outputs (-2,-2) (0,-2) (2,-2) |

Connection offsets are relative to the entity centre in its **unrotated
(north) frame**. Note the two machines are opposite-handed: the chemical
plant takes input from the north and emits south, the refinery takes input
from the south and emits north — so a chain of both needs one of them
rotated, which is exactly why orientation must be a planning variable.

The pump's own connections are half-tile because it is 1x2: **output (0,-0.5),
input (0,+0.5)** — a north-facing pump draws from the south and pushes north.

## Rotation and flipping

Fluid connection points move with the entity, so a planner cannot treat a
machine as a featureless box:

- rotate east: `(x, y) -> (-y, x)` (verified: a north connection (0,-1) becomes (1,0))
- rotate south: `(x, y) -> (-x, -y)`
- rotate west: `(x, y) -> (y, -x)`
- flip horizontal: `(x, y) -> (-x, y)`; flip vertical: `(x, y) -> (x, -y)`

Flipping is what lets a mirrored line feed from the opposite side without
re-planning the whole block — the 2.0 flip support is a real layout tool, not
cosmetic. Apply flip first, then rotation.

## Throughput and distance

- ~6000 fluid/s theoretical per connection (100/tick); ~4200/s practical.
- A machine with two outputs of the same fluid reaches roughly 8400/s.
- Flow rate depends on segment fullness: near-empty segments accept quickly,
  near-full segments push out quickly.
- A continuous pipeline spanning more than **320x320 tiles** (10x10 chunks)
  without a pump **stops flowing entirely** — long runs need pump breaks.
- Underground pipes span at most **10 tiles** between the two ends, on the
  same axis, facing each other.

## Planner implications (to build on)

1. Fluid lines need a network-aware layout pass: assign each pipe run a fluid,
   and prove adjacency purity before emitting a build plan.
2. Underground pipes are the crossing primitive: when two fluid runs must
   cross, one dives under. That is the only legal crossing.
3. Pumps serve three planner roles: direction, network separation, and
   long-run refresh (every <320 tiles).
4. Machine orientation becomes a planning variable for the first time —
   which side inputs arrive on is chosen, not given.



--- FILE: docs/30_codex_brief_realbase_autonomy.md ---

# Path: docs/30_codex_brief_realbase_autonomy.md
# Purpose: Handoff for Codex to finish the real-base autonomous factory-expansion system started this session — the plan, the current live-verified state, exactly how the service is run, and the remaining work.

Use gpt5.6-terra, high effort. Read `AGENTS.md` first (files <=500 LOC, "# Path:"/"# Purpose:"
header on every file, deterministic/symbolic planning only, commit messages explain why, do NOT
touch git remotes). This is a NEW direction, separate from the synthetic-sandbox electronics-block
pipeline that fills most of this repo — read the "What this is (and is NOT)" section carefully so
you don't accidentally wire the new work back into the old sandbox assumptions.

---

## 1. What this is (and is NOT)

The user pivoted the whole project to a new goal: **an autonomous system that, given a high-level
target ("produce X", "research Y"), figures out on its own how to build/expand a REAL factory on a
REAL Factorio save — surveying actual terrain, deciding what to build, placing it, connecting it,
and troubleshooting failures itself — with NO human babysitting each step.** The user's exact
words: *"It should do this decision making on it's own, not through you ... I give it a target ->
do x research, -> increase x production ... and it should figure out how to do that itself, not
through you babysitting it's every step."* And: *"if it's running low on electricity, make that
itself, if it's running low on assembly machines make that itself ... it should set up everything
on it's own from here on out."*

This is NOT the synthetic-sandbox pipeline. Do NOT reuse or extend:
- `planners/electronics_block.py`, `tools/build_processing_units.py`, `planners/resource_survey.py`
  (all hard-locked to the synthetic `planner-sandbox` surface + fixed WorldSpec).
- `orchestrator/expansion_daemon.py` / `run_cycle.py` / `loop_daemon.py` (three older,
  non-integrated autonomy-loop prototypes, all sandbox-bound; the user's intent supersedes them —
  they implement only 2 of 9 catalog actions and assume a pre-registered line registry).

The new system lives in three NEW files created this session (all uncommitted — commit them as
part of your first change; they compile and are live-verified, see section 3):
- `orchestrator/live_base.py` (265 LOC) — observe a real surface/force over RCON.
- `orchestrator/autonomous_builder.py` (426 LOC) — the decide → build → troubleshoot loop.
- `planners/belt_bridge.py` (119 LOC) — reusable chest-to-chest belt+inserter connection geometry.

Two ALSO-uncommitted, load-bearing Lua changes make the mod able to target a real surface/force
(previously everything was hardcoded to `planner-sandbox`/`planner`). DO NOT revert these:
- `factorio_mod/sandbox_shared.lua`: `get_or_create_sandbox_surface(surface_name)` and
  `get_or_create_planner_force(force_name)` now take an optional arg to operate on an EXISTING
  surface/force (e.g. `"nauvis"`/`"player"`) with no creation/tech-sync/friendship — the real
  force already has its own research.
- `factorio_mod/layout_executor.lua`: `execute_build_plan` reads `build_plan.surface` and
  `build_plan.force` and passes them through, so a BuildPlan can carry `"surface":"nauvis"`,
  `"force":"player"`. These need a mod redeploy + server restart to take effect (see section 4).

---

## 1b. Prior state — everything the deterministic PLANNER already achieved (the "1M save" / `planner-sandbox` pipeline)

Before this real-base pivot, the whole project was a deterministic symbolic planner + live executor
that built ONE fixed factory on a SYNTHETIC surface. That work is committed (git history `78e21ea`
… `5b9a1a2`, milestones M1–M7) and, crucially, **fully working and live-verified** — it is the
proving ground for every geometry/execution primitive the new real-base system reuses. You are not
extending it, but you should MINE IT FOR PATTERNS, and you must not re-break what it got right.

What the sandbox pipeline does and achieved:
- **The build:** `planners/electronics_block.py::build_electronics_block(include_processing=True,
  world=...)` composes a complete raw-ore → iron/copper plate → cable → electronic-circuit →
  advanced-circuit → **processing-unit** factory (~4,300 production entities + ~250 infrastructure
  entities), plus the sulfur/sulfuric-acid oil chain, against a hand-authored `ElectronicsWorldSpec`
  (`tests/fixtures/electronics_world_spec.json`) on a generated 500×500 `planner-sandbox` surface
  (save `1M_test.zip`), isolated `planner` force.
- **Verified end-to-end LIVE** (`tools/build_processing_units.py --construction-mode radial
  --existing-topology reset`, checked by `tools/verify_factory_invariants.py`): exactly **1
  electric network, 1 roboport network, 0 stuck ghosts**, oil refineries `working` with crude
  flowing, processing units actually produced. This clean result was the payoff of M7.

Load-bearing things it got RIGHT that the real-base system depends on (do not regress):
- **Pole auto-wiring root cause (the single most important fix).** Factorio's
  `LuaSurface.create_entity` auto-wires nearby electric poles UNRELIABLY once ~60+ poles already
  exist on a surface (verified live: fine with 2–3 poles, silently drops connections at scale).
  Fixed in `factorio_mod/layout_executor.lua::ensure_pole_wiring` — after every placement it
  explicitly wires each pole pair within `min(get_max_wire_distance)` using the modern
  `get_wire_connector(pole_copper).connect_to(...)` API (NOT `connect_neighbour`, which doesn't
  exist on poles in 2.0.77). The new real-base builder relies on this exact pass every time it
  submits a plan.
- **Live-verified prototype constants** (match `planners/infrastructure.py::POLE_SPECS`):
  substation wire=18 supply=9, big-electric-pole wire=32 supply=2, medium wire=9 supply=3.5,
  small wire=7.5 supply=2.5; roboport construction radius 55, logistic/link ~46 (conservative 50
  hard limit). The new `autonomous_builder.py` uses these same numbers for `extend_power` /
  `extend_roboport_coverage`.
- **Geometry primitives you should reuse, not reinvent:** `planners/infrastructure_geometry.py`
  (`step_points`, `choose_clear_l_route`, `minimum_spanning_tree_edges`, `FootprintPlacer`),
  `planners/local_layout_planner.py::generate_line_layout` (+ `generate_mining_feed`), and the
  fluid geometry in `planners/fluid_layouts.py` / `planners/fluid_routing.py`
  (A* obstacle-aware pipe routing, `generate_fluid_machine_row`). `planners/belt_bridge.py` (new)
  already wraps `choose_clear_l_route` for the real base — a `pipe_bridge` for fluids should mirror
  it.
- **Native-tier defaults + explicit upgrade path** (`a4145f0`): initial builds use
  `fast-transport-belt` + `fast-inserter` (Nauvis-craftable); express/stack/turbo are
  planet-imported and only worth it at scale — there's a `tools/generate_electronics_upgrade_plan.py`
  + `factorio_mod/upgrades.lua` upgrade flow. Keep this principle on the real base.

Known OPEN issues carried over from the sandbox work (documented in `docs/28`, `docs/29`; on a real
Nauvis save some of these DON'T apply because water/oil is real terrain, not seeded):
- Offshore-pump orientation/`water_lakes.py` direction semantics are backwards, and correcting them
  collides with the pump's own power scaffold (task list #1). On the real base you SKIP lake-seeding
  entirely but still need the correct pump connector geometry from `docs/29`.
- A belt-turn-immediately-after-an-underground-exit can be left disconnected (item-routing tunnel
  geometry) — relevant if you reuse the sandbox item router; the new `belt_bridge` avoids it by
  routing simple L-paths.
- `docs/26`/`docs/27` are the M6/M7 session-state handoffs with the full blow-by-blow if you need
  deeper history; `CURRENT_STATUS.md` is the append-only milestone log.

The through-line: the sandbox pipeline proved the deterministic geometry + the live-execution mod
work. The real-base system swaps out the "fixed WorldSpec on a synthetic isolated surface"
assumption for "survey and adapt to a real surface/force," reusing the proven primitives.

---

## 2. Architecture of the new system (how it already works)

`orchestrator/autonomous_builder.py::run(goal_item, ...)` loops:
`survey → decide the single deepest missing stage → build it → troubleshoot → repeat`, until the
goal item has a real, working line, or it raises `StuckError` (never guesses silently — a hard
project rule).

- **Survey** (`orchestrator/live_base.py`): `find_line` (existing machines by recipe + working
  count), `nearest_resource` (nearest real ore/resource patch + its bbox), `find_clear_area`
  (nearest buildable box, ring search — note: it EXCLUDES resource tiles from "occupied" so a
  mining stage can stand ON ore), `entity_at`/`is_safe_to_clear`/`remove_entity_at`
  (obstruction handling), `entity_status_name` (decode live `.status`), `pole_network_id` +
  `nearest_pole_on_other_network` (power-gap detection), `nearest_roboport`, `chest_contents`,
  `bot_and_power_summary`.
- **Decide** (`ensure_produced`): recursion over `planners/recipe_data.py::LINE_RECIPES`. If the
  item is already producing (>=1 machine with that recipe in `working` status) → return its
  output chest position. Else if it's a `_mineable` recipe (sole ingredient is a raw resource,
  not another LINE_RECIPE) → build a mining+smelting stage. Else → recursively ensure every
  ingredient is produced first, collect their output-chest positions, then build a conversion
  stage fed from those. Builds exactly ONE stage per call and returns None, so the caller
  re-surveys each loop (idempotent, restart-safe).
- **Build**: `build_mining_stage` (uses `LocalLayoutPlanner.generate_line_layout(mining_feed=True)`),
  `build_conversion_stage` (chest-fed line + a `belt_bridge` from each ingredient's real upstream
  chest). Both call `strip_local_power(plan, remove_substations=False)` to drop the sandbox's
  free `electric-energy-interface` cheat but keep the local substation, then set
  `plan["surface"]="nauvis"`, `plan["force"]="player"`. Conversion stages replace the sandbox's
  `infinity-chest` feeders with real `steel-chest` (via `_swap_infinity_chests`) — no cheating,
  everything comes from mined ore.
- **Troubleshoot** (the user explicitly asked for this — all three are implemented AND
  live-verified this session):
  - `_submit` retries after clearing a blocking tile ONLY if it's safe map clutter (tree/rock,
    neutral force); anything a force built raises `StuckError` ("go around, not through") rather
    than bulldozing real infrastructure.
  - `extend_power`: if a stage's machines report `no_power`, detect the isolated pole network and
    build a real medium-pole chain to the nearest pole on another (the main) network.
  - `extend_roboport_coverage`: if ghosts never build because the site is beyond every roboport's
    55-tile construction radius, chain new roboports out (within link distance) until covered.
  - `_diagnose_machines`: polls each machine's real `.status` with a grace period (bots are slow),
    reports the actual stuck reason; for conversion stages, if a feed chest is empty it names the
    specific bridge that isn't delivering.

Everything is materialized as a standard BuildPlan (`schemas/build_plan.schema.json`) and submitted
through the EXISTING `orchestrator/game_bridge.py::GameBridge.build_layout(authorization, plan)`
(backed by `factorio_mod/layout_executor.lua`), which already preserves direction/recipe/etc. and
does explicit pole auto-wiring. Authorization comes from
`planners/sandbox_infrastructure.py::build_layout_authorization`.

---

## 3. Current live-verified state (what's actually running on the user's save)

Save: a copy of the user's `mod_playground.zip` (real Nauvis, `player` force). Starting point the
user placed by hand: 1 roboport at (3,-1), 50 logistic + 50 construction bots, an
electric-energy-interface at (0,0), substations forming a small powered grid, and two
passive-provider chests holding a large starter stockpile of machines/belts/inserters/materials
(that stockpile is for BUILDING entities, NOT for feeding production — the user was explicit that
science packs must be made "from scratch by mining ore").

**ACCURACY NOTE (added after a later server restart):** the factory described below WAS built and
verified live, but it was never `/server-save`d, so the on-disk `mod_playground.zip` copy reloaded
clean. The base is currently back to its pristine starting state (1 roboport, 1
electric-energy-interface, 4 substations, the 2 stockpile chests, 1 storage chest — nothing else).
The verification below is a true record of what the code achieved, NOT a description of what is
physically standing right now. Re-running `run("automation-science-pack", ...)` should rebuild it.
Lesson: call `/server-save` after any live build you want to keep.

Verified working live this session (real ore → real product, nothing scripted):
- Iron chain: 3 mining drills on the real iron patch → 3 electric furnaces → iron-plate (135+
  produced) → 2 assemblers making iron-gear-wheel (88+ produced) → belt bridge back to →
  2 assemblers making automation-science-pack (18+ produced). All on the existing power grid.
- Copper chain: `ensure_produced('copper-plate', ...)` was run through the autonomous builder and
  exercised ALL THREE self-repair paths for real: the copper patch is ~60 tiles out, so the build
  hit (a) `no_power` → `extend_power` built a pole bridge and fixed it, and (b) ghosts beyond
  roboport range → `extend_roboport_coverage` chained a roboport out and they built. As of handoff,
  1 of 2 copper drill/furnace pairs is mining+smelting; the 2nd has the known bug below.

Nothing here is committed yet. `git status` shows the 2 modified Lua files + 3 untracked new
Python files. `git log` head is `5b9a1a2`.

---

## 4. EXACTLY how the service is run (you will likely NOT have live RCON — user runs it; but this
is the full procedure so your code matches it)

Same division of labor as prior Codex briefs: **you write/extend the code offline; the USER runs
it live against their save and reports back.** But your code must match this runtime exactly.

**Server (headless Factorio against a COPY of the save — never the user's original):**
- Data dir this session (session-specific temp path; the user will have their own when they run
  it — treat the path as a variable `$fd`):
  `...\scratchpad\mod_playground_run\` containing `config.ini`, `mod_playground.zip` (copy),
  `server-settings.json` (with `auto_pause=false`), and `mods\factorio_cursor_rl_agent\`.
- `factorio.exe` at `E:\Games\Factorio\bin\x64\factorio.exe` has "Run as administrator" forced in
  its Windows compat settings → it can ONLY be launched from an ELEVATED PowerShell. The agent
  cannot self-elevate; the user runs this launch command:
  ```powershell
  $fd = "<data-dir>"
  $exe = "E:\Games\Factorio\bin\x64\factorio.exe"
  $argList = @("--config","$fd\config.ini","--mod-directory","$fd\mods","--start-server","$fd\mod_playground.zip","--server-settings","$fd\server-settings.json","--port","34199","--rcon-port","27017","--rcon-password","planner_test")
  Start-Process -FilePath $exe -ArgumentList $argList -RedirectStandardOutput "$fd\launch_out.log" -RedirectStandardError "$fd\launch_err.log"
  ```
  Ports: game 34199, RCON 27017, password `planner_test` (chosen to not collide with the older
  sandbox server on 34198/27015 if it's also up).

**Mod deploy (needed after ANY `factorio_mod/*.lua` change — mods only load at server start):**
copy `info.json` + every `factorio_mod\*.lua` into `$fd\mods\factorio_cursor_rl_agent\` AND into
`%APPDATA%\Factorio\mods\factorio_cursor_rl_agent\` (so the user's own client matches — a mismatch
gives "mod script files are not identical between you and the server" on join). After deploy, the
server AND the user's client both need a full restart. Byte-identical mod folders are required.

**RCON (ad-hoc queries):** from repo root, Git Bash:
```bash
MSYS_NO_PATHCONV=1 python tools/rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/sc <lua> rcon.print(...)"
```
`MSYS_NO_PATHCONV=1` is required so Git Bash doesn't mangle the leading `/` of `/sc`.

**Running the autonomous builder (the actual service):**
```python
from tools.rcon_client import RconClient
from orchestrator.game_bridge import GameBridge
from orchestrator.autonomous_builder import ensure_produced, run
# one target end-to-end:
run("automation-science-pack", surface="nauvis", force="player",
    rcon_port=27017, rcon_password="planner_test",
    script_output=r"<data-dir>\script-output", reference_point=(3.0, -1.0))
```
`reference_point` is where the builder starts searching for space/resources (the roboport at
(3,-1) is a good origin). `script_output` must be the server's own `script-output` dir (that's
where the mod writes its JSON reports that GameBridge polls).

A clean-slate reset (the older sandbox helper) is NOT used here — this is a real base, you never
wipe it. To remove a specific mis-built test stage, target exact entity NAMES in a bounded area
(a blanket "destroy everything in area" is correctly blocked by the safety classifier and by good
sense — never do it on the real base).

---

## 5. The plan / remaining work (in priority order)

**A. Fix the known drill-placement bug (start here — it's the immediate blocker).**
`build_mining_stage` calls `find_clear_area` near the ore patch, then places drills via
`generate_mining_feed` (drills 3x3, at rows -3..-1 north of the line's input belt). The bug: the
chosen origin can put one or more drills' 3x3 footprints partly OFF the ore patch's irregular
edge, so those drills report `no_minable_resources` while others work. `find_clear_area` only
checks that the AREA is buildable (and now correctly ignores resource tiles), but does NOT verify
every individual drill footprint actually overlaps minable ore. Fix: after computing candidate
drill positions, verify (live, via a new `live_base` helper that queries `find_entities_filtered
{type='resource', area=<drill 3x3>}`) that each drill's footprint contains the target ore; if not,
shift the whole line origin along the patch (or shrink machine_count) until every drill sits on
ore. Prefer aligning the line to the patch's actual bbox (returned by `nearest_resource`) over
guessing. This is deterministic and testable offline with a fake bbox + fake resource-tile set.

**B. Add the missing science-pack recipes to `planners/recipe_data.py::LINE_RECIPES`.**
Only `automation-science-pack` is defined. The user's target set is automation, logistic, chemical,
and production science packs. Add `logistic-science-pack`, `chemical-science-pack`,
`production-science-pack` and every intermediate they need that isn't already present (check
Factorio 2.0 Space Age recipes live via RCON `prototypes.recipe['<name>'].ingredients` against the
running game — do NOT trust memory for 2.0 recipes; they differ from 1.1). Chemical science pulls
in an oil→sulfur→sulfuric-acid fluid chain and engine units; that means the builder needs a fluid
path too — see C. Respect the existing LINE_RECIPES shape (machine, ingredients, amounts,
product_amount, craft_time, and fluid_ingredients where relevant, as processing-unit already
shows).

**C. Extend the builder to handle FLUIDS (currently solids-only).**
`build_conversion_stage` bridges solid ingredients over belts only. Chemical/production science
need pipes (water from offshore pump, oil from pumpjack, sulfuric acid between stages). You have a
lot of prior fluid geometry to draw on (`planners/fluid_layouts.py`, `planners/fluid_routing.py`,
`generate_fluid_machine_row`) but it's all sandbox-shaped — adapt the PATTERN (a
`pipe_bridge` analogous to `belt_bridge`, plus fluid-source stages for real pumpjack/offshore-pump
placement) rather than wiring in the sandbox composer. Note the offshore-pump direction bug fully
diagnosed in `docs/29`/`planners/water_lakes.py` — on a REAL Nauvis save the water/oil already
exists as terrain, so you do NOT seed lakes here (that was a synthetic-sandbox concern); you just
need to place the pump/pumpjack correctly against existing water/oil and connect the pipe. Use the
live-verified connector geometry in `docs/29` (pumpjack west-facing connector at center+(-1,+1),
offshore-pump connector semantics) and re-probe live to confirm before trusting.

**D. Generalize "build more of X when running low" beyond power/roboports.**
Power and roboport self-expansion are done. The user also wants: low on assembly machines → build
more; low on belts/inserters/pipes/rails/bots → produce those itself; i.e. a dedicated
"expansion supplies" sub-factory. This is the largest remaining piece. A reasonable first slice:
when `run(goal)` detects a stage is throughput-limited (all its machines `full_output` upstream
but downstream still starved, or a feed chest chronically empty despite a working upstream), add a
parallel line or more machines — reuse the `add_parallel_line`/`extend_line_x` IDEAS from
`core/action_catalog.py` but implement them against the real base, not the sandbox. Do NOT try to
build the whole 9-action catalog at once; implement the specific expansions the science-pack goals
actually demand, verify each live, then broaden. Flag anything that needs a product decision (e.g.
"how many parallel lines is 'enough'") rather than inventing a magic threshold.

**E. Wire a top-level goal/target interface.**
Right now the entry point is `ensure_produced`/`run(goal_item)`. The user wants to hand it targets
like "do X research" and "increase X production by N". Add a small CLI/driver
(`tools/autonomous_run.py` or similar) that accepts a goal spec and drives `run` to completion,
emitting progress. Research targets mean: ensure the required science packs are producing, then
set the research queue (there's a `/set_research` mod command + `game_bridge.set_research`) and
report progress — but confirm the tech tree state on the REAL player force first (it already has
whatever the user researched).

---

## 6. Hard constraints (do not violate — these are the user's standards, learned the hard way)

- **Never babysit / never guess silently.** Every failure path either self-repairs deterministically
  or raises `StuckError` with the real diagnosed reason. No fabricated success.
- **From scratch by mining ore** — production ingredients come from mined resources, never from the
  user's starter stockpile chests (those are for building entities only) and never from
  infinity-chest cheats.
- **Real base = never destructive at scale.** Route around real infrastructure; only auto-clear
  neutral map clutter (trees/rocks). A blanket area-wipe is forbidden.
- **Electric-only** (no burner drills/boilers/fuel furnaces) unless the user says otherwise.
- **Verify live, not by assertion.** Every real defect this project has ever had was found by
  running the actual game (preflight/tests passing != game truth). Your offline work must be
  structured so the user can run it live and it actually works; where you can't verify live
  yourself, say exactly what's unverified.
- Files <=500 LOC; keep the "# Path:"/"# Purpose:" headers.

---

## 7. Deliverables / report back

Commit the 3 new Python files + 2 Lua changes as your first commit (they're validated), then
proceed through the plan. Report: which files changed, LOC of each, what you verified offline vs.
what needs the user's live run, and any point where you had to stop for a product decision. Do NOT
push. Do NOT connect to a live Factorio instance yourself — hand live-run steps to the user with
the exact commands from section 4.



--- FILE: docs/31_factorio_mod_interaction_and_troubleshooting.md ---

# Path: docs/31_factorio_mod_interaction_and_troubleshooting.md
# Purpose: Authoritative runbook for safe Factorio mod interaction and troubleshooting.

# Factorio Mod Interaction and Troubleshooting

This is the authoritative runbook for interacting with this repository's Factorio mod.
Begin read-only, identify the exact server and data directory, and obtain explicit user
authorization before any live mutation, deployment, save/reset, or lifecycle action.

## Architecture

```text
operator/Python --console command over RCON--> Factorio server
operator/Python <--JSON from script-output---- Factorio mod
```

- The mod exposes 19 console commands through `commands.add_command`.
- It has no `remote.add_interface`; there is no Python-callable Factorio remote interface.
- `tools/rcon_client.py` sends raw console commands.
- `orchestrator.game_bridge.GameBridge` sends commands over RCON, then waits for a new,
  parseable JSON file in the server's own `script-output`.
- `tools/autonomous_run.py` is the high-level real-base production/research workflow.

Raw `/sc` is arbitrary Lua and can mutate the world. Review every character before
sending it. Unprotected Lua errors have historically killed the dedicated server in this
development setup; keep diagnostics small, wrap uncertain access with `pcall`, and print
compact output with `rcon.print`.

## Choose the Narrowest Interface

| Need | Use | State risk |
| --- | --- | --- |
| Connectivity, `/help`, compact query | `tools/rcon_client.py` | Depends on command |
| Supported export/report | `GameBridge` | Depends on method |
| Real-base item or research goal | `tools/autonomous_run.py` | State-changing |
| Inspect an existing JSON artifact | Offline validator/tool | None to server |

Do not use autonomous execution for diagnosis. Do not use `/sc` when a registered
read-only export already answers the question.

## Preflight

1. Confirm the intended server, RCON host/port, and allowed action.
2. Identify that server process's own `script-output` directory.
3. Probe with `/sc rcon.print(game.tick)`.
4. Check registration with `/help <command>`.
5. For a real base, explicitly use surface `nauvis` and force `player`.
6. Record repo and deployed-mod revisions before diagnosing drift.

There is a current default mismatch: `rcon_client.py` and `GameBridge` default to port
`27015`; `autonomous_run.py` defaults to `27017`. Always pass the port explicitly.
Examples use the overridable local development values `127.0.0.1:27017` and
`planner_test`; they are not universal credentials.

### PowerShell

```powershell
Set-Location "C:\Users\djsma\Downloads\Github_Desktop\Factorio_Cursor_RL_Agent"
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/sc rcon.print(game.tick)"
python tools\rcon_client.py --host 127.0.0.1 --port 27017 --password planner_test "/help snapshot"
```

PowerShell uses `$env:NAME=...`, `Start-Sleep`, and `Select-Object -Last`; it does not
interpret Bash-style `NAME=value command`, GNU `timeout`/`tail`, or shell syntax the
same way.

### Git Bash

```bash
cd "/c/Users/djsma/Downloads/Github_Desktop/Factorio_Cursor_RL_Agent"
MSYS_NO_PATHCONV=1 timeout 20 python tools/rcon_client.py \
  --host 127.0.0.1 --port 27017 --password planner_test \
  "/sc rcon.print(game.tick)" 2>&1 | tail -3
```

`MSYS_NO_PATHCONV=1` prevents Git Bash from rewriting slash-prefixed Factorio commands.

## Locate and Validate `script-output`

`GameBridge` must receive the `script-output` directory belonging to the same server as
the RCON endpoint. A client-side `%APPDATA%\Factorio\script-output` is wrong when the
dedicated server uses a separate config/data directory.

```powershell
$serverData = "C:\path\to\server-data"
$scriptOutput = Join-Path $serverData "script-output"
Test-Path -LiteralPath $scriptOutput -PathType Container
Get-ChildItem -LiteralPath $scriptOutput -Force
```

```bash
script_output='C:\path\to\server-data\script-output'
test -d "$script_output" && find "$script_output" -maxdepth 2 -type d
```

Validate the pairing by listing existing files, issuing a read-only file-producing
command, and confirming a new parseable JSON file appears:

```python
from pathlib import Path
from orchestrator.game_bridge import GameBridge, load_json

bridge = GameBridge(
    Path(r"C:\path\to\server-data\script-output"),
    host="127.0.0.1", port=27017, password="planner_test",
)
try:
    path = bridge.request_snapshot(surface="nauvis")
    print(path, load_json(path)["tick"])
finally:
    bridge.close()
```

Do not delete old reports. `GameBridge` snapshots existing filenames before the command
and waits for a new parseable file.

## Read-only Diagnostic Cookbook

The Lua below reads state, but `/sc` remains an arbitrary-code interface.

### Registration, surfaces, and forces

```text
/help snapshot
/help export_recipe_catalog
/help research_status
/help inspect_sandbox_topology
/help verify_electronics_execution
/sc local s={};for n,_ in pairs(game.surfaces) do s[#s+1]=n end;table.sort(s);local f={};for n,_ in pairs(game.forces) do f[#f+1]=n end;table.sort(f);rcon.print("surfaces="..table.concat(s,",").." forces="..table.concat(f,","))
```

### Supported exports

```text
/snapshot nauvis
/export_recipe_catalog player
/research_status {"force":"player","technology":"automation"}
/inspect_sandbox_topology
/verify_electronics_execution
```

The last two inspect `planner-sandbox`; a missing sandbox is an expected useful failure
on a real-base-only server.

### Machine status

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local names={};for k,v in pairs(defines.entity_status) do names[v]=k end;local o={};for _,e in pairs(s.find_entities_filtered{force=f,name={"assembling-machine-2","electric-furnace","electric-mining-drill"}}) do local ok,r=pcall(function() return e.get_recipe() end);o[#o+1]=((ok and r) and r.name or e.name).."@("..e.position.x..","..e.position.y..")="..(names[e.status] or "?") end;table.sort(o);rcon.print(table.concat(o," | "))
```

### Logistic coverage and inventory

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local o={};for _,c in pairs(s.find_entities_filtered{force=f,type="logistic-container"}) do local n=c.logistic_network;o[#o+1]=c.name.."@("..c.position.x..","..c.position.y..")net="..(n and "YES" or "NONE") end;table.sort(o);rcon.print(table.concat(o," | "))
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local total=0;for _,c in pairs(s.find_entities_filtered{force=f,type={"container","logistic-container"}}) do local inv=c.get_inventory(defines.inventory.chest);if inv then total=total+inv.get_item_count("automation-science-pack") end end;rcon.print("automation-science-pack in chests="..total)
```

### Belt movement

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local n,active,total=0,0,0;for _,e in pairs(s.find_entities_filtered{force=f,type={"transport-belt","underground-belt"}}) do n=n+1;local c=0;for i=1,e.get_max_transport_line_index() do for _,item in pairs(e.get_transport_line(i).get_contents()) do c=c+(item.count or 0) end end;if c>0 then active=active+1;total=total+c end end;rcon.print("belts="..n.." with_items="..active.." items="..total)
```

### Electric networks and closest gap

Discover network IDs dynamically; never assume names such as `net2` or `net4` remain stable across saves:

```text
/sc local s=game.surfaces["nauvis"];local f=game.forces["player"];local nets={};local poles={};for _,e in pairs(s.find_entities_filtered{force=f,type="electric-pole"}) do local id=e.electric_network_id or 0;nets[id]=(nets[id] or 0)+1;poles[#poles+1]={e=e,id=id} end;local o={};for id,n in pairs(nets) do o[#o+1]="net="..id.." poles="..n end;table.sort(o);rcon.print(table.concat(o," | "));local best=nil;for i=1,#poles do for j=i+1,#poles do if poles[i].id~=poles[j].id then local a,b=poles[i].e,poles[j].e;local dx=a.position.x-b.position.x;local dy=a.position.y-b.position.y;local d=math.sqrt(dx*dx+dy*dy);if not best or d<best.d then best={d=d,a=a,b=b,ai=poles[i].id,bi=poles[j].id} end end end end;if best then rcon.print("closest_gap="..best.d.." net="..best.ai.." "..best.a.name.."@("..best.a.position.x..","..best.a.position.y..") <-> net="..best.bi.." "..best.b.name.."@("..best.b.position.x..","..best.b.position.y..")") else rcon.print("closest_gap=NONE") end
```

This reports topology only. Bridging a gap is a separate, state-changing planning decision requiring authorization.

### Independent invariant measurement

```powershell
python -m tools.verify_factory_invariants --rcon-host 127.0.0.1 --rcon-port 27017 --rcon-password planner_test --surface nauvis --json
```

Module execution works. Direct `python tools/verify_factory_invariants.py` currently
fails with `ModuleNotFoundError` because of its import path.

## Registered Commands

Classification describes effect, not authorization. Do not test state-changing commands
on a live server without explicit approval and valid payloads.

### Read-only/export (6)

| Command | Purpose | Output directory |
| --- | --- | --- |
| `/snapshot [surface]` | Export deterministic surface state | `factorio_mod/snapshots/` |
| `/export_recipe_catalog [force]` | Export recipes/raw resources | `factorio_mod/recipe_catalogs/` |
| `/research_status [JSON]` | Export force/research state | `factorio_mod/research_reports/` |
| `/export_ghost_observation` | Export sandbox ghosts | `factorio_mod/ghost_observations/` |
| `/inspect_sandbox_topology` | Inspect sandbox topology | `factorio_mod/topology_reports/` |
| `/verify_electronics_execution` | Measure sandbox invariants | `factorio_mod/live_execution_reports/` |

### State-changing (13)

| Command | Mutation | Output directory |
| --- | --- | --- |
| `/create_planner_world <JSON>` | Create/explicitly reset planner world | `factorio_mod/world_reports/` |
| `/apply_ghost_plan <JSON>` | Render sandbox ghosts | Console/status only |
| `/execute_ghost_plan <JSON>` | Place authorized ghosts | `factorio_mod/execution_reports/` |
| `/execute_construction <JSON>` | Let bots construct authorized ghosts | `factorio_mod/construction_reports/` |
| `/execute_upgrade_plan <JSON>` | Apply authorized upgrades | `factorio_mod/execution_reports/` |
| `/execute_deconstruction_plan <JSON>` | Mark authorized deconstruction | `factorio_mod/execution_reports/` |
| `/reconcile_sandbox_topology <JSON>` | Reconcile/explicitly reset topology | `factorio_mod/topology_reports/` |
| `/seed_ore_patches <JSON>` | Seed resource entities | `factorio_mod/ore_seed_reports/` |
| `/ensure_sandbox_scaffolding <JSON>` | Provision sandbox infrastructure | `factorio_mod/scaffold_reports/` |
| `/seed_water_lakes <JSON>` | Seed bounded water terrain | `factorio_mod/water_seed_reports/` |
| `/build_layout_plan <JSON>` | Build authorized positioned layout | `factorio_mod/layout_reports/` |
| `/set_research <JSON>` | Queue force research | `factorio_mod/research_reports/` |
| `/spawn_construction_spidertron [JSON]` | Spawn/equip sandbox spidertron | `factorio_mod/spidertron_reports/` |

`GameBridge` constants cover snapshots, ghost observations, execution, construction,
scaffolding, ore/water seeding, layouts, live verification, research, topology, and
recipe catalogs. It has no current collector for `world_reports` or
`spidertron_reports`. `apply_ghost_plan` writes no report.

## Real-base Rule

Legacy sandbox paths often default to `planner-sandbox` and force `planner`. Real-base
workflows must explicitly pass surface `nauvis` and force `player`. Confirm those names
exist before mutation.

```powershell
python tools\autonomous_run.py produce automation-science-pack `
  --surface nauvis --force player `
  --rcon-host 127.0.0.1 --rcon-port 27017 --rcon-password planner_test `
  --script-output "C:\path\to\server-data\script-output" `
  --reference-point 3 -1 --max-iterations 20
```

This example is state-changing and requires explicit authorization.

## Offline Belt-bridge Troubleshooting

`planners.belt_bridge._route_points` is an internal diagnostic helper, not a stable public API. At a corner, the corner belt must face the **outgoing** leg; the preceding tile continues to face the incoming leg.

```powershell
python -m pytest tests/test_belt_bridge.py -q
```

The current tests cover corner orientation and fail-closed `bridge_chest_to_chest` behavior: an obstacle run beyond the selected underground tier's reach raises instead of emitting an invalid tunnel; a blocked tunnel endpoint raises because both sides must be free; and emitted underground pairs validate against the BuildPlan schema. Run this offline before a live retry. Do not delete entities or increase reach constants without prototype evidence.

## Parameterized Server-launch Anatomy

This is anatomy, not authorization to launch. Use durable operator-controlled paths, not ephemeral Claude/temp-session directories:

```powershell
$factorioExe = "E:\Games\Factorio\bin\x64\factorio.exe"
$serverData = "C:\path\to\durable-server-data"
$args = @(
  "--config", (Join-Path $serverData "config.ini"),
  "--mod-directory", (Join-Path $serverData "mods"),
  "--start-server", (Join-Path $serverData "save.zip"),
  "--server-settings", (Join-Path $serverData "server-settings.json"),
  "--port", "34199",
  "--rcon-port", "27017",
  "--rcon-password", "planner_test"
)
$stdout = Join-Path $serverData "factorio-stdout.log"
$stderr = Join-Path $serverData "factorio-stderr.log"
Start-Process -FilePath $factorioExe -ArgumentList $args `
  -RedirectStandardOutput $stdout -RedirectStandardError $stderr
```

Launching/restarting is a lifecycle mutation and may require UAC. Obtain explicit user authorization first; do not silently add `-Verb RunAs` or start a second server.

## Failure Matrix

| Symptom | Likely cause | Safe next check |
| --- | --- | --- |
| Auth traceback | Wrong password | Verify launch settings; do not brute-force |
| `WinError 10061` | Server down/wrong port | Confirm process and explicit port |
| Tick works; `/help` fails | Mod absent/disabled/stale/wrong server | Compare loaded mod and deployed revision |
| Little/no RCON response | File-only report or silent rejection | Inspect expected new report; use `GameBridge` |
| Invalid snapshot surface is silent | Handler returned without RCON error | Enumerate surfaces, retry valid read-only export |
| `script-output` missing | Wrong data directory | Reconcile path with server launch/config |
| Wait timeout | Wrong tree, paused server, stale mod, error, slow export | Check log/subdir/`auto_pause`; do not rerun mutations |
| JSON schema failure | Drift, stale/partial file, serialization bug | Check newest tick, revision, matching schema |
| Sandbox reported missing | No `planner-sandbox` | Expected for real-base-only state |
| Direct invariant script import error | Broken direct-script path | Use `python -m tools.verify_factory_invariants` |
| Failure references code absent now | Stale import/process/worktree | Record provenance; rerun only if authorized |

Bad credentials and closed ports currently produce uncaught tracebacks from
`rcon_client.py`; the final exception is the useful signal.

## Deployment and Restart Boundary

Repository Lua is not automatically loaded by a running server. Command registration and
Lua changes require the intended mod deployment and a server restart/save reload.

Do not copy/redeploy a mod, replace a mod folder, run `/server-save`, `/quit`, reset or
reconcile state, or restart/launch a server without explicit user authorization. When
authorized, verify hashes and launch arguments, preserve the save, then repeat tick and
`/help` preflight after restart.

## Safe Escalation

1. Stop after the first clear failure; avoid repeated state-changing retries.
2. Capture the exact command with secrets redacted, endpoint, data path, code/deployed
   revisions, report path/tick, and final error.
3. Classify transport, registration, output-path, schema, live-state, or Python failure.
4. Propose the smallest read-only next check.
5. Ask before deployment, save/reset, lifecycle, or autonomous actions.

## Verified Snapshot: 2026-07-27

These are observations from one local development server, not permanent truth:

- Tick and `/help` probes for the five primary read-only commands worked.
- `GameBridge` exported a schema-valid `nauvis` snapshot with 99,441 entities.
- The schema-valid `player` recipe catalog had 640 recipes and 14 raw resources.
- Automation research status exported successfully.
- Topology reported no `planner-sandbox`; electronics verification produced the
  schema-valid expected failure for that missing surface.
- Module-form invariant verification worked read-only on `nauvis`.
- A read-only pole diagnostic found 24 poles split across two networks (19 and 5), with a closest cross-network gap of 15.5 tiles.
- Targeted tests reported 48 passed and 3 skipped, plus a pytest cache permission warning.
- A captured autonomous log reported `bring_stage_up` undefined while current source
  defines it, pointing to stale process/import provenance rather than a settled code bug.



--- FILE: docs/architecture.md ---

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



--- FILE: docs/data_contracts_and_determinism.md ---

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



--- FILE: docs/planner_and_execution.md ---

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
