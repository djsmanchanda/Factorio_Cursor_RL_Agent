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
