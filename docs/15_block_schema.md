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
