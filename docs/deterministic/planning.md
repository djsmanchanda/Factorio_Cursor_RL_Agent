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
- Bridges from existing output belts preserve their observed heading, including
  belt-to-chest conversion feeds. The first turn belongs on clear ground, not
  on the live source belt; detours retain the same source-direction constraint.
- Route around established infrastructure. A blocked endpoint should trigger another candidate or a safe failure.
  An exhausted belt-route search reports `belt_bridge_unroutable` with source/
  destination and surveyed belt endpoints, direction constraints, attempted
  tiers, route limit, and planner reason. These details survive mission-blocker
  persistence; diagnostics do not relax route legality.
- Diagnose supply, delivery, inserter throughput, machine speed, and machine count in that order.
- Repair existing capacity before duplicating it.
- Change the tier of an owned machine through Factorio's native upgrade order,
  never an overlapping ghost or a destroy-and-rebuild pair. Scope every order
  to the explicit surface and force, require the expected source prototype at
  the exact position, and preserve the active recipe as an ownership guard.
  Construction bots perform the replacement and recover the old machine. A
  downgrade is legal only when the replacement prototype supports the active
  recipe; completion is observed only after the target prototype is live.
- Keep mall reserves distinct from sustained intermediate demand. Promote intermediates to full lines when measured demand justifies it.
- Treat fluids as type-safe networks; never mix fluids through an implicit shared pipe.
  Opening oil links use bounded land, underground-bypass, wider-land, then
  water-crossing attempts. Reuse the validated route for both placements and
  purity segments; never replan already submitted transaction geometry.
- Generate fluid-machine layouts through rotation, reflection, and structural
  expansion operators. Reject collisions, unreachable attachments, and fluid
  mixing first; among legal candidates minimize the complete routed pipe bill,
  pole bill, occupied land, and expansion seam. A compatible even-sized row may
  grow as two opposing half-rows sharing one same-fluid connector seam instead
  of extending indefinitely in one direction.
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
- Migration is build -> first delivered output -> retire. The bootstrap may
  retire once an owned replacement furnace completes a craft and the matching
  item is observed in its exact provider chest, with generated power verified.
  Remaining drills, furnaces, or inserters need not be complete; their ghosts,
  reservations, and mall bills remain active. Check delivery during construction
  polling, not only after the full blueprint finishes. Then the starter drill, furnace,
  inserter, and provider chest receive exact deconstruction orders. Retirement
  waits for construction bots to recover those entities and their contents into
  logistics; it must never destroy them. After the production entities are
  gone, an empty starter-pole leaf is pruned back one pole at a time; pruning
  stops at a consumer-supplying pole or a pole needed for connectivity. Empty
  poles with multiple neighbours require an observed alternate copper path
  excluding that pole (bounded to 64 surveyed poles); geometric reach alone
  never licenses removal. Each removal is followed by a fresh survey. Legacy requester
  cells and their recognized mine-side
  intake follow the same recoverable teardown, but are recognized only so old
  saves can retire them; new starts must not create one.
- Once provisioning records a replacement origin, mine haul head, exact
  transport actions, and complete reservation, those identities are canonical
  across retries. A retry reconciles the same entities and ghosts; it must not
  extend the collector through its own haul route, re-route around that route,
  or resurvey a new refinery because the original block has become real or
  temporarily has no output. If another project consumed a construction item,
  reconciliation queues that exact shortage through the mall and retries the
  persisted district instead of aborting or replanning it.
- Later district expansion is a separate transaction from opening-foundation
  readiness. The original live six-furnace module remains sufficient lifecycle
  evidence while future modules construct, and recipe surveys may not undercount
  owned but temporarily unset furnaces. Persist a larger replacement footprint
  only after its executor submission is accepted; a rejected material preflight
  leaves the prior ownership record unchanged.
- Automation science may overlap final pioneer teardown only when both metal
  replacements retain their full lifecycle reservations, match the exact
  opening six-furnace module, and have measured output. Otherwise the gate
  resumes the named district and reports whether it must reserve the district,
  construct the replacement, or repair its power or transport. Starter absence
  alone is not the readiness signal.
