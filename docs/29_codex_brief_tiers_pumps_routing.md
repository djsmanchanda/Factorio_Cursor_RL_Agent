# Path: docs/29_codex_brief_tiers_pumps_routing.md
# Purpose: Self-contained brief for Codex covering three live-diagnosed follow-ups after the M7 MST-spine fix (commit fab65e2) produced the first fully clean live build.

Use gpt5.6-terra, high effort. Read `AGENTS.md` first (files <=500 LOC, "# Path:"/"# Purpose:"
header on every file, deterministic/symbolic planning only, commit messages explain why, no live
RCON access expected -- validate offline with `preflight()` + the test suite, the user runs live).

## Context: this is now a working factory, don't regress it

As of commit `fab65e2`, a live build (`tools/build_processing_units.py --construction-mode
radial --existing-topology reset`) produces a fully clean result: 285 infrastructure entities in
exactly 1 electric network + 1 roboport network (MST-routed spine, not hub-and-spoke), all 4325
production ghosts built via center-out radial construction, 0 remaining ghosts, and the full
`verify_factory_invariants`-equivalent live report returns `"ok": true, "violations": []` --
processing units are being produced end-to-end. This is the first time this project has reached
that state. Everything in this brief is refinement on top of a working baseline, not more
firefighting -- be conservative, don't destabilize what's passing.

## Task A: default to Nauvis-native belt/inserter tiers; add an upgrade path to premium tiers

The user found and fixed this live, then confirmed it works: `planners/electronics_block.py`
currently hardcodes `BELT = "express-transport-belt"` and `INSERTER = "stack-inserter"` (module
constants used everywhere). **In Factorio Space Age, `express-transport-belt`,
`stack-inserter`/`bulk-inserter`, and `turbo-transport-belt` are not craftable on Nauvis** --
they (or their ingredients) must be imported from another planet via rockets/space platforms,
which is a real added logistics cost that doesn't make sense to pay for a factory's initial,
low-volume construction. The user's direction: **start construction with the cheapest
Nauvis-native tier that meets the recipe's throughput need** (`fast-inserter` /
`fast-transport-belt`, not `transport-belt` -- confirm against `planners/recipe_data.py`'s
`FEEDER_RATES = {"fast-inserter": 4.0, "bulk-inserter": 8.0, "stack-inserter": 12.0}` and
`BELT_TIERS = {"transport-belt": 15, "fast-transport-belt": 30, "express-transport-belt": 45,
"turbo-transport-belt": 60}` for which tier actually meets each stage's required throughput --
don't just swap to the single cheapest tier if it can't keep up, `line_layouts.py:393-394`
already has tier-selection-by-throughput logic to reuse/generalize), **and add a way to upgrade
to the premium tier later once the factory reaches a scale that justifies importing it.**

The upgrade mechanism already exists and should be reused, not reinvented:
`factorio_mod/upgrades.lua` (`/execute_upgrade_plan` command) +
`orchestrator/game_bridge.py::execute_upgrade_plan`. What's missing is (a) the initial build
using the cheap tier by default, and (b) a deterministic "upgrade plan" generator that emits
`upgrade` actions (check `schemas/build_plan.schema.json` and existing upgrade-plan shape used by
`upgrades.lua`) to swap specific belts/inserters to the premium tier. Don't invent a "scale
threshold" heuristic out of thin air -- if there's no existing signal for "the factory has scaled
enough to justify imports" in this codebase (check `core/` and `orchestrator/expansion_daemon.py`
for anything resembling throughput/scale tracking), surface that as an explicit open question in
your report rather than guessing a magic number; a reasonable default might be "upgrade is a
separate, explicitly-invoked tool/CLI flag the user runs later" rather than something the
initial build decides on its own.

## Task D (NEW, live-observed this session): pipe connector misalignment is systemic, not just the offshore pump -- and the router leaves obvious undergrounding opportunities unused

