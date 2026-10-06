<!-- Path: docs/rl/Laya_Strategy_Guidelines.md | Purpose: The user's factory strategy as reviewed soft guidelines that Laya reads; editing this doc changes strategy, not code. -->

# Laya strategy guidelines

These are the user's strategy rules for the `mod_playground` mission. They are
**soft guidelines**: Laya follows them by default. Rules marked with
preferences in `docs/rl/laya_mission_guidelines.json` also weight the current
guided policy's choices (see "How rules steer decisions" below). A deviation is allowed only
when measured outcomes in disposable runs show it is better, and every
deviation is journaled. Hard safety and legality rules stay in
`docs/system_invariants.md`.

`docs/rl/laya_guidelines.json` quotes these passages exactly; the loader
rejects a rule whose quote no longer appears here. To change strategy, edit
the sentence here and its quote there.

## Mission

Start from the mod_playground starter kit and reach advanced circuits, then self-expand and meet all item needs.

After advanced circuits, research mining productivity 4 by building assembly lines for automation, logistic, chemical and production science packs and their precursors, feeding the packs to labs, then moving to the next science item.

Use only the starter kit and items the factory manufactures; never borrow player inventory or gifted items.

## Deciding

Serve the current phase's goals first: actions on items its goals need come before stock and other needs, which wait while a goal action is possible.

Serve the bottleneck: prefer actions on items in the current bottleneck chain, deepest unmet input first.

Make missing construction items before their stock runs out; a build blocked by a missing item is a reason to make that item.

Never spend construction items the current phase goals' builds still need; a side build that would leave no goal build affordable waits.

Build a machine after its inputs are made or stocked; a machine whose input nobody makes locks its other inputs in its requester, so build the missing input first.

Conserve scarce starter items: prefer builds that leave enough poles, chests and inserters for the next builds; a build that spends most of what is left of an item needs a strong reason.

Wait only when nothing useful can be built, or while construction and deliveries are still arriving.

Extend robot coverage toward resources and sites a current need requires; coverage that serves no need spends poles and roboports for nothing.

These come from the deterministic runner's history: of 184 runs since 2026-09-05, 74 ended with no progress on a construction item (steel, drills, splitters, furnaces, fast inserters, poles) whose inputs had no reliable producer, and its furthest run toward plastic stopped when gear and iron-stick stock drained with no permanent producer.

## Planning

Plan land before building: protect ore patches for mining, keep a smelting strip beside each plate's patch for its mine and furnace line, and keep the mall together in one reserved zone.

Ignore small ore patches unless no larger patch of that resource lies within 250 tiles of the base; small patches get no reservation. A patch is small below the deterministic new-site minimum of 200,000 ore or about 256 tiles of ore.

A smelting strip is at least one six-furnace module wide (three furnaces, 9 tiles, plus 2 for the belt and 1 spare on each side; the basic module is 15 tiles) and 34 tiles where space allows (two parallel lines, or one 18-furnace line of Start-Middle-Middle-End), with 5-tile belt corridors kept free on both sides for future belts.

The reservations are enforced when options are generated (`training/laya_site_plan.py`); the mall site is Laya's first decision of an episode. Belt corridors accept only belts, splitters and poles. Roboports may stand inside a smelting strip, because its lines' provider taps need logistic coverage, and on an ore zone, because coverage must cross a patch to reach the ones beyond it. Strips keep clear of the starter area (24x24 around the starter roboport). The starter rule replaces requester-fed single furnaces and single drills into chests for iron, copper and stone (`training/laya_starters.py`).


Land is a grid of 64-tile parcels; every build stays inside one parcel, and the corridor lattice between parcels carries only belts, pipes and poles.

A purpose grows into the parcel next to its own before a distant one, so production stays in contiguous blocks near its inputs. See `docs/rl/Laya_Land_Grid.md`.

Reserve a block of land for each item beside its inputs, sized for its future lines; grow each new line next to the existing ones from the block's near edge, so the rest of the block stays free.

The starter area around the starter roboport, (-12,-10) to (10,12) on mod_playground, is kept clear like a corridor. The mall is one large 40x40 block beside it, filled with quad cells from the edge its roboport covers.
## Opening

First priority: iron and copper starter mines and furnaces; stone and the mall follow.

The first mine of each resource is a direct starter, as in the deterministic runner: iron is two drills feeding two furnaces, copper one drill feeding one furnace, stone two drills feeding one furnace (two stone per brick); drills output straight into the furnace and one inserter moves the output to a provider chest.