- A migration is incomplete while any recognized starter chest or furnace
  remains. Producing plates somewhere else is not sufficient evidence.
- Extraction grows in complete six-drill checkpoints:
  `6 -> 12 -> 24 -> 48 -> 96`, but measured demand chooses when to advance.
  After plastic output releases the independent mall, iron demand continues
  the same doubling policy beyond 96 and may advance into later validated
  refinery generations. New iron transport uses fast belts once their producer
  is proven; held fast belts, undergrounds, and splitters replace exact
  ledger-owned yellow infrastructure opportunistically. Collision, ownership,
  and coherent mine/refinery checks still apply to every increment.
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
  Longitudinal mine expansion preserves the selected belt tier as well.
  Affordability checks never substitute transport prototypes in an expansion
  delta: existing collector tiles and exact removal actions keep their
  identities. Later changes use the ownership-checked native upgrade path.
- Metal refineries grow with their mine in complete six-furnace modules. A
  12-drill phase targets 12 furnaces and a 24-drill phase targets 24; mining
  productivity headroom must not skip a module or double the requested block.
  New-mine rows scale with the recipe's own ore-per-product ratio instead:
  iron/copper open three drills per row while stone-brick opens six, so 12
  drills feed 6 stone furnaces at the same tightness; narrow patches still
  fall back through patch-fit.
  The provider is a construction-storage side tap, not the main output path.
  It retains a fast inserter as the refinery grows; sizing that tap for all
  furnace output must not force a bulk-inserter/advanced-circuit dependency.
- Steel begins only after the opening iron district is verified and its pioneer
  retired. One compact furnace takes plates directly from an existing iron
  belt through an inserter and outputs to a provider; it has no feed belt or
  requester. Siting checks its complete footprint and the iron district's
  expansion reservations. If no belt-side site fits, a free ore patch may host
  a compact drill -> iron furnace -> inserter -> steel furnace seed instead.
  The funded seed and its exact identity persist per episode across retries.
  Its startup pole reserve and full construction bill share the same material
  project, `conversion_steel-plate`, at priority 100. A resumed active legacy
  `compact_steel_seed` reservation is merged into that owner before its duplicate
  claim is completed. Unrelated project reservations remain intact. Restarting
  preserves the full bill, including a submitted constructing state, instead of
  replacing it with the original two-pole estimate. A nested material shortage
  during conversion recovery queues its bill once and yields to the scheduler;
  it cannot recursively retry itself or swallow hard planner failures.
  Pipe production is not a prerequisite for this beltless steel seed.
  After advanced-circuit production is proven, a complete six-furnace steel
  refinery replaces it. A normal splitter is added beyond an unused iron-belt
  terminal, preserving the existing provider and leaving the second output
  available; no existing belt is reversed or removed. Candidate sites near
  the mall are compared by full feed-route cost before placement. Steel's
  measured furnace demand joins ordinary iron-capacity planning. Bots recover
  the seed after first replacement steel is observed in its provider, even
  while remaining machines construct; the delivery witness and retirement
  persist separately from full completion. Shared power remains protected.
  Both stages remain fully funded.
  Steel is a persistent conversion stage, not a mall recipe: it never claims a
  compact assembler slot or passes through mall reserve policy.
- Planner-owned roboports are movable service infrastructure. When one blocks
  an owned refinery extension, place and power a connected replacement outside
  the future footprint before removing the old port. Production infrastructure
  remains authoritative and must be routed around or reported as a conflict.
- End an east-flow collector immediately beyond its drill row, then route from
  that stable haul head. Longitudinal expansion owns the reserved corridor west
  of the head; it must not buy an unused eastward belt tail. If the owned west
  corridor is full or blocked, add a parallel collector through an explicit
  splitter instead of opening a duplicate mine.
