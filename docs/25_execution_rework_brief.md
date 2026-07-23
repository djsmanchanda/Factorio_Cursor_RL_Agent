# Path: docs/25_execution_rework_brief.md
# Purpose: M7 brief - fix ore seeding and replace fixed-roboport construction with a self-powered spidertron builder.

# M7 — Make the factory actually build and run as one network

## Live ground truth (measured 2026-07-23, clean sandbox)

The surveyed processing-unit factory was executed live several times. The
offline planner numbers are correct (Euclidean wire reach, big pole wire 32,
medium 9, substation 18, roboport link 46), and preflight passes — but the GAME
is a fragmented, ore-less mess. Measured directly:

- `ore_tiles = 0` — **58 drills, 0 mining**. No ore exists anywhere. The
  WorldSpec declares patches but execution never seeds them (and `reset` wipes
  any that existed). Nothing downstream can work.
- `roboport_networks = 3`, `electric_networks = 8` — not one of each.
- `ghosts` plateau ~1371 even after materials are topped up: the stuck ghosts
  sit in regions no *powered, built* roboport covers.

Root cause of the fragmentation: **the build never completes.** The fixed
roboport network must bootstrap outward from the one powered hub — near
roboports build the poles that power the next roboports, which then build
further out. Any material shortfall or coverage gap stalls that chain, leaving
poles as ghosts, which fragments the electric spine into islands. An incomplete
spine is why the user must hand-place poles to connect them: the connecting
pole is an unbuilt ghost.

Two independent defects, then: (1) no ore; (2) construction can't reliably
finish via the bootstrapping roboport network.

## The fix

### 1. Seed ore (blocking, do first)
Execution must seed every mining row's resource patch from the WorldSpec before
building, and after any `reset`. Acceptance: every `electric-mining-drill`
sits on its ore and reports `working`/`low_power`, `ore_tiles > 0`.

### 2. Spidertron construction vehicle (replaces bootstrapping)
Verified live that a spidertron supports: equipment grid holding
`fusion-reactor-equipment` (self-powered), `personal-roboport-mk2-equipment`
(a construction area that MOVES with it), `battery-mk2-equipment`; a trunk of
construction robots and materials; and `autopilot_destination` waypoints.

Build the factory with a self-powered spidertron instead of relying on the
placed roboport network:
- spawn it, equip reactor + roboport(s) + battery, load bots + a materials
  buffer;
- drive it on a deterministic lawnmower tour of the ghost bounding box (teleport
  step-by-step is fine and more deterministic than autopilot pathing), pausing
  so its personal roboport bots build the ghosts in range at each stop;
- restock its materials from script/provider as it depletes;
- continue until 0 ghosts remain or a real blocker is found (report it).

This removes the power/coverage bootstrapping entirely: the vehicle carries its
own power and construction area everywhere. The FACTORY's own one-power-network
and roboport network still get fully built (by the spidertron), so they end up
connected because every pole actually gets placed.

## Acceptance (measured live by tools/verify_factory_invariants.py, not preflight)

1. `ore_tiles > 0`; every drill on ore and working.
2. Exactly ONE electric_network_id across all poles/substations/machines.
3. Exactly ONE roboport logistic network; zero roboports without a network.
4. Zero remaining ghosts (or each named with a real, non-coverage reason).
5. Fluid machines hold their input fluids (amount > 0); refineries `working`.
Report the actual numbers. preflight passing is NOT evidence; the harness is.

## Notes
- Reset wipes the sandbox; `/server-save` after, or the next restart reloads
  the old state (this has caused repeated confusion).
- Settle must poll until ghosts hit 0 or genuinely plateau, not a fixed tick
  count (the old 3600-tick wait measured mid-build noise and false-failed).
- Spidertron ref: wiki.factorio.com/Spidertron.
