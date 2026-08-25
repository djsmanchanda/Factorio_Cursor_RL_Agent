<!-- Path: docs/deterministic/planning.md | Purpose: Keep useful deterministic planning principles without making them RL constraints. -->

# Deterministic planning

## Current components

- `LocalLayoutPlanner`: entity placement and local connections.
- `CityPlanner`: larger zones, corridors, and interfaces.
- `PlanetPlanner`: planet-level production and transfers.
- `InterplanetarySupervisor`: global flows and recovery.

These names describe intended responsibilities, not permission to build speculative layers before current scenarios need them.

## Planning principles

- Plan machines, inputs, outputs, power, logistics, and expansion space together.
- Prefer a continuous direct belt over chest and inserter hops when both solve the same transport problem.
- Route around established infrastructure. A blocked endpoint should trigger another candidate or a safe failure.
- Diagnose supply, delivery, inserter throughput, machine speed, and machine count in that order.
- Repair existing capacity before duplicating it.
- Keep mall reserves distinct from sustained intermediate demand. Promote intermediates to full lines when measured demand justifies it.
- Treat fluids as type-safe networks; never mix fluids through an implicit shared pipe.
- Base capacity on rates and live game facts, not machine counts alone.

## Production lifecycle invariants

- A requester-fed iron or copper smelter is temporary bootstrap infrastructure.
  Once belt production can fund a complete direct mine-to-refinery system, the
  controller must build that direct system without waiting for more demand.
- Migration is build -> validate -> retire. The bootstrap remains intact until
  the direct mine, continuous ore belt, refinery, power, and plate output are
  built and observed healthy. Only then may its requester/provider chests,
  temporary furnaces, and mine-side logistic intake be removed.
- A migration is incomplete while any recognized bootstrap chest or furnace
  remains. Producing plates somewhere else is not sufficient evidence.
- Extraction grows in complete six-drill checkpoints:
  `6 -> 12 -> 24 -> 48 -> 96`, but measured demand chooses when to advance.
  Mine, transport, and refinery capacity move as one coherent increment.
- Startup opens a direct six-furnace iron foundation, then copper. Between
  those explicit raw steps, a standing intermediate may start only when every
  direct input is already working or has produced output: gears follow live
  iron, cable follows live copper, and circuits follow their live feeders.
  Intermediate requests never recursively choose or open a missing raw
  foundation. Stone, steel, oil, and later materials remain demand-driven.
- Metal refineries grow with their mine in complete six-furnace modules. A
  12-drill phase targets 12 furnaces and a 24-drill phase targets 24; mining
  productivity headroom must not skip a module or double the requested block.
- The first persistent steel line is six furnaces, placed beside and belt-fed
  from iron. It waits for at least the 12-furnace/12-drill iron checkpoint so
  steel cannot consume the entire iron line while belts and gears are starved.
- Planner-owned roboports are movable service infrastructure. When one blocks
  an owned refinery extension, place and power a connected replacement outside
  the future footprint before removing the old port. Production infrastructure
  remains authoritative and must be routed around or reported as a conflict.
- Preserve a bounded straight collector beyond the first drill row before the
  haul may turn. Expand longitudinally first; if that owned corridor is full
  or blocked, add a parallel collector through an explicit splitter instead
  of opening a duplicate mine.
- Furnace expansion must include enough mine and transport capacity to feed
  it. Any coherent demanded mine/refinery expansion may be placed as pending
  ghosts before every construction item is stocked only when every missing
  item has a producer and every solid prerequisite traces back to active raw
  extraction. Collision, ownership, and duplicate-pending checks still run
  first, and missing items stay queued. A supply-starved refinery triggers
  mine or transport repair, never an isolated furnace block.
- The same complete-chain condition applies to every other coherent blueprint.
  The initial construction window is five minutes. Diagnose and remedy its
  local ghost backlog throughout that window; only a flat unresolved job may
  fail at the end (upstream production, delivery, bot or roboport capacity,
  coverage, or power).
- A partially built or unconfigured furnace cluster is pending construction,
  not recoverable capacity. Recovery may adopt only an exact planner-shaped
  six-furnace module; until a direct refinery has produced plates, repair its
  power/transport path instead of expanding its mine or selecting another site.
- A position inside a pole's supply area is not evidence that a power bridge
  was built. Capacity planning distinguishes existing coverage from a submitted
  network bridge and keeps measuring the actual connected grid.
- The base has one primary electric grid: every new pole, substation, roboport,
  mine, and production district connects to the highest-generation network.
  Capacity telemetry measures that same network, never a nearer island.
- Multiple consumers of one resource require an explicit splitter/manifold and
  throughput budget. Independent belts may not overwrite or reverse the same
  collector head.
- Oil refining, plastic, and sulfur form a source-local district. Site the
  chemical block near the selected crude-oil source, rotate the pumpjack toward
  that block, and connect the exact pump connector before extending a long
  power, construction, or pipe corridor back toward the factory.
- Chemical construction coverage reserves the complete future pipe and machine
  footprint before siting roboports. Remote coverage waves defer while their
  ports remain power-starved, and every local oil substation is connected as
  soon as the plan is submitted rather than after all pipe ghosts complete.
- Offshore pumps require a straight orthogonal shoreline: water across the
  intake width, land across the output width, and the first pipe on the
  immediately adjacent land-side connector tile. Diagonal shoreline corners
  are not candidates.
- A product does not become a mine/refinery stage merely because its recipe has
  one raw-resource ingredient. Direct extraction is an explicit recipe role;
  assembler products such as landfill stay in the assembly path.
- A test may claim migration success only from the complete lifecycle outcome,
  including teardown. Mocked helper calls and source-text assertions are not
  acceptance evidence.

These are also useful RL priors and reward features. They are not a catalog the learned policy must copy.

## Deterministic power districts

Solar expansion uses fixed rectangular templates rather than opportunistic
clear-spot chains. A candidate unit has a complete footprint, clearance box,
poles or substations, accumulator bank, connection points, and adjacency
offset. The controller classifies each lattice cell, rejects a candidate as a
whole on any live entity, ghost, terrain, deconstruction order, reservation, or
pending-plan conflict, and builds at most one fully funded unit before
remeasuring. Power plans are tagged atomic, so the executor performs a
whole-footprint preflight before its first placement and refuses the complete
unit on any blocked coordinate. Sizing compares usable solar plus firm
generation and measured connected accumulator storage with bounded peak demand,
including night energy and recharge surplus; it stops when that metric converges.
