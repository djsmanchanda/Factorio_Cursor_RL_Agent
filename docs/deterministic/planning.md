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
  `6 -> 12 -> 24 -> 48 -> 96`. Early direct iron deliberately advances to
  12, then 24, because most construction demand consumes iron or an iron
  derivative. Larger phases remain demand-driven.
- Metal refineries grow with their mine in complete six-furnace modules. A
  12-drill phase targets 12 furnaces and a 24-drill phase targets 24; mining
  productivity headroom must not skip a module or double the requested block.
- Planner-owned roboports are movable service infrastructure. When one blocks
  an owned refinery extension, place and power a connected replacement outside
  the future footprint before removing the old port. Production infrastructure
  remains authoritative and must be routed around or reported as a conflict.
- Preserve a bounded straight collector beyond the first drill row before the
  haul may turn. Expand longitudinally first; if that owned corridor is full
  or blocked, add a parallel collector through an explicit splitter instead
  of opening a duplicate mine.
- Furnace expansion must include enough mine and transport capacity to feed
  it. A coherent iron mine/refinery expansion may be placed as pending ghosts
  before every construction item is stocked; collision, ownership, and
  duplicate-pending checks still run first, and missing items stay queued.
  A supply-starved refinery triggers mine or transport repair, never an
  isolated furnace block.
- Multiple consumers of one resource require an explicit splitter/manifold and
  throughput budget. Independent belts may not overwrite or reverse the same
  collector head.
- Oil refining, plastic, and sulfur form a source-local district. Site the
  chemical block near the selected crude-oil source, rotate the pumpjack toward
  that block, and connect the exact pump connector before extending a long
  power, construction, or pipe corridor back toward the factory.
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
