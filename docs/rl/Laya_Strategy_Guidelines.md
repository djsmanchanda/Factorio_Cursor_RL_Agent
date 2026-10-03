<!-- Path: docs/rl/Laya_Strategy_Guidelines.md | Purpose: The user's factory strategy as reviewed soft guidelines that Laya reads; editing this doc changes strategy, not code. -->

# Laya strategy guidelines

These are the user's strategy rules for the `mod_playground` mission. They are
**soft guidelines**: Laya follows them by default. A deviation is allowed only
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

## Opening

Build starter mines for iron, copper and stone first.

Start the mall with four assembling machines: two permanent slots for copper cable and iron gear wheels, the rest rotating to whatever item is in demand.

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
