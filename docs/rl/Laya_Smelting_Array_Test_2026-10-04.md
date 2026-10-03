<!-- Path: docs/rl/Laya_Smelting_Array_Test_2026-10-04.md | Purpose: Reproducible, isolated belt-only smelting mechanics evidence and every observed failure. -->

# Four express belts of ore to four express belts of plates

Iron and copper both reached **180 ore/s consumed and 180 plates/s produced**
over a common **7,200-tick / two-game-minute** acceptance window. Each of the
four output belts delivered **45 plates/s**. This is a scripted mechanics test
of the user's design, not a learned Laya policy result or promotion evidence.

## Shape and sizing

The array consists of **16 parallel Start–Middle–Middle–End lines**, each with
**18 electric furnaces**, for **288 furnaces** total. The approved Start has
no furnaces; each Middle has six, and End has six. The user count of 24 for
this serial shape is therefore corrected to 18 without changing its shape.

Live prototype checks establish normal furnace crafting speed 2 and recipe
energy 3.2 seconds: **0.625 plates/s per furnace**, hence **11.25/s per line**.
Four lines consume one **45 ore/s express input belt** and merge into one
**45 plates/s express output belt**. Four such groups provide 180/s. The
express prototype speed is 0.09375 tiles/tick, checked against 45 items/s.

The design retains `training/laya_lines.line_units(recipe, 3, ox, oy)` and its
16-tile parallel pitch. It does not lengthen or remove an existing End.
The approved line bodies keep **regular belts, regular inserters and all
splitter priorities**. New external 1:4 splitter fanouts and 4:1 output merges
use express belts/splitters. There are no tier substitutions inside the
approved templates and no requester, provider or other chest in the production
path. Existing line, power, validation and ghost-execution modules are reused,
not forked or redirected.

## Measured expansion

Each row below passed independently for both iron and copper over 7,200 ticks.
The ore-consumption and plate-production numbers come from engine production
statistics; output delivery is independently counted at the terminal belts.
Per-line engine `products_finished` deltas agree with the force/surface counters.

| Lines | Furnaces | Ore consumed/s | Plates produced/s | Output belts, plates/s |
|---:|---:|---:|---:|---|
| 1 | 18 | 11.25 | 11.25 | 11.25, 0, 0, 0 |
| 4 | 72 | 45 | 45 | 45, 0, 0, 0 |
| 8 | 144 | 90 | 90 | 45, 45, 0, 0 |
| 12 | 216 | 135 | 135 | 45, 45, 45, 0 |
| 16 | 288 | 180 | 180 | 45, 45, 45, 45 |

Every individual line averaged 11.25/s. At full capacity every output belt
also delivered exactly 45/s in **each of four consecutive 1,800-tick
subwindows**. Furnace crafting is discrete; shorter production-counter windows
fluctuate slightly around 180/s, while the complete two-minute production and
consumption totals are exactly 21,600 each. Belt ore stock did not grow,
fixture insertion attempts were never denied in acceptance windows, and
all submitted ghosts were constructed.

Primary step-by-step evidence:

- `data/laya-saved-world/smelting-iron-20261004-g/evidence/measurement-{1,4,8,12,16}.json`
- `data/laya-saved-world/smelting-copper-20261004-a/evidence/measurement-{1,4,8,12,16}.json`

Final reporting/budget verification repeats the full-line and full-array
checks under `smelting-iron-20261004-h` and `smelting-copper-20261004-b`.
Their `fixture.json` captures live capacities, the declared construction bill,
robot count and power fixture. `decision-states.jsonl` includes the typed
pre-action state, available option/features, explicit mechanics selection,
execution receipt and measured outcome for header and line builds. Pending
selection snapshots are written before mutation. `measurement-16.json` also
checks **remaining construction items + bot-built entities = the initial bill**
for every declared item. No top-ups occur. Final saves are retained as
`server/saves/smelting-final.zip`.

## Fixture and isolation

Each test starts from an immutable copy of `saves/mod_playground.zip`, in its
own server directory and explicit `training/smelting-*` surface and
`training-smelting-*` force. Source SHA256 remained:

`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.

The RCON-only, inert-until-initialized `smelting_fixture.lua` supplies only ore,
directly onto the declared input belts, with separate successful/denied fixture
counters. Terminal removal counts plates transported to each output endpoint;
it never increments the factory's production or ore-consumption statistics.
Partial-array steps declare only the matching required ore rate; all four
inputs run fully compressed at 45/s for the complete array.

The fixture declares 16 roboports, 3,200 construction robots, protected power
sources/scaffold and passive providers **outside the production path**. Its
one-time construction budget funds all ghosts with actual provider items.
There are **zero requester chests anywhere on the test surface**. No mining
is used. Power is deliberately a declared fixture, not production-learning
or energy-efficiency evidence. Regular inserter capacity bonus is zero;
furnaces use normal quality with no modules or beacons.

Private mod staging copies the existing deterministic mod unchanged and the
training lab into the test root. Installed mods, real saves, mall/viewer
hosts, clean-runner hosts and the console are not deployed, reset or stopped.
Private test mod hashes and process/save/port identities are pinned in each
manifest before interactions. The final endpoint is **34270 / 27170**;
34230/27130, 34232/27132 and console 9140 remain outside this test.

## Failures, preserved rather than hidden

All roots below remain under `data/laya-saved-world/`.

| Root | Failure | Cause and correction |
|---|---|---|
| `smelting-iron-20261004-a` | Fixture initialization; then error-report cleanup | Factorio 2.1.20 has no `LuaForce.stack_inserter_capacity_bonus`; remove that unsupported setting. The initial error path also lacked its evidence directory and skipped automatic stop; create the directory first, stop the owned host explicitly, and put cleanup before reporting. |
| `smelting-iron-20261004-b` | Surface disappeared during header construction | The new fixture had no training-lab ownership registration, so the orphan watchdog deleted it. Add exact fixture ownership to the existing watchdog; normal episodes and unrelated orphan cleanup remain unchanged. |
| `smelting-iron-20261004-c`, `-d` | Host preparation rejected | Another agent's `exp-c0-goal-weight-20261004/goal-weight-10-s3` host occupied 34240/27140 after the previous test stopped. Identify it without exposing its secret, leave it untouched, and select free 34270/27170. Preparation failures now receive a ledger/summary entry. |
| `smelting-iron-20261004-e` | Snapshot failed once furnaces existed | Removed inventory enum names `furnace_source`/`furnace_result` produced a nil inventory index. Use the engine's `crafter_input`/`crafter_output`. |
| `smelting-iron-20261004-f` | Four-line expansion left 22 static ghosts | Upper/lower fixture roboports at y=-40/+72 left a two-tile construction coverage gap. Move the lower fixture row to +64 and validate actual funded construction coverage before submission. The preceding one-line 11.25/s acceptance remains valid evidence. |

The successful iron/copper runs have no throughput shortfalls, placement
failures or unfunded builds. Host-level setup failures are not policy mistakes
and are not positive training labels. All attempts are recorded through
`tools/run_ledger.record(...)` as **system `rl-lab`**. The earliest preparation
failures required explicit backfill; unknown timings stay unknown.

Relevant engine APIs: [transport-line fixture methods](https://lua-api.factorio.com/latest/classes/LuaTransportLine.html),
[current inventory enums](https://lua-api.factorio.com/latest/defines.html#defines.inventory),
and [quality-specific prototype crafting speed](https://lua-api.factorio.com/latest/classes/LuaEntityPrototype.html#get_crafting_speed).

## Reproduce and lifecycle

```bash
.venv/bin/python tools/run_laya_smelting_test.py \
  --root data/laya-saved-world/smelting-iron-NEW \
  --recipe iron-plate
.venv/bin/python tools/run_laya_smelting_test.py \
  --root data/laya-saved-world/smelting-copper-NEW \
  --recipe copper-plate
```

Use fresh roots and free explicitly chosen ports when another test is running.
The runner freezes private mod copies, preserves evidence and stops only its
verified owned host. Existing clean/mall/viewer/production ports are rejected.

Required lifecycle for the verified test: **nothing further**; owned test
hosts are stopped. Future test launches automatically load the new lab helper
from a fresh private copy. To use the changed lab mod on an existing training
worker, explicitly redeploy **only to that stopped worker**, restart its
Factorio runtime and affected consumer, and synchronize/restart a GUI only if
joining it. No shared worker deployment or restart was performed here.

Verification: **30 focused tests pass** (array shape/collision/capacity acceptance,
fixture ownership/inertness/counter separation, typed-state isolation, existing
line behavior and lab contracts). Native Lua parsing and Python compilation
also pass. The separate optional Lupa suite is unavailable in this interpreter;
its skipped cases are not counted as passes. Native Lua ownership tests and
live private-host reloads exercise the changed lab lifecycle directly.