- Furnace expansion must include enough mine and transport capacity to feed
  it. Any coherent demanded mine/refinery expansion may be placed as pending
  ghosts before every construction item is stocked only when every missing
  item has a producer and every solid prerequisite traces back to active raw
  extraction. Collision, ownership, and duplicate-pending checks still run
  first, and missing items stay queued. Submit the non-destructive mine growth
  once before waiting on refinery-growth ghosts; a later survey defers while
  those mine ghosts are pending instead of opening a duplicate batch. A
  construction/coverage/power/output wait yields to queued mall work instead
  of spending the whole pass polling. The pending district retains its bill
  and prevents another expansion from taking that pass; optional core-mall
  promotion also yields to binding construction. A submitted growth packet
  waiting for safe cutover retains binding material priority. No timeout
  increase or duplicate blueprint is needed to let its suppliers run. A
  supply-starved refinery triggers mine or transport repair, never an isolated
  furnace block.
- Construction stock is allocated through an episode-scoped material ledger.
  Each controller survey also reconciles the live remaining entity-ghost bill.
  Unfunded ghosts restore binding mall demand even after a producer batch was
  credited or a starter retired; historical crafts cannot satisfy still-visible
  unfunded ghosts. Stock-covered ghosts do not demand duplicate production.
  Every named project records its complete item bill, current reservation,
  source producer, expected rate, and ETA. A compact producer reserves its
  whole standalone cell plus one recipe craft before it recursively schedules
  prerequisites; nested producers therefore protect every requester/provider
  chest needed to reach first output. The cell starts building once half the
  bill is stocked and every missing bill item already has a scheduled supply
  chain; the reserved remainder arrives while ghosts construct instead of
  blocking placement behind the slowest ingredient. Starter caps limit idle inventory only:
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
  This is recovery after the mall has duplicate cells, not the cold-start
  source: no bootstrap profile supplies free requester chests. Existing stock
  or production must fund every chest; an impossible cold-start dependency is
  a real supply blocker, never permission to insert items.
  A permanent half-cell refresh also surveys its shared requester and clears
  obsolete `mall:*:<side>` sections for that half only. The opposite machine's
  side remains untouched, so one chest converges to at most its two live
  recipe groups instead of accumulating retired recipes.
  Stock-gate refreshes configure existing machines only: they reserve no
  replacement assembler. Resolve actual per-position tiers in one survey,
  including mixed upgrade rows, and refresh the gate again when a tier changes.
  Missing or ghost targets defer for re-observation rather than becoming a
  collision error or silently placing a replacement.
