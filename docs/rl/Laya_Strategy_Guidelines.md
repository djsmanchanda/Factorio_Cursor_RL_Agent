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

Serve the bottleneck: prefer actions on items in the current bottleneck chain, deepest unmet input first.

Make missing construction items before their stock runs out; a build blocked by a missing item is a reason to make that item.

Wait only when nothing useful can be built, or while construction and deliveries are still arriving.

Extend robot coverage toward resources and sites a current need requires; coverage that serves no need spends poles and roboports for nothing.

These come from the deterministic runner's history: of 184 runs since 2026-09-05, 74 ended with no progress on a construction item (steel, drills, splitters, furnaces, fast inserters, poles) whose inputs had no reliable producer, and its furthest run toward plastic stopped when gear and iron-stick stock drained with no permanent producer.

## Planning

Plan land before building: protect ore patches for mining, keep a smelting strip beside each plate's patch for its mine and furnace line, and keep the mall together in one reserved zone.

The reservations are enforced when options are generated (`training/laya_site_plan.py`); the mall site is Laya's first decision of an episode.

## Opening

First priority: iron and copper starter mines and furnaces; stone and the mall follow.

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

Reserve the top and bottom quad slots until long-handed inserters are manufactured; regular inserters cannot replace them.

## How rules steer decisions

Each soft rule above is quoted verbatim in `docs/rl/laya_mission_guidelines.json`.
A rule may carry `prefer` entries: a condition on the decision (need, family,
power type, need condition, phase, bottleneck position) and a weight. Until a
trained Laya checkpoint exists, the guided policy multiplies the weights of
every rule Laya sees for that decision, keeps an exploration floor, and
samples. Edit the sentence and its preferences to change behavior; no code
change is needed. Every decision records which rules matched each option.
