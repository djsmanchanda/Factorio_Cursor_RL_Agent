# Path: docs/28_codex_brief_pump_landfill_wiring.md
# Purpose: Self-contained brief for Codex to fix three live-diagnosed defects found after the M7 water-seeding + belt-turn fix (commit 81b19e1).

Use gpt5.6-terra, high effort. Read `AGENTS.md` first (files <=500 LOC, "# Path:"/"# Purpose:"
header on every file, deterministic/symbolic planning only, commit messages explain why).

## Context: what's already working, don't break it

This project builds a real Factorio factory via deterministic planners + live RCON execution.
As of commit `81b19e1`, a live end-to-end build (`tools/build_processing_units.py
--construction-mode radial --existing-topology reset`) gets through infrastructure placement
(553 entities, exactly 1 electric network + 1 roboport network -- this is the hard-won core
result of this project, do not regress it) and 2 of 6 production rings before stalling. Do not
touch `factorio_mod/layout_executor.lua`'s `ensure_pole_wiring` function or the fail-fast network
check in `tools/electronics_radial_execution.py::assert_infrastructure_networks` -- those are
correct and load-bearing.

Three NEW defects were found live this session, all downstream of the water-lake seeding added
in `81b19e1` (`factorio_mod/water_seeding.lua`, `tools/electronics_execution.py::water_seeding_payload`/`_water_lake_bounds`).

## Defect 1: entities get placed ON TOP of the newly-seeded water lake (this is what stalls ring 2)

Live-diagnosed: ring 2 stalls with a `medium-electric-pole` at `(17.5, 86.5)` reporting
`can_place_entity{..., build_check_type=defines.build_check_type.ghost_revive}` = false. That
position falls inside the water lake `water_seeding_payload` seeds around the offshore pump at
`world.offshore_pump_sites[0]["position"] = (20.5, 83.5)` (a north-facing lake, per
`_water_lake_bounds`, spans roughly x:[14,27] y:[84,96] for that site). Factorio will not let you
build a land entity on a water tile -- ever -- so this pole ghost can NEVER be revived as-is.

The user's explicit direction: **do not silently work around this by adding landfill** unless
that's genuinely the simplest correct fix for a SPECIFIC entity that must sit on what is now
water (quote: "if it wants to do that, it needs to put landfill on that spot first" -- i.e., if
landfill is used, it must be a deliberate, visible planning decision, not implicit). For most
cases (a local supply pole near the pump) the right fix is almost certainly **do not place the
entity there at all** -- route it around the lake, the same way `_resolve_spine_pole_overlaps` in
`planners/sandbox_infrastructure.py` already relocates colliding spine poles within slack. The
lake's tiles need to become a first-class "obstacle" set that:
- `planners/electronics_block.py`'s composition passes (preview infra, item/fluid routing,
  `_cover_emitted_geometry`'s recompose) already treat `occupied_tile_indices(...)` as obstacles
  for various purposes -- the lake needs to enter that same obstacle vocabulary. Figure out the
  cleanest place: probably wherever `generate_offshore_pump_source` (in
  `planners/resource_layouts.py`) already knows the pump's local power/pipe scaffold geometry, so
  the lake's footprint (reuse `tools/electronics_execution.py::_water_lake_bounds`, or move that
  function somewhere both modules can import from without a circular import -- your call) is
  known and dodged at PLAN TIME, not discovered live.
- Verify no OTHER stage's fixed geometry (item routes, other local power poles, roboports) also
  overlaps a lake -- there are currently 2 offshore-pump sites in `world.offshore_pump_sites`
  (check `tests/fixtures/electronics_world_spec.json`), each seeds its own lake.
- Add an offline check (in `planners/preflight.py` or wherever fits the existing pattern) that
  fails loudly if any planned entity footprint overlaps a lake rectangle, so this class of bug is
  caught by `preflight()` before a live run ever starts. (Today preflight has no concept of water
  lakes at all -- this is new ground for it.)

## Defect 2: offshore pump and pumpjack are placed/oriented wrong and never connected to their pipe

