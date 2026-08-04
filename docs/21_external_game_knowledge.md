# Path: docs/21_external_game_knowledge.md
# Purpose: Data-only external game knowledge (wiki-derived). Never overrides system standards (docs/19, docs/20).

# External Game Knowledge (Factorio Wiki)

Source: wiki.factorio.com Tutorial:Quick_start_guide + Tutorials index. Ingested 2026-07-18.
Per docs/19: reference data only — validated before use, never authoritative over our standards.

## Early-game progression order (Quick Start Guide)
1. Resources: coal, copper ore, iron ore, stone near spawn; water for steam.
2. Burner phase: burner drill → stone furnace direct-insert; paired coal drills fuel each other.
3. Belts + burner inserters (self-fuel from coal belts) for transport.
4. Electricity: offshore pump → boilers → steam engines; ratio **1 pump : 20 boilers : 40 steam engines**; replace burner drills with electric.
5. Research Automation (assembling machine 1, long inserters), then Logistics (splitters, underground belts, fast inserters).
6. Automate science: gear assembler + red-science assembler → inserter into labs.

## Ratios / heuristics
- Miners: ~2:1 iron:copper early game.
- Belt lanes: keep items split across both lanes of a belt for throughput ("ore split more or less evenly on each side").
- Leave room to expand around every production area.

## Relevant tutorials for later phases
- Main bus (base organization standard for mid-game) — candidate input for CityPlanner block design.
- Applied power math, Nuclear power (PlanetPlanner-era power planning).
- Train signals (rail standard alignment check for docs/14).
- Circuit network cookbook (control logic, far future).

## Logistics tiers (user-provided, 2026-07-18)
- Belts (items/s): transport-belt 15, fast-transport-belt 30,
  express-transport-belt 45, turbo-transport-belt 60 (**Vulcanus-only
  production** — must be imported off-planet).
- Inserters: plain inserter, fast-inserter, bulk-inserter high hand capacity
  (upgradable +11); stack-inserter (**Gleba-only production**) stacks items
  4-high on belts, effectively quadrupling belt throughput.
- Tier is **chosen from the rate one inserter carries**, not fixed
  (superseded the fast-inserter baseline, 2026-08-01). `inserter_for_demand`
  in `planners/recipe_data.py` picks the cheapest tier covering the busiest
  flow at a machine -- ingredients in AND product out, since a line layout
  puts the same tier on both faces. An electric furnace moves 0.625 item/s,
  so a fast inserter there was ~5x oversized and cost a production chain it
  never used; a plain inserter carries it with margin. Busy lines still
  escalate (electronic-circuit draws 4.5 copper-cable/s -> bulk-inserter).
- stack-inserter is never auto-selected: Gleba-only production means demand
  alone must not conjure one onto Nauvis. A caller asks for it by name.
- Feeder COUNT is sized separately (`_feeders_needed`), so a cheaper tier
  widens the feed array rather than throttling the line.
- Planet-sourcing constraints are supply-chain facts for PlanetPlanner-era
  planning; on the test sandbox all tiers are available via scaffolding.

## Throughput physics (user-provided, validated live 2026-07-18)
- Inserter swings are rotation-bound: 180° to load, 180° to unload. One
  feeder cannot supply a hungry line; the first machines strip the belt and
  downstream machines starve (observed: 6-machine circuit line at 2/s of a
  9/s cap, machines 3-6 idle).
- Slower belts worsen unload time. Belt tier + inserter tier upgrades
  measured: fast-inserter + transport-belt 0.6/s → stack-inserter +
  express-belt 7.68/s on the same 6-machine circuit line (12.8x).
- Planner consequence: feed points scale with per-ingredient demand
  (feeders = ceil(demand / feeder_rate); LINE_RECIPES amounts × craft rate).

### Known gap: feeder rates are ceilings, and belt tier is unmodelled
`FEEDER_RATES` holds best-case chest->belt throughput -- fully researched hand
capacity, unloading onto a belt fast enough not to hold the swing. Only
fast-inserter has a live datapoint behind it. The plain inserter's figure is
arithmetic (~0.36x a fast one, applied to the researched fast value) and the two
high-capacity tiers are prototype claims, so all three are listed in
`UNMEASURED_FEEDER_RATES` and discounted by `UNMEASURED_RATE_DERATING` before

