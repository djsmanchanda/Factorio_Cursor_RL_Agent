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
5. Water terrain is not a normal pipe tile. A span with at most nine water tiles uses land endpoints; a longer straight crossing alternates pipe-to-ground spans of at most ten tiles and pairs of landfill tile ghosts. The landfill ghosts must be built before their pipe-to-ground ghosts are submitted, and each consumes real landfill from construction stock.
