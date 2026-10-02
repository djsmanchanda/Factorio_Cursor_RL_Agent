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
- Compile parameterized mining, furnace, gear/belt assembly and powered-roboport
  expansion alternatives, plus two bounded waits. Seeded menu sampling preserves
  legal operation/product coverage; no strategy ranks the alternatives.
- Validate plan schema, internal footprints, live collisions, scope, construction
  coverage, required logistic delivery coverage and current network funding.
  Validate again immediately before the exact selected transaction.
- Submit only atomic funded ghosts through the existing mod layout executor.
  Configure recipes/requesters only after observing the exact bot-built entities.
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
   sampled sites, one-machine cells, two assembly recipes, and one-hop coverage
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
