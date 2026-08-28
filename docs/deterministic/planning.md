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
- Site persistent refineries by the complete transport bill: preserve legal
  input/output flow, minimize ore-belt plus plate-belt length, then prefer the
  output nearer current demand. Reserve the full expansion footprint at that
  site so later six-furnace modules can extend without relocation.

## Production lifecycle invariants

- Iron and copper start with one removable direct stack: one drill outputs
  straight into one electric furnace, then one inserter publishes plates to a
  provider chest. Stone-brick uses the same output stack with two perpendicular
  drills feeding its furnace. These starters use no belts, requester chest, ore
  intake, or bot haul, and may use small patches unsuitable for a persistent
  district.
- Startup builds the iron, copper, then stone-brick starter before attempting any
  complete mine-to-refinery foundation. Once a starter produces plates, the
  complete system's missing belts and inserters are ordinary construction
  demand and must remain visible to the mall.
- A starter drill is temporary bootstrap capacity, not a one-drill persistent
  resource district. Exclude it from managed-mine discovery and phase counts
  while the independent six-drill belt collector and refinery are built.
- Migration is build -> validate -> retire. The bootstrap remains intact until
  the direct mine, continuous ore belt, refinery, power, and plate output are
  built and observed healthy. Only then may the starter drill, furnace,
  inserter, and provider chest be removed. Its grid pole may remain as shared
  service infrastructure. Legacy requester cells are recognized only so old
  saves can retire them; new starts must not create one.
- A migration is incomplete while any recognized starter chest or furnace
  remains. Producing plates somewhere else is not sufficient evidence.
- Extraction grows in complete six-drill checkpoints:
  `6 -> 12 -> 24 -> 48 -> 96`, but measured demand chooses when to advance.
  Mine, transport, and refinery capacity move as one coherent increment.
- Managed paired mines own their power grid. One substation sits immediately
  outside each three-column/six-drill module and directly wires adjacent mine
  modules; its wires may cross the ore patch. Mine planning must not replace
  this lattice with a generic medium-pole bridge or reserve the mine interior
  against its own substations. A longitudinal 12-drill expansion adds its
  second grid substation, while a splitter-fed parallel row adds matching lower
  grid substations.
- After all three starters, startup opens a direct six-furnace iron foundation,
  then copper, then stone-brick. Between those explicit raw steps, a standing
  intermediate may start only when every direct input is already working or
  has produced output: gears follow live
  iron, cable follows live copper, and circuits follow their live feeders.
  Intermediate requests never recursively choose or open a missing raw
  foundation. Steel, oil, and later materials remain demand-driven.
- Opening iron and copper foundations use regular belts throughout. Their
  complete mine, haul, and refinery bill is queued against the working regular
  belt producer; partial fast-belt stock cannot promote the blueprint into a
  tier whose producer is still gated.
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
- Construction stock is allocated through an episode-scoped material ledger.
  Every named project records its complete item bill, current reservation,
  source producer, expected rate, and ETA. A compact producer reserves its
  whole standalone cell plus one recipe craft before it recursively schedules
  prerequisites; nested producers therefore protect every requester/provider
  chest needed to reach first output. Starter caps limit idle inventory only:
  a blocking project raises its producer target to the exact scheduled bill.
  Producer reservations remain held through construction and release only
  after measured output; an impossible self-seed is a typed supply wait rather
  than a generic no-progress failure.
- A compact producer that needs its own requester chest may borrow one duplicate
  copper-cable or gear assembler long enough to make the aggregate requester
  seed reserved by the ledger. The borrowed cell recursively makes missing
  solid prerequisites first, adds a uniquely tagged ingredient section to its
  existing requester, and restores its original recipe and request group after
  the seed is measured. Configuration is existing-entity-only and fails closed
  if the planner-owned cell has disappeared; it never creates a replacement.
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
  that block, and start the pipe on the exact external tile beyond the complete
  3x3 pumpjack footprint before extending a long power, construction, or pipe
  corridor back toward the factory.
- The first refinery may use basic oil processing as bootstrap. Every later oil
  expansion uses advanced oil processing as one complete refinery-and-cracking
  block; heavy and light outputs may not be left without cracking consumers.
  Size refinery count against observed pumpjack throughput: at speed 1 a well
  supplies `10 * yield * (1 + productivity)` crude/s, while one refinery
  consumes 20 crude/s. A second refinery is not capacity when the wells cannot
  feed it.
- Accumulator production is an explicit chemical chain: sulfur plus iron and
  water make sulfuric acid, then acid plus iron and copper make batteries.
  Fluid-bearing recipes never fall through the solid mall-line planner.
- Plastic selects a coal patch local to the oil district rather than reusing a
  remote base-mall mine. Place its chemical plants near the midpoint between
  that coal source and the refinery, and feed coal by one continuous belt; a
  plastic requester and long construction-bot coal haul are not valid steady
  transport.
- Chemical construction coverage reserves the complete future pipe and machine
  footprint before siting roboports. The complete required roboport chain is
  placed and power-connected without synchronously waiting for charge; only bot
  activity waits on charging, never coverage geometry or production planning.
  Every local oil substation is connected as soon as the plan is submitted
  rather than after all pipe ghosts complete.
- A remote chemical district is submitted as independently affordable packets:
  coal belt, power backbone, refinery/plastic machines, crude pipeline, plastic
  petroleum pipeline, sulfur machines, sulfur petroleum pipeline, and water
  pipeline. Reserve the complete future footprint first, then release every
  packet whose construction-item supply chain is active. Producers and bots run
  concurrently; do not warehouse the complete district bill before ghosting it.
  Construction polling spends one controller wait per window, not per sample,
  and a visibly shrinking local backlog may use the existing progress extensions.
- Offshore pumps require a straight orthogonal shoreline: water across the
  intake width, land across the output width, and the first pipe on the
  immediately adjacent land-side connector tile. The Factorio entity direction
  faces into water, opposite its land-side pipe output. Diagonal shoreline
  corners are not candidates.
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
Every Nauvis template contains one accumulator per solar panel; the power bill
and the placed geometry use the same 1:1 ratio.