Live screenshots this session showed: the offshore pump is not sitting correctly relative to the
lake edge (the user provided a reference screenshot of a CORRECTLY placed pump: it sits with its
single fluid connector touching a water tile directly, pipes running out from the land side), and
neither the offshore pump nor the pumpjack ends up connected to the rest of the pipe network --
they sit isolated. Same defect for both, likely the same root cause: whatever generates their
placement + first pipe segment (`planners/resource_layouts.py::generate_offshore_pump_source` and
`generate_pumpjack_source`) is emitting a position/direction that either (a) doesn't line up the
pump/pumpjack's actual output connector with the pipe stub the rest of the network expects, or (b)
the connecting pipe segment's endpoint doesn't match the entity's real connector tile. Check the
exact `direction` field being emitted and cross-reference against Factorio's actual connector
position for `offshore-pump` and `pumpjack` prototypes (pumpjack's output connector is a specific
tile offset from its center depending on direction; verify live via RCON on a test instance if
you're unsure rather than guessing from memory -- `entity.fluidbox` / the visible pipe connection
point once built is the ground truth). Fix so the emitted `place_entity`/`place_ghost` action's
position+direction puts the connector tile exactly where the adjoining pipe segment (from
`world.crude_pipe_tiles` / `world.water_pipe_tiles`) expects it.

## Defect 3: electric spine wiring is "absolutely horrendous" -- rework the routing strategy, not just pole-type selection

This session already changed `planners/infrastructure.py::plan_power_network` to prefer
substations over big-electric-poles for short legs (commit `fdf654a`), but the user has now seen
it live and the wiring topology itself is bad: many long, criss-crossing wires radiating from a
single hub to far-flung sites, instead of a clean, locally-connected chain. Direct user quote:
"better electric connection logic, lesser randomly placed big electric pole, only use them when
trying to connect two spots more than 1-200 spaces further away -- there also try to connect
through closest pole near the spot" (i.e. loosen the exact long-leg threshold to a reasonable
value in the 100-200 tile range rather than treating 100 as a hard number, AND -- the more
important part -- **route each site through whichever existing pole is nearest to it, not always
via an L-shaped path from the central hub**).

Concretely: `plan_power_network` currently does, for every site independently,
`l_route(hub, anchor)` -- a fresh horizontal-then-vertical path from the ONE central hub straight
to that site's anchor, regardless of how many other sites/poles already sit near that anchor. This
is why the wiring looks like spokes from one center rather than a tree that branches locally. The
roboport network (`plan_roboport_network` in the same file) already solves exactly this shape of
problem correctly: it builds a **minimum spanning tree** over all site positions
(`planners.infrastructure_geometry.minimum_spanning_tree_edges`) and walks each MST edge
rectilinearly, so roboports chain locally instead of all radiating from one point. Rework
`plan_power_network` to do the same: build an MST over `[hub] + [site anchors]`, walk each MST
edge with the existing relay/spine-pole-type-by-leg-length logic (substation for short edges,
big-electric-pole only for edges over the threshold), instead of routing every site
independently from the hub. This should also naturally reduce total pole count and length now
that long overlapping spokes collapse into shared tree edges.

Re-verify after this change: `planners/preflight.py`'s `power_reach` check must still pass on the
full composed bundle (`build_electronics_block(include_processing=True, world=...)` against
`tests/fixtures/electronics_world_spec.json`), and the full test suite
(`python -m pytest tests/ -q`) must still be green (baseline as of `81b19e1`: 44 tests in the
directly-affected files passed, 1 skipped -- confirm the FULL suite separately, it's larger than
that). Also re-run `_resolve_spine_pole_overlaps` mentally against an MST-based spine: it already
handles arbitrary entity types per point (reads `POLE_SPECS[action["entity"]]["size"]`
dynamically) so it should keep working unchanged, but verify.

## What NOT to do

- Do not touch `factorio_mod/layout_executor.lua`'s wiring pass or the fail-fast network check --
  both are correct and hard-won this session.
- Do not add landfill as a blanket workaround for defect 1 -- only where a specific entity
  genuinely must occupy what is now water, and make that an explicit, visible planning decision
  (a `place_entity`/`place_ghost` action for a `landfill` tile placement, or the mod's own tile
  API), not a silent side effect.
- No live RCON access is expected for this task -- validate with `preflight()` and the test suite
  offline, the way every other planner change in this project has been validated. The user will
  run the live build themselves after you're done.
- Keep files <=500 LOC; split if a change pushes something over.

## Report back

Which files changed, final LOC of each touched file, new/changed test count, and full test suite
pass/fail status. If you hit a genuine ambiguity (not a style choice) you can't resolve from the
code -- e.g. the exact correct connector tile for a pumpjack in a given direction -- say so
explicitly rather than guessing silently.
