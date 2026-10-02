<!-- Path: docs/rl/Laya_Saved_World_Adapter.md | Purpose: Document the disposable mod_playground adapter, live mechanics evidence and remaining policy gates. -->

# Laya saved-world adapter

## Implemented boundary

`tools/run_laya_saved_world.py` is a separate entry point for exact copies of
`mod_playground`. It does not modify the lab runner's training identity checks,
invoke the deterministic cascade, or grant production authority.

The CLI owns disposable roots strictly below `data/laya-saved-world/`. Preparation
copies the source save into an immutable baseline and a private server, freezes
both project mods from an explicit installed-mod directory, records hashes,
and starts separate loopback game/RCON ports. Fleet mode prevents GUI deployment
and repository-save snapshots. Before RCON, the adapter verifies the OS process's
save, config, mod directory and ports against this manifest.

`saved-world-v1` facts have a separate schema and checkpoint input profile.
Existing lab checkpoints reject these inputs, including the unrecorded-profile
diagnostic override. Training and calibration must explicitly cover the new
profile before a checkpoint can execute model decisions here. Changing checkpoint
metadata does not constitute training or approval.

## Tools and execution

- Observe actual provider/storage stock per network, outstanding ghost bills,
  player inventory separately, bot counts/coverage, power-grid anchors, machine
  states/recipes, resource samples, research and engine production/consumption.
- Compile parameterized mining, furnace, enabled solid-recipe quad slots and powered-roboport
  expansion alternatives, plus two bounded waits. Seeded menu sampling preserves
  legal operation/product coverage; no strategy ranks the alternatives.
- Validate plan schema, internal footprints, live collisions, scope, construction
  coverage, required logistic delivery coverage and current network funding.
  Validate again immediately before the exact selected transaction.
- Submit only atomic funded ghosts through the existing mod layout executor.
  Defer recipes, requester sections and inventory limits until observing the
  exact bot-built entities. Each quad slot owns a stable requester section;
  changing its recipe preserves other slots' sections.
  Before long-handed inserters exist in available provider/storage stock,
  only the left/right slots can be submitted. Each new top/bottom slot must
  fund both required long-handed inserters; regular inserters cannot substitute.
  Selecting a horizontal module reserves its future vertical machine/arm
  footprints without placing ghosts or reserving construction items there.
  Other builds are rejected if they occupy that reserved space. Reservations
  survive pending construction, runner restarts and saved-prefix forks and
  enter the policy observation and replay fingerprint.
  Pending configurations reconcile the already selected intent. Ambiguous
  interrupted submissions require review and are never silently replayed.
- Advance the paused world by exact engine ticks, measure independent engine
  counters, and retain pre/post states, selection, pruning, submission,
  configuration, code hashes and measurements in durable receipts. Recent
  measured flows enter the next model state.

The RCON Lua helper is Python-owned tooling, pinned at adapter startup. It is
not a mod deployment and does not add commands to the installed mods.

## Usage

```bash
.venv/bin/python tools/run_laya_saved_world.py prepare \
  --root data/laya-saved-world/example \
  --source-save saves/mod_playground.zip \
  --mods-source ~/.local/share/factorio-rl/deterministic/mods

.venv/bin/python tools/run_laya_saved_world.py observe \
  --root data/laya-saved-world/example

# Mechanics verification: explicitly selected, never credited to Laya.
.venv/bin/python tools/run_laya_saved_world.py replay \
  --root data/laya-saved-world/example --option-id <presented-option-id>

# Requires a genuinely trained saved-world-v1 checkpoint.
.venv/bin/python tools/run_laya_saved_world.py run \
  --root data/laya-saved-world/example --checkpoint <saved-world-checkpoint> \
  --decisions 10 --measure-ticks 1800

.venv/bin/python tools/run_laya_saved_world.py stop \
  --root data/laya-saved-world/example
```

Use a fresh root for a fresh episode. Prepared test servers remain available
and paused between commands until stopped. Graceful Factorio shutdown saves
the advanced disposable world; it does not change the source or immutable
baseline. Preparation records the first paused tick rather than claiming that
startup advanced zero ticks. These runs are not head-to-head promotion evidence.

## Observed verification — October 2, 2026

