<!-- Path: docs/rl/Laya_Land_Grid.md | Purpose: Design of Laya's grid land model: fixed parcels per purpose with a corridor lattice, built to scale to a 5k x 5k map. -->
# Laya land grid

## Item blocks (current model)

Each item gets one reserved **block** beside its inputs, sized for its future lines,
and grows inside it before needing new land (`training/laya_site_plan.base_zones`,
`training/laya_lines.line_candidates`):

- Plate blocks are 50 deep where they fit (one 18-furnace line facing the patch, or
  three line columns along it), then 34, then 15, run at least 50 along the patch
  (`BLOCK_SPAN`), and are clipped to the build-validation box.
- Lines are enumerated nearest the block's anchor first (the block point nearest its
  ore); the nearest legal site(s) carry `next_slot`, and the doc rule
  `mission.item-blocks` weights other sites x0.2, so lines pack from the near edge.
- Both line sizes (6 and 18 furnaces) are offered.

The 64-tile parcel lattice below is an optional, larger-scale layout (`land_model: grid`).

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

Implemented: `LiveWorld.survey` runs chunked read-only Lua (`training/laya_land_grid.survey`,
256 x 256 per call) summing resource tiles and amounts per parcel; the grid plan uses it
within `PLAN_RADIUS` (6 parcels) and saves it as `evidence/land-survey.json`. Live check
on `mod_playground`: 0.3 s for +-448 tiles, 72 parcels with resources, and the western
crude field (~288 tiles out) planned as four `oil:crude-oil` parcels.

## Rules Laya reads

The strategy guidelines quote the grid rules (`Laya_Strategy_Guidelines.md`,
Planning): builds stay inside one parcel, corridors carry only transport and
power, and a purpose expands into the adjacent parcel before a distant one.

## Status

Matched runs (raw start, C3 window, 8 seeds each):

- Seeds 101-108, local scan: grid C2 4/8, C3 3/8; patch C2 3/8, C3 3/8.
- Held-out seeds 201-208, with the wide survey: grid C2 0/8; patch C2 5/8, C3 2/8.
  Copper failed with `scope`: the survey moved copper's plan to parcels straddling
  the saved-world action box (+-128 around the map origin, `training/laya_saved_world.BOUNDS`),
  which holds only about 4 x 4 parcels. Built parcels are now kept inside it
  (`actionable`), but no in-box parcel touches the copper parcels, so their hauls fail.

**Blocker for the grid default: the action area.** Build validation, the resource
scan and the build authorization all use that fixed box in the shared adapter. The
grid needs it to grow with the planned parcels (per-request bounds covering the
parcels a candidate touches). That change belongs to the shared adapter and is
coordinated with its owner; until then `land_model` stays `patch`.

`land_model: grid` is a runner setting (variant `docs/rl/laya_variants/grid-land.json`),
compared against the patch-rectangle plan before it becomes the default.