Screenshots this session showed fluid-consuming machines (chemical plants at minimum, possibly
others) with the classic Factorio "missing fluid input" alert icon (a blue droplet with a
warning triangle) even where a routed pipe runs visibly close by -- i.e. the SAME class of bug
diagnosed in detail for the offshore pump below (a machine's real connector tile not lining up
with where the router's pipe actually ends), but showing up at MORE points across the factory,
not just that one site. Do not treat the offshore-pump fix as sufficient once done -- after
fixing it, systematically verify (live, via the same `get_pipe_connections`/test-pipe-and-check
`get_connections` method used to diagnose the offshore pump) that every fluid machine type this
project places (`oil-refinery`, `chemical-plant`, `assembling-machine-*` fluid ports, `pumpjack`,
`offshore-pump`) has its declared attachment point in `planners/electronics_contracts.py` /
`planners/fluid_layouts.py::header_attachment` matching its REAL connector tile and direction,
not just an assumed offset. A single live probe script that creates one of each machine type,
reads its real connectors for every direction, and asserts the codebase's assumed offsets match
would catch this whole class of bug at once rather than one machine at a time.

Separately: the user also observed the fluid router (`planners/fluid_routing.py`'s new A*
implementation from Task C / commit `c7d0d20`) producing a long overground pipe run with a
sharp dogleg where a short `pipe-to-ground` (underground pipe) crossing would clearly be
shorter and cleaner -- comparing two live screenshots, the more direct one was unambiguously
better. The router's docstring already distinguishes `hard_tiles` (never crossable) from
`tunnelable_tiles` (occupied routes that MAY support an underground crossing "in a later
emission phase") -- check whether that later phase actually exists yet, or whether undergrounding
is a planned-but-not-implemented capability of the current router. If it's not implemented, this
is real follow-up work: the router should prefer a short underground crossing over a long
overground detour when the tile-length savings clearly justify it (Factorio's `pipe-to-ground`
has a fixed max reach, currently `underground = belt_type.replace(...)`-style tier logic already
exists for belts in `planners/item_routing.py` -- check for an equivalent pipe underground-reach
constant to reuse, or add one following the same pattern).

## Task B UPDATE (superseding the ground truth below where it conflicts): offshore pump root cause fully diagnosed live, fix is blocked by a real collision, not missing data

Everything in the original Task B below has been resolved/superseded except the collision at the
end -- read this update first, it's the current state.

Confirmed live via two independent RCON tests (create a test offshore-pump with an explicit
`direction`, then place a test pipe on each of its four neighbor tiles and check
`pipe.fluidbox.get_connections(1)` for whether it lists the pump as a connected owner):
- `direction=north` (Factorio's default, and what `planners/resource_layouts.py` emits today
  since the fixture's `offshore_pump_sites[0]` has no `direction` key) links the pipe connector
  on the pump's **south** tile.
- `direction=south` links the connector on the pump's **north** tile.
This is the *opposite* of what `planners/water_lakes.py::water_lake_bounds`'s branch labels
assume (its `"north"` branch places the lake south of the pump, `"south"` places it north --
i.e. it treats `direction` as "the side facing land", not "the side facing water", which is
backwards from the live-tested entity behavior above).

The real site (`tests/fixtures/electronics_world_spec.json`'s one offshore-pump at
`(20.5, 83.5)`) has ACTUAL water terrain to its south (confirmed by reading live tiles around it).
With the current default (no `direction` key -> `"north"`), the pump's live connector lands on
its south tile -- which is water, so no real pipe can ever attach there. That's the entity
orientation bug the user saw and asked to "rotate 180 degrees": it needs `"direction": "south"`
so its connector lands on the north (land) side instead.

**Setting `direction: "south"` alone does not work yet.** I traced this down to the exact tile:
with the corrected direction, the fluid router's `water_from` anchor point
(`planners/electronics_block.py`: `water_from = (water_output[0] - 1, water_output[1])`) lands
inside the pump's own hard-tile-plus-clearance footprint (its power scaffold
`power_anchor = (anchor[0] - 3, anchor[1] - 3)` for offshore-pump, from
`planners/resource_layouts.py::_fluid_resource_plan`, sits very close by) -- the router can't
even take a first step out, let alone reach the sulfur/sulfuric-acid consumers 50+ tiles away.
I confirmed this precisely: `water_from` and literally all 8 of its neighbors are either hard
tiles or inside a clearance halo, for any `output`/`water_pipe_tiles` value I tried near the
pump's real connector tile (verified the connector's real tile is `(20, 82)` for this site, one
tile north of the pump's own `(20, 83)` footprint tile).

**This needs a real fix, not another patch attempt**: redesign
`_fluid_resource_plan`'s offshore-pump power-scaffold placement (and/or the stub pipe geometry)
so there's a genuinely clear tile adjacent to the connector for the fluid router's anchor to
start from -- the scaffold and the connector are currently placed too close together for ANY
routing anchor choice to escape the clearance halo. Once that's fixed, also flip
`water_lake_bounds`'s branch labels (swap north<->south and east<->west, matching the live-tested
semantics above) and set `"direction": "south"` on the fixture's offshore-pump site -- I've left
both of those UNDONE in the current committed state specifically because they only matter once
the scaffold-collision is fixed; doing them first with the scaffold conflict unresolved breaks
the offline build (`ValueError: No clear fluid route to (89, 105) inside bounded search area`,
or later `ValueError: Plan collision: water_source offshore-pump at (20.5, 83.5) overlaps
water_source pipe at (20.5, 82.5)` depending on which stub tiles you try). Do not re-attempt the
direction/lake-semantics swap without first fixing the scaffold/connector clearance conflict, or
you'll rediscover the exact same dead end.

## Task B (original, mostly superseded by the update above -- kept for the pumpjack context, which IS fully resolved)

The user manually fixed a misplaced offshore pump and pumpjack live, twice now, across two
sessions (`docs/28_codex_brief_pump_landfill_wiring.md`'s "Defect 2" was left unresolved by the
previous Codex pass specifically because "the repository has no verified connector offsets for
offshore-pump or pumpjack" -- that blocker is now removed, ground truth below).

**Live-probed ground truth** (RCON query against the user's manually-corrected, now-working
entities on `planner-sandbox`):
```
pumpjack@(18.5,-43.5) direction=12 (west)
  fluid connector: position=(17.5,-42.5) [offset (-1,+1) from entity center], flow_direction=output
offshore-pump@(18.5,83.5) direction=8 (south)
  fluid connector: position=(18.5,83.5) [offset (0,0) from entity center], flow_direction=output
```
(`defines.direction`: north=0, east=4, south=8, west=12, using the classic 4-way values.) Query
used: `entity.fluidbox.get_pipe_connections(index)` per fluidbox index, reading `.position`,
`.direction`, `.flow_direction`. If you need more samples (other directions, to derive the general
offset formula per direction rather than trust one sample each), the user can run the equivalent
query again for you -- ask for it rather than extrapolating a directional pattern from a single
sample per entity if you're not confident, since getting this wrong is exactly what caused the
original bug.

Cross-reference this against `planners/resource_layouts.py::generate_pumpjack_source` and
`generate_offshore_pump_source` -- find where the emitted `direction` and the adjoining pipe
segment's expected connection tile are computed, and fix whichever one doesn't match the real
connector position above. The pipe segment that's supposed to attach here comes from
`world.crude_pipe_tiles` / `world.water_pipe_tiles` in the WorldSpec fixture -- the fix needs the
entity's actual connector tile to line up with wherever that pipe run currently ends, which may
mean moving the entity, changing its direction, or (least preferred, only if genuinely necessary)
adjusting the pipe run's endpoint to match a correct entity placement.

## Task C: fluid pipe routing (sulfuric acid, water) makes excessive detours; tighten the electric spine further too

Live observation from the user, comparing the sulfuric-acid and water pipelines against the
electric spine (which THIS session's MST rework already improved and the user confirmed is
"better" but "still fairly excessive"): the sulfuric-acid line in particular "goes quite left for
a long distance, then down, then right again for a very long distance" -- a large unnecessary
rectangular detour instead of a direct-ish path. Likely culprit:
`planners/fluid_routing.py::fluid_chain_link_segments` / `planners/fluid_layouts.py`'s
`generate_fluid_chain_link` -- these currently route with some fixed/rectilinear strategy (check
whether it's a simple two-leg L-route per chain link, similar to what
`plan_power_network` used to do before this session's MST fix, or something else). Direction:
route these fluid chains with something closer to a proper shortest-path method (the user
explicitly said "try being closer to Minimum Spanning Tree/Dijkstra's method") given the known
obstacle set (`occupied_tile_indices` of everything already placed -- the same obstacle
vocabulary `planners/electronics_block.py` already threads through the composition passes,
including the water lake tiles from `planners/water_lakes.py` added in the previous commit).

Also apply the same tightening pass to `planners/infrastructure.py::plan_power_network`'s MST
edges if you find the current elbow-choice (`choose_clear_l_route` in
`planners/infrastructure_geometry.py`) is picking unnecessarily long detours where a shorter
clear path exists -- the user says it's "better" now but still not tight enough.

**Additional constraint from the user: "having places demarked for future expansion."** Whatever
routing strategy you land on should not simply find the tightest possible path through every gap
that happens to be free right now -- leave some deliberate margin/reserved corridors so a future
larger build (more production lines, more of the same stage) has room to grow without requiring
this pipe/wire network to be torn up and rerouted. This is a real design tension (tightest path vs.
reserved space) -- don't over-engineer a full "expansion zone" system if nothing like it exists
yet; a reasonable minimal version might be routing with a small fixed clearance margin around
existing structures rather than hugging them exactly, or keeping corridors on a consistent grid
spacing that leaves room to insert more lines later. If you're not sure how far to take this,
implement the "tighter routing" half concretely (that's clearly specified and testable) and
describe options for the "reserved expansion space" half in your report rather than guessing at
a design the user hasn't fully specified.

## What NOT to do

- Don't touch `factorio_mod/layout_executor.lua`'s wiring pass, the fail-fast network check in
  `tools/electronics_radial_execution.py`, or the MST/relay-pole-type logic in
  `plan_power_network` beyond the elbow-tightening in Task C -- those are the hard-won, currently
  passing core of this project.
- Don't guess pump/pumpjack connector geometry -- ground truth is provided above; ask for more
  live samples if you need them rather than extrapolating.
- Keep files <=500 LOC; split if a change pushes something over.
- Full test suite (`python -m pytest tests/ -q`) must stay green; re-run
  `preflight()` against the full composed bundle
  (`build_electronics_block(include_processing=True, world=load_electronics_world_spec(Path("tests/fixtures/electronics_world_spec.json")))`)
  before finishing.

## Report back

Which files changed, final LOC of each touched file, new/changed test count, full test suite
pass/fail status, and explicitly flag any of the three tasks you could not fully resolve (e.g. if
Task A's "scale threshold" or Task C's "reserved expansion space" needs a product decision from
the user rather than an engineering guess).