- Reduced supply begins entirely on assembling-machine-1 and regular inserters;
  neither upgraded tier is part of the starter contract. Solid compact cells,
  including the permanent assembling-machine-2 and fast-inserter producers,
  are assigned tier 1 from the live machine-category catalog. Once the first
  full iron foundation is built and its owned replacement has measured output,
  the mall queues AM2 replacements immediately, without waiting for copper,
  plastic, logistics-chest production, or the complete core mall. AM2 orders
  require proven AM2 production and transferable stock remaining after material
  reservations plus a four-item construction reserve. Fast-inserter upgrades
  retain their later core-mall gate. Upgrades use native in-place bot orders, limited to one machine per pass. Pending upgrades are excluded from later batches, and mixed AM1/AM2
  recipe rows remain one observable line throughout the transition.
  Until plastic has produced output, low-demand construction items are finite
  batches made by a recoverable recipe loan in an existing mall assembler.
  The loan changes the existing requester group and output gate, may mix the
  temporary product with earlier contents in the same provider, and restores
  the original recipe and requests after the batch. Permanent one-recipe mall
  slots become permanent only after plastic releases the independent mall.
  A finite cell's requester buffer is capped to the crafts in its current
  need-plus-margin batch plus 20% headroom (rounded up); it may not use the
  normal throughput window to warehouse construction components. Requester
  and buffer inventories are committed work-in-progress, not transferable
  construction stock: blueprint affordability and new-cell ingredient
  sourcing count only ordinary, passive-provider, and storage containers.
  The need-plus-margin squeeze applies only while starter metal carries the
  base; once the direct iron/copper replacements are healthy and released,
  pre-core items keep their grown standing reserve past the bill and build
  ahead instead of stopping at need-plus-margin.
  Optional core-mall promotion must yield unchanged supply waits to the
  queued-work scheduler in the same pass. A completed prerequisite may be
  stopped at its stock cap until its loan receives the next scheduling turn;
  that status alone is not a broken gate. Recipe configuration, restoration,
  or promotion consumes the pass for re-observation; observed craft progress
  alone must not let one optional batch monopolize scheduling. Coverage waits
  retain bounded polling. The same decision applies to forced-pool admission
  and chest-prerequisite admission.
  Concurrent recipe loans may run on different free cells: a batch for X and
  a batch for Y (or for X's precursors) each borrow their own cell and advance
  on their own passes, so the rotating pool makes several things at once. Each
  planner-owned cell hosts at most one loan, since paired halves share one
  passive provider. Only when no borrowable cell is free does a request for a
  different batch service the active loan through measured completion and
  restoration, defer the new batch, and retry it on the next pass; a busy or
  completed prior loan is never classified as an absence of borrowable
  capacity. Every controller pass also services completed finite loans after
  their stock demand retires, including batches with no optional spare margin.
  Their original recipe and requester group must be restored through the normal
  loan lifecycle; stock completion alone must not leave upstream capacity
  borrowed indefinitely. Completed spare ceilings also release without unrelated
  construction pressure; unfinished optional spares retain the existing handoff
  policy.
  Chemical-ladder handoffs use the same boundary: restoring a downstream loan
  ends the current pass, and the predecessor loan begins only after the next
  live observation confirms that restoration.
  A rotating slot may wait on a missing input only when that input has a live
  producer. It recursively switches to a missing solid assembler dependency
  when possible. If the missing dependency requires a furnace, fluid stage,
  extraction, or another external capability, it restores the active loan,
  establishes that capability, and only then borrows a slot again.
  Logistic chest promotion has an additional recipe gate: passive-provider and
  requester chests first establish the dedicated steel-plate capability, then
  wait for a working steel-chest mall batch or its exact stocked chest seed,
  and finally for a working advanced-circuit producer. Steel chests remain a
  one-machine temporary mall batch (with a small margin) until the core mall
  is self-sufficient; they never open a rate-sized standalone conversion line.
  Permanent core-mall cells are not loan donors. Advanced-circuit admission
  still walks the chemical ladder through oil and plastic first, so a borrowed
  chest cell never requests an input that the base cannot yet make.
  Downstream chemical-dependent batches require observed predecessor
  production, not merely a few held ingredients. Unknown production telemetry
  defers admission explicitly; it must not silently authorize the recipe.
  Every loan persists both its blocking bill and an optional spare ceiling:
  it may keep producing useful extras while the slot is idle, but a competing
  batch preempts it as soon as monotonic craft progress proves the blocking
  bill was made. Binding construction retires at its exact transferable bill,
  without waiting for the optional spare margin. Completing that bill or
  proving a batch complete while another construction bill is binding releases
  spare production without waiting for a competing allocator call. Standing
  mall topology yields to binding work, including the pass that retires its
  final material, so the district can observe completion first. Required
  feeders still follow the selected batch's dependency path.
  The borrowed machine's stock gate always tracks the loan's
  current step target and is refreshed on drift; a bill-frozen gate stranding
  a spare-phase loan is configuration drift, not progress. Foundation-blocked
  items rate 100 while queued and retire with their demand; a binding loan
  below its blocking bill is shielded from preempt by non-binding batches.
  After both metal districts validate and release their pioneers, the
  controller stops scarcity-sized recipe switching. Cheap, high-volume
  construction consumables (belts, inserters, poles, pipes, splitters, and
  circuits) round up to a complete item stack and to additional complete stacks
  when the bill is larger. Expensive production machines retain their exact
  deployment bill: four required AM2s must not become a fifty-machine batch.
  Other recipe intermediates also retain their exact blocking bill, so four
  required iron sticks cannot delay expansion behind a hundred-stick batch.
  Electronic circuits and splitters begin filling as
  non-binding reserve work while the stone district opens. Before that phase,
  low-demand batches target their blocking bill plus at least 20% spares
  (rounded up, covering requester/buffer WIP), and retire only once that
  transferable stock exists. Upstream belt batches additionally reserve the
  recipe-derived draw of queued belt consumers plus two crafts of input that a
  stock-capped assembler can preload; three splitters therefore add 20 belts
  (12 consumed and 8 retained) before the ordinary spare/WIP margin. A demand
  whose bill is met in
  transferable stock while its loan keeps advancing without accumulation
  retires as drained; the cell finishes spares in the background. A lagging
  build names its transferable shortfall, locked WIP, and pending ghosts
  instead of waiting on force-wide stock that bots cannot spend.
  When either blocking stack has more than one minute of measured backlog, an
  existing one-machine producer may claim a second reservation-funded slot in
  the phase-bounded bootstrap pool. A lone transport-belt cell facing a large
  backlog may do the same. The extra cell still needs its exact bill
  and shared provider; it is capacity allocation, not free starter supply.
  A cell bill whose shortfall is reserved but flowing -- scheduled producer
  chain plus spendable stock on hand -- draws from that flow instead of
  waiting out the reserve; a stagnant stockpile with no scheduled producer
  still blocks. A coherent foundation's combined preflight and its submitted
  mine/refinery packets share one reservation transaction even when their
  persisted project names differ; a retry may not count its child packet
  reservations as foreign stock and demand a duplicate bill.
  Before plastic, the compact mall has a hard global ceiling of twelve
  assemblers shared by anchors, permanent prerequisites, and rotating demand.
  After plastic, the layout separates forty-eight permanent per-item halves
  from twelve demand-driven halves. A recipe's first cell is allocated from
  the permanent bank; temporary duplicate capacity may use only the demand
  bank, and reclaims never take a permanent cell. Before plastic, core-mall
  requests may not bypass the shared ceiling; when it is full, core promotion
  or a blocked ordinary demand reclaims an idle completed non-anchor temporary
  slot and converts it in place. After plastic, any promotable solid item with
  measured demand above 5/s leaves the mall for a complete six-assembler direct
  line. This commonly applies to gears, copper cable, circuits, and belts. Its
  inputs use line-to-line belt delivery with logistic requesters disabled. Once
  a circuit request exceeds the
  capacity of two circuit cells (by live rate or queued work beyond their
  patience window), it is not assigned a third mall half.  The controller
  builds the first dedicated block instead: six electronic-circuit assemblers
  plus the recipe-derived nine copper-cable assemblers, expands copper and
  iron to their measured input rates, and releases the replaced compact halves
  only after the direct lines are healthy.  Copper cable enters the circuit
  input as a full continuous belt while iron sideloads the remaining lane;
  the resulting 36/s aggregate bus requires an express-or-better belt.  The
  provider chest is a side tap for construction storage; the primary belt
  remains continuous for downstream production.  The cable block's 27/s
  output still lands on one output lane, so it requires a turbo belt; the
  circuit input bus itself is express-or-better.
  Monotonic loan craft progress counts as controller progress even when bots
  consume each output before it can accumulate in provider stock. The loan tag
  persists the absolute stock target of every completed prerequisite step, so
  a consumed gear/circuit/belt batch advances toward the parent recipe instead
  of being manufactured again. Once a parent step starts, incidental stock
  churn cannot push the rotating assembler backward into a completed child.
  Pipe has an additional lifecycle boundary. Before plastic output, pipe is a
  finite rotating batch and the borrowed slot is restored. Once plastic
  releases independent cells, pipe may become permanent; at that point the controller
  converts a stocked low-demand building slot in place, reusing its assembler,
  requester, inserters, provider, and power instead of funding another compact
  cell.
- The controller's outer pass limit counts non-progress decisions. A pass is
  credited back when required stock grows, pending ghosts fall, a recipe loan
  advances, an observed built-roboport frontier moves closer to its declared
  coverage target, or the outstanding-work state changes. A replacement
  next-hop ghost may keep the aggregate ghost count flat, so frontier credit is
  keyed by surface, force, coverage purpose, and target and advances only on a
  strictly closer real roboport. A pass serves up to three
  ready mall tasks: each item is attempted at most once, and a deferred task
  does not prevent another ready peer from using the remaining pass budget,
  so independent cells and loans build concurrently instead of one per pass. The consecutive unchanged
  pass guard remains the tighter detector for real contradictions. Unchanged
  mall requester, provider-limit, and stock-gate configurations are submitted
  once per run and refreshed after any recipe-loan reconfiguration.
  Within a run, a dedicated mall provider limit and its item stock gate are
  monotonic: a smaller incidental demand cannot shrink a reserve already being
  filled. Deliberate shared-provider loan mode remains uncapped and restores the
  dedicated policy after the loan.
- Core-mall readiness distinguishes installed capacity from current crafting.
  A real powered, non-loaned assembler stopped by its verified item-specific
  logistic stock cap is ready when that same network holds the cap quantity.
  This exception does not prove chemical production or unlock plastic/advanced
  circuit milestones; those still require observed production.
- Pending mall batches are peers unless one appears in the selected item's
  recipe closure or declared compact-cell bill. A drill batch must therefore
  continue while splitter and inserter batches are also queued; treating every
  peer as a prerequisite creates a false circular wait around the single
  rotating assembler.
- Iron gears and copper cable are permanent mall anchors. Recipe loans may
  borrow only a duplicate, leaving at least one assembler on each recipe.
  Before a saturated transport-belt cell promotes to a six-machine shared
  line, the dynamic mall allocator borrows the second cable cell: its requester
  first makes any missing gears, then switches to belts, while the two baseline
  gear assemblers and one cable assembler remain assigned. The loan restores
  the cable recipe when its reserve is full or another batch needs the slot.
- A collision-checked additive blueprint does not wait for every construction
  item to have a producer. Stage its reachable construction coverage, submit
  the ghosts, and promote the persisted exact shortfall as binding mall work;
  bots and producers then finish the job concurrently. Retrying an already
  submitted direct-belt mine in this
  additive mode services its power anchor without synchronously waiting for
  every drill before submitting the refinery; full district validation still
  gates starter retirement. Destructive cutovers
  and synchronous service infrastructure remain fully funded boundaries. The
  initial construction window is five minutes. Diagnose and remedy its local
  ghost backlog throughout that window; only a flat unresolved job may fail at
  the end (upstream production, delivery, bot or roboport capacity, coverage,
  or power). Synchronously awaited poles and roboports diagnose a flat ghost
  tail after ten seconds; if its construction item has no live supply chain,
  that item returns to the normal mall scheduler immediately instead of
  holding the controller for the remaining window.
- A partially built or unconfigured furnace cluster is pending construction,
  not recoverable capacity. Recovery may adopt only an exact planner-shaped
  six-furnace module; until a direct refinery has produced plates, repair its
  power/transport path instead of expanding its mine or selecting another site.
- New paired-mine siting has one global candidate budget across every possible
  future-reserve size. Each candidate row is surveyed once, then its supported
  reserve is measured; reserve fallback must not restart the same live RCON
  scan. The complete operation emits one timed `new_mine_site` survey event.
  A read-only new-mine result is reused across supply-only retries and copied
  before plan metadata is attached. Any submitted mine capacity invalidates the
  cached result because occupancy has then changed.
- A position inside a pole's supply area is not evidence that a power bridge
  was built. Capacity planning distinguishes existing coverage from a submitted
  network bridge and keeps measuring the actual connected grid.
- Every new electric pole, substation, and roboport is an ordinary
  construction ghost. Its item bill is reserved through the material ledger
  and supplied by the mall; the executor may not create service infrastructure
  directly. For these entity types, direct placement remains only as an
  upstream synchronous intent that the submit boundary converts, or in
  non-production sandbox fixtures.
- Construction coverage advances one reachable roboport at a time. The current
  network first builds a funded pole branch beside the planned port, then the
  port ghost lands powered in the same controller pass. A reachable in-flight
  port is credited by concurrent coverage requests even though it does not yet
  provide service; they wait for it rather than placing a near-duplicate.
  Observed power makes the port usable, and only then can its new construction
  radius build the following hop. Planning a distant destination never licenses
  a complete live pole line ahead of the bots. Direct plate starters and compact
  mall cells use the same power-first submission order so a coverage wait cannot
  strand their local machine grid. After starter construction, every local
  pole must also pass exact-anchor generation verification; pre-build supply
  coverage alone cannot certify the new starter's network.
- Emergency pole chains are planned at Factorio's actual half-tile medium-pole
  centres, and every adjacent edge is checked against the shorter endpoint's
  wire reach before submission. A successful placement report is still not
  connectivity evidence: the repaired target must observe generation, and a
  disconnected roboport remains eligible for repair on every later survey.
  Before committing a medium-pole chain, compare fully stocked big-pole
  routes with medium- or big-pole consumer hookups along a clear corridor.
  Substations are local distribution for building clusters, never new bridge
  trunks or bridge terminals, including remote chemical/coal roboport waves.
  Existing substations remain valid connection anchors. Seeded substations
  may fund local layouts; manufacturing replacements requires observed
  advanced-circuit production through the chemical capability gate. A packed
  consumer with no legal pole hookup fails safely instead of upgrading to a
  substation. Prefer fewer placements, or a stocked route when medium poles
  are unavailable. Long-reach routes check
  complete 2x2 footprints and the shorter endpoint's wire reach, and remain
  subject to the same material reservation and observed-connectivity gates.
- The base has one primary electric grid: every new pole, substation, roboport,
  mine, and production district connects to the highest-generation network.
  Capacity telemetry measures that same network, never a nearer island.
- Multiple consumers of one resource require an explicit splitter/manifold and
  throughput budget. Independent belts may not overwrite or reverse the same
  collector head.
- Oil refining, plastic, and sulfur form a source-local district. Its bootstrap
  capabilities advance in one measured order: pipe, steel, chemical plant,
  oil refinery, offshore pump, pumpjack, plastic, advanced circuit, sulfur,
  then sulfuric acid. Plastic is validated before the sulfur/water branch is
  attached; the controller must not ghost both product branches as one opaque
  startup bill. Site the
  chemical block near the selected crude-oil source, rotate the pumpjack toward
  that block, and start the pipe on the exact external tile beyond the complete
  3x3 pumpjack footprint before extending a long power, construction, or pipe
  corridor back toward the factory. Pumpjack siting reserves each complete
  3x3 body and its external output stub together. Later bodies and outlets
  avoid both reservations; if a preferred outlet is blocked, try the other
  rotations before rejecting that well. The opening and expansion selectors
  share this pure geometry policy in `planners/pumpjack_siting.py`; full plan
  collision validation remains mandatory. Opening oil packet geometry and service
  plans persist in an episode-scoped immutable transaction before submission.
  Retries and runner restarts reconcile that exact transaction rather than
  selecting a new site; live validation still gates each submission.
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
  footprint before siting roboports. Each required roboport hop and its power
  bridge are funded ghosts built inside the preceding port's construction
  radius; the controller observes that hop before advancing the chain. Charging
  may delay the next hop naturally. Every local oil substation is likewise a
  funded ghost and is connected once bots have built it rather than appearing
  directly when the plan is submitted.
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

## Work-state taxonomy

All nonterminal waits and terminal blockers use the shared states `planned`,
`constructing`, `coverage_wait`, `power_wait`, `supply_wait`, `producing`,
`retiring`, `retired`, and `failed`. Each signal also carries a stable code,
details, and either `bug` or `intended_difficulty`; controller decisions inspect
those fields instead of matching human-readable log text.

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
Until advanced-circuit production is proven, the controller performs required
grid-connection repairs but postpones all generation construction. It does not
size a solar district or queue solar panels or accumulators, preventing a small
early power deficit from outranking the iron foundation and chemical ladder.
After advanced circuits are live, power expansion uses the full 1:1
panel/accumulator template. If a measured deficit selects a unit whose materials
are short, the controller queues that unit's exact materials through the normal
mall scheduler instead of merely rechecking the same unfunded atomic bill.

## Blueprint-only deterministic construction

Every new entity is a material-funded ghost constructed by bots. The submission
boundary converts legacy `place_entity` plans for every prototype, not just
power infrastructure. Observed matching entities use `configure_entity` and
incur no new material bill. Settings requiring a real chest inventory are
applied after bot construction. The Lua executor independently rejects missing
player-force direct-placement targets before executing any actions.

No bootstrap inventory grants and no destroy/recreate requester shortcut are
allowed. Trash settings unsupported on a bot-built requester remain unsupported;
removing a real building to force those settings is forbidden. Sandbox/training
fixture creation remains isolated from the deterministic player-force runtime.
