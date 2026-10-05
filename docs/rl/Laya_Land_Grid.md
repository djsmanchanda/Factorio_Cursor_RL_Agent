<!-- Path: docs/rl/Laya_Land_Grid.md | Purpose: Design of Laya's grid land model: fixed parcels per purpose with a corridor lattice, built to scale to a 5k x 5k map. -->
# Laya land grid

Laya will eventually oversee a map of up to 5,000 x 5,000 tiles. Land is planned
as a **grid of parcels** rather than rectangles drawn around nearby patches, so
that every later build has a predictable place and a predictable route.

## Grid

- **Parcel pitch 64 tiles** (two chunks). The grid is anchored so the starter
  roboport sits in the middle of the **base parcel**; the anchor is stored in the
  plan (`grid.origin`) and never moves.
- Each parcel reserves its first **6 tiles on the west and north edges** as
  corridor. Together they form a continuous lattice of 6-wide corridors across
  the whole map for belts, pipes and poles (and later rails). Only corridor
  entities (belts, undergrounds, splitters, pipes, poles) may stand in a corridor.
- A parcel interior is **58 x 58**: three parallel 18-furnace lines (16-tile
  pitch), about 54 furnaces or 33 plates/s.
- 5k x 5k is about 6,100 parcels; corridors are checked arithmetically, never
  stored as zones, so planning cost does not grow with map size.

## Parcels and purposes

| Purpose | How it is assigned |
|---|---|
| `ore:<resource>` | Every parcel whose interior holds enough survey samples of a usable patch. |
| `smelt:<plate>` | One free parcel per plate, the one nearest its ore parcels (ties toward the base). More are allocated next to it when its lines fill. |
| `mall` | The mall site chosen by the plan decision, inside or beside the base parcel. |
| base parcel | Kept free of smelting; holds the starter area and mall growth. |
| free | Unassigned until a purpose needs land. |

Allocation (`training/laya_land_grid.allocate`) picks the free parcel nearest the
purpose's inputs, preferring parcels adjacent to the same purpose so production
grows in contiguous blocks. Later purposes (chemistry, science, rails) use the
same call.

## Observation at scale

The per-decision query stays local (machines, ports, stock). Land planning needs
the resource map instead: a one-time **wide survey** summarising resource amount
per parcel, refreshed rarely, replaces the +-128-tile sample scan when the map
grows. The nearest crude field in `mod_playground` is about 288 tiles away, so
plastic is the first purpose that needs it.

## Rules Laya reads

The strategy guidelines quote the grid rules (`Laya_Strategy_Guidelines.md`,
Planning): builds stay inside one parcel, corridors carry only transport and
power, and a purpose expands into the adjacent parcel before a distant one.

## Status

`land_model: grid` is a runner setting (variant `docs/rl/laya_variants/grid-land.json`),
compared against the patch-rectangle plan before it becomes the default.