The complete user-supplied Factorio 2.0.26 experimental table is preserved in
`docs/reference/inserter_throughput_factorio_2_0_26.txt`. It covers
chest-to-chest, chest-to-belt, chest-to-splitter, and perpendicular
belt-to-chest cases across capacity bonuses, qualities, belt tiers, and belt
stacking. It is a diagnostic reference, not yet an active lookup table: the
real-base snapshot does not currently export the inserter-capacity research,
quality, destination belt tier/occupancy, or pickup geometry needed to choose a
safe row. Collapsing those dimensions into one speed would recreate the
underfeeding bug this section warns about.
anything is sized from them. Callers use `feeder_rate()`, never the table.

The derating is a safety margin, NOT a measurement. Erring low buys a
cheaper-than-needed tier or one extra feed point; erring high builds a line that
runs throttled while every machine still reports as working -- which reads
downstream as saturation and gets five more equally throttled machines built
beside it.

The bigger unmodelled factor is the destination belt: the measured 12.8x jump
above came from upgrading belt AND inserter together, so a fast inserter onto a
plain transport-belt carries well under this table's 4.0/s.

To close it, measure per tier on a live base rather than guessing better:
1. Build one machine fed from an infinity chest through one inserter of the
   tier, unloading onto a saturated belt of the tier being paired with it.
2. Read `get_item_production_statistics` over a fixed tick window; divide.
3. Repeat per (inserter tier, belt tier) pair.
4. Record the number here, and remove that tier from `UNMEASURED_FEEDER_RATES`.

## Advanced feeding patterns (user-provided, 2026-07-18 — next to implement)
- **Dedicated belt per ingredient**: fill the entire input belt (both lanes)
  with the high-demand ingredient; run a second parallel belt for the other
  ingredient, reached by long-handed inserters (2-tile reach, slower swing).
- **T-junction sideloading**: a belt can empty onto one lane of another belt.
  Feeder belts running perpendicular continuously top up a lane; gaps on one
  lane are compensated by the other. Belt-fed lanes beat chest+inserter
  feeding because the belt buffer absorbs inserter swing gaps.

## Production growth axes (user standard)
Throughput grows over time along these axes, in roughly this order:
1. Faster belts (yellow → red → blue → turbo)
2. Stacked items (stack inserters, 4-high belt stacking)
3. More machines per line (X) and parallel lines (Y, LINE_PITCH_Y)
4. Better machine tiers (assembling machine 1 → 2 → 3)
5. Quality tiers (normal → uncommon → rare → epic → legendary)

## Feed-style tradeoffs (measured live, 2026-07-18)
Same 6-machine electronic-circuit line (27/s cable + 9/s plate demand),
stack inserters throughout, steady-state collector rates:
- chest-fed, express belts: **9.6/s** — chest feeders spray the high-demand
  ingredient onto BOTH lanes, so no lane cap; but limited feeder buffer.
- sideload-fed, express belts: **8.27/s** — each ingredient gets ONE dedicated
  lane; cable capped at an express lane's 22.5/s < 27/s demand (lane-limited).
- sideload-fed, turbo belts: **9.07/s** — turbo lane (30/s) clears the 27/s
  demand; residual ~0.5/s vs chest is junction/hop latency.
Planner rule of thumb: sideload feeding needs lane rate >= per-ingredient
demand; otherwise use chest feeding, a dedicated both-lane belt for the hot
ingredient, or a higher belt tier. These are exactly the tradeoffs the RL
decision layer (docs/22) will weigh as catalog actions.