Later plate capacity comes from the approved stackable six-furnace modules (`planners/smelter_block.py`, regular-belt "basic" variant), fed by six-drill mine lines over belts, stacked in parallel or in series as demand grows.

Start the mall with four assembling machines: two permanent slots for copper cable and iron gear wheels, the rest rotating to whatever item is in demand.

Keep permanent producers for heavily used intermediates: iron gear wheels, iron sticks, copper cable and electronic circuits.

Medium poles, big poles and assembling machine 2 need steel; start steel smelting before pole stock runs low.

Then build six-drill mine and furnace lines for iron and copper, and expand them as demand grows.

Treat 12 iron, 12 copper and 6 stone furnaces as a reference allocation; measure whether ore supply and demand justify it.

## Transport

Use logistic bots to supply one or two machines and mall machines.

Once demand and sustained production exceed what one or two bot-fed machines need, switch to direct belt transfers.

## Power

Use substations only in mine and mall areas, where one substation powers many machines and there is space for it.

Elsewhere use medium electric poles: one costs about 12 iron plates and 1 copper plate, while a substation costs about 60 iron plates, 28 copper plates and 10 plastic.

For long-distance connections use big electric poles, which reach much farther for about 29 iron plates and 2 copper cables.

The substation restriction is enforced when power layouts are generated (`training/laya_power.py`): substations are offered only for mine and mall builds and never as relay chains. Builds already inside existing coverage are offered with no new poles.

## Mall expansion

Before advanced circuits are produced, the mall rotates: a few cells are borrowed from their baseline recipe to make whatever blocks the next build, then restored.

After advanced circuits are produced, the mall moves to dedicated slots, one item per slot, with no rotation unless it is extremely necessary.

A large order that would take a dedicated slot too long (for example a thousand express belts) schedules a dedicated assembly line; until that line runs, a rotated mall slot makes the first batch at priority.

Reserve the top and bottom quad slots until long-handed inserters are manufactured; regular inserters cannot replace them.

Gear and cable slots are permanent; other mall slots rotate to the current shortage, so repurpose an idle or stocked slot before building a new cell.

Every mall slot stops at a stock target, so finished intermediates stop consuming plates; products the current phase measures run ungated.

Let a retargeted slot fill its stock target before switching it again; a slot waiting for its new ingredients is busy, not idle.

## Operations

Laya's actions are the deterministic runtime's operations (training/laya_ops.py); these rules decide when each is worth running.

Each plate begins with its direct starter; build a plate's starter before any other work for that plate.

Once a plate's starter runs, open its mine and six-furnace refinery joined by one belt; a plate without a starter does not get a refinery yet. The refinery is placed as soon as it is chosen, even before every item is in stock: its placed ghosts reserve their bill, steer the mall, and let the steel seed start.

Extend an open refinery in place by one six-furnace module, with its mine, while its plate is still short.

Once a plate's refinery delivers plates, retire its starter; until then the starter keeps producing.

The bootstrap mall starts with standing slots (gear and cable first, then circuits and belts); borrow a slot for other items only once standing slots exist.

Poles are made from steel, so before any work spends poles, fence the steel starter's poles; without them neither the refinery nor steel can start.

Build the compact steel seed as soon as the iron refinery stands; it is what replenishes poles.

When an operation reports a material shortage, produce the missing items before retrying it.

While the mall is making a batch, let it finish before asking for another item; switching items makes its loans restore and preempt each other, so nothing completes.

Do not retry an operation that failed for missing materials until those materials have changed; a blind retry only adds another power bridge to another abandoned slot.

While bots build or coverage grows, wait for it rather than starting more work.

An operation the tooling reports as waiting on bots or coverage is retried after a wait, not immediately.

## How rules steer decisions

Each soft rule above is quoted verbatim in `docs/rl/laya_mission_guidelines.json`.
A rule may carry `prefer` entries: a condition on the decision (need, family,
power type, need condition, phase, bottleneck position) and a weight. Until a
trained Laya checkpoint exists, the guided policy multiplies the weights of
every rule Laya sees for that decision, keeps an exploration floor, and
samples. Edit the sentence and its preferences to change behavior; no code
change is needed. Every decision records which rules matched each option.

When a phase has more rules than fit in one decision (15 plus the adapter's
rule), Laya is shown hard rules first, then rules whose preferences match a
current option, then text-only rules; rules that match nothing on this menu
are left out and listed in `observation_digest.guidelines_omitted`.