Evidence: `data/laya-saved-world/adapter-verified-20261002/verification.json`,
transaction receipts and server logs. Actual source SHA-256:
`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.
Factorio 2.1.20, frozen installed shared mod 0.2.0 and lab mod 0.1.0,
Nauvis/player, loopback ports 34198/27018.

| Explicit mechanics replay | Engine-clock window | Observed outcome |
|---|---:|---|
| Mining | 60 seconds | 37 iron ore; working miner |
| Smelting | 120 seconds | 34 iron plates; working furnace |
| Gear assembly | 120 seconds | 53 gears |
| Belt assembly | 120 seconds | 36 belts |
| Expansion | 60 seconds | Second powered roboport; one connected logistics network |

Each transaction ended with zero pending ghosts. Production windows include
construction and configuration, and do not assert steady-state capacity or
joint sustained demand. All choices were explicit mechanics replays. The
earlier failed-smelting world is preserved separately at
`data/laya-saved-world/adapter-smoke-20261002/`; fixing reversed inserters in
the generator preceded the fresh verified run.

Twenty-one focused tests passed. The real checkpoint tokenizer accepted the
final 10-option state with measured flows at 1,532/2,048 tokens. Model weights
were not loaded for that check. `r21-final` refused the new profile before
opening the adapter or performing model actions. No Laya gameplay, new training,
baseline comparison or promotion is claimed. Original save and immutable
baseline hashes were verified unchanged; both test servers were stopped.

## Remaining limitations and ordered work

1. **Matched outcomes and data.** Capture paused initial/prefix saves, replay
   competing actions under identical budgets/clocks/mods, and label measured
   continuations with completion-first preferences. Keep all prefix relatives
   in one split. Mechanics replay choices alone are not preferred labels.
2. **Train and test Laya.** Fine-tune/calibrate with `saved-world-v1`, retain
   lab regression coverage, then run the bounded saved-world director from
   fresh copies. Inference failures/uncertainty stop safely; no baseline fallback.
3. **Expand the executable menu.** Current scope is a fixed 256×256 survey,
   sampled sites, one-machine furnace cells, AM1 quad slots, and one-hop coverage
   expansion using stocked substations. Add scale/route alternatives, repair,
   mall funding, actual power generation, fluids and research mechanics with
   measured curricula. Logistic transport is currently the only cell-delivery
   realization; it is not evidence that bot hauling beats continuous belts.
4. **Full comparison.** Add explicit joint rate/sustain acceptance, lifecycle
   costs, matched startup timing and an independent full deterministic opening
   from identical source conditions. This adapter's receipts cannot replace
   the existing held-out promotion gate.

Lifecycle for this milestone: launch new adapter processes to use the Python
and RCON helper changes. No installed mod redeployment, production-server
restart or GUI change is required.

## Quad and counterfactual verification — October 3, 2026

Resumed the unfinished tranche from the October 3 handoff. Live verification
found and corrected two mechanics failures: clearing a ghost chest's nonexistent
inventory, and vertical long arms both feeding the machine instead of extracting
its output. Settings now apply after construction; the shared quad generator
uses a requester-facing input arm and a machine-facing output arm. This shared
Python geometry fix also applies to future deterministic quad plans.

The original starter kit matches exactly, including 50 construction and 50
logistic robots. A saved paused prefix restored its tick and observed-state
fingerprint exactly. Recipe/category checks, reused-entity funding and shared
requester sections were exercised in Factorio 2.1.20 with frozen installed mods.

Fresh evidence: `data/laya-saved-world/quad-final-20261003/`. Explicit replays
mined iron and copper, smelted plates, made gears, cable, circuits, inserters and
long-handed inserters, then delivered 18 belts from a north slot and 10 from a
south slot during their respective checks. A recipe change manufactured two
additional assemblers before the south expansion. Every transaction ended with
zero pending ghosts. These windows include construction/configuration; they
do not establish joint sustained throughput or learned decisions. Earlier failed
worlds and diagnostic receipts remain preserved separately.

`tools/collect_laya_saved_world.py` saves one paused prefix and sequentially
forks its alternatives, preserving ancestor identity and common engine horizons.
It stops at the first tool failure, retains outcomes, excludes invalid/all-failed
comparisons, and writes train-only cases for the source lineage. Example:

```bash
.venv/bin/python tools/collect_laya_saved_world.py \
  --parent data/laya-saved-world/example \
  --output data/laya-saved-world/example-counterfactuals \
  --menus 1 --seed 4 --horizon-ticks 3600 --sustain-ticks 1200
```

The diagnostic pilot at `data/laya-saved-world/cf-pilot-20261003/` measured eight
alternatives with one starting fingerprint and identical 3,600-tick horizons.
Mining sustained 0.8/0.6/0.6/0.6 ore/s over four 300-tick endpoint windows;
seven other actions failed the ore objective without tool/safety errors. One
validated completion-first case resulted. This single-world diagnostic has no
independent calibration/evaluation coverage and is insufficient for fine-tuning.
It uses a fixed measurement continuation, not a full factory policy rollout.

The final eight-option state with measured flows fits the real tokenizer at
1,811/2,048 tokens; no model weights were loaded. The source hash remains
`9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.
All owned test servers were stopped. Restart affected Python consumers to use
the changes; no mod deployment or production/GUI restart was performed.

The separate mall supply contract and its implemented accounting seam are in
[Laya mall supply](Laya_Mall_Supply.md). Its live fixture remains separate work.

The user's follow-up clarified the horizontal-first expansion contract above.
Sixty-one focused checks pass. Read-only live legality probes found no admitted
vertical slots with 20 regular inserters and zero long-handed inserters, rejected
a pole in an empty reserved vertical footprint, and admitted a correctly funded
slot with two long-handed inserters. A paused prefix fork retained all reserved
centers and matched the full observed-state fingerprint. Evidence:
`data/laya-saved-world/quad-reservation-verification-20261003.json` and
`quad-reserved-fork-20261003/reservation-restoration.json`. These probes placed
no entities or ghosts. All test servers were stopped; the source hash is unchanged.
Lifecycle: restart affected Python runners; no mod deployment or production
server restart is required.