**Capacity headroom (user standard, 2026-07-18):** provision feed and drain
capacity with a 20-25% buffer over raw demand (FEED_HEADROOM = 1.25 in the
planner) — feeder counts, collector counts, and the belt-tier suggestion in
the sideload lane check all use it. Hard lane-check failure only below raw
demand; the suggested tier always meets demand x headroom. Two purposes:
supply never runs at the ragged edge, AND the slack pre-pays expansion — a
line can grow ~25% (more machines in X) before its feed/drain infrastructure
needs rework, which the RL decision layer should count when costing
"extend_line_x" against other catalog actions.

## Roboport ranges (user-provided, verified live 2026-07-22)
Two distinct radii, and confusing them wastes materials or strands builds:
- **construction area**: `construction_radius = 55` -> 110x110 tiles. This is
  where construction bots may place ghosts. Overlapping construction areas do
  NOT merge networks.
- **supply / logistic area**: `logistic_radius = 25` -> 50x50 tiles. This is
  what links roboports into ONE network; in practice roboports must be within
  **~46 tiles** of each other to connect.
Consequence for scaffolding anchors: anchors further apart than ~46 tiles are
separate logistic networks, so each needs its own materials and bots (verified
live: anchors at (10,92) and (40,158), 68 tiles apart, report
`same_network=false`). Either space anchors <= 46 apart to form one network and
stock it once, or keep them separate and stock every anchor - the planner must
choose deliberately rather than assume.

## Quality system (wiki + user, 2026-07-18)
Encoded in `core/quality_modules.py`.

- Tiers and strength (effects are per-strength, additive): normal 0,
  uncommon 1, rare 2, epic 3, legendary 5.
- Per-entity quality effects, per strength point: assembling machines +30%
  crafting speed; inserters +30% rotation speed; electric poles +1 tile
  supply reach and +2 wire reach; beacons -16.67% power; modules +30%
  positive effects. Transport belts and walls gain health only — no
  throughput effect, so they are planner-irrelevant and excluded from the
  effects table.
- Crafting: quality modules give a chance to upgrade output one tier (then a
  repeated 10% chance per further tier). Ingredient quality match is exact,
  not minimum — a recipe set to a quality tier requires ALL item ingredients
  at exactly that tier; fluids have no quality and are exempt. Recyclers
  return 25% of inputs.
- **Safety rule (user-mandated):** mixed-quality items on a shared line jam
  production irrecoverably. Quality production requires dedicated, sorted
  lines per tier. `validate_uniform_quality()` rejects any line/feed spec
  whose ingredient quality tiers are not uniform and equal to the recipe's
  tier.
- Planet notes: none needed — quality tiers and effects are planet-agnostic.

## Modules (wiki, 2026-07-18)
Encoded in `core/quality_modules.py`.

- speed-module 1/2/3: speed +20/+30/+50%, energy +50/+60/+70%.
- productivity-module 1/2/3: productivity +4/+6/+10%, energy +40/+60/+80%,
  speed -5/-10/-15%.
- efficiency-module 1/2/3: energy -30/-40/-50%.
- quality-module 1/2/3: quality chance +1/+2/+2.5%, speed -5% each.
- Stacking rule: machine properties (speed/energy/pollution) cannot drop
  below 20% of their original value regardless of how many modules stack —
  `apply_modules()` enforces this floor.
- Productivity modules only apply to intermediate-product recipes. In our
  LINE_RECIPES world (`planners/local_layout_planner.py`) the intermediates
  are: iron-gear-wheel, copper-cable, iron-stick, electronic-circuit,
  iron-plate, copper-plate — plus automation-science-pack, since science
  packs accept productivity in Factorio. Productivity modules are never
  allowed in beacons.
- Module slots (verified live against 2.0.77 prototypes via RCON,
  2026-07-18): assembling-machine-1: 0, assembling-machine-2: 2,
  assembling-machine-3: 4, electric-furnace: 2, beacon: 2.
- Planet notes: none needed — module effects and slot counts are
  planet-agnostic.

## Implications adopted (validated against our invariants)
- Two-lane belt feeding supports 2-ingredient recipes on a single input belt
  (inserters only pick up items their destination accepts).
- Science automation chain (gears → red science → labs) is the first
  multi-line dependency target for LocalLayoutPlanner chaining.
