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
