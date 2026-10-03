<!-- Path: docs/rl/Laya_Mall_Supply.md | Purpose: Specify the separate supplied-input mall economy, controller contract and verified mechanics. -->

# Laya supplied-input mall

The separate `mall-world-v1` environment is implemented in
`training/laya_mall.py`, `training/laya_mall_world.py` and its protected RCON
helper, with CLI `tools/run_laya_mall.py` and facts contract
`schemas/laya_mall_world.schema.json`.

## Confirmed economy

- Iron and copper each start at **1 plate/s**, with **no rate cap**.
- Each upgrade **consumes newly manufactured** 2 electric mining drills,
  10 transport belts, 4 inserters and 1 splitter.
- Add **1.5 plates/s** to the supply needed by the prioritized product queue.
  Do not also apply a time-based ramp.
- One starter chest holds 10 requester chests, 20 passive-provider chests,
  six AM1 assemblers and 12 regular inserters. No other construction items
  are granted.
- Three quad modules reserve all twelve slots at initialization. Top/bottom
  slots require an assembler and **two long-handed inserters** funded from
  available stock. Regular inserters cannot replace them.

Declared disposable fixtures provide an electric-energy interface, four
substations, one roboport with 50 construction and 50 logistic bots, a starter
provider and a return-storage chest. All technologies are unlocked as a
research fixture. These fixtures are protected and excluded from construction
cost/earned manufacturing credit.

## Isolation and accounting

A private copy of `mod_playground.zip` hosts the engine; the mall starts on an
empty `laya-mall/*` surface with its own `laya-mall-*` force. It borrows no Nauvis
stock, production counters or player inventory. Its manifest declares
`runtime=disposable_mall`, `production_authority=false` and
`comparison_eligible=false`. The separate namespace avoids the lab mod's
`training/*` orphan watchdog without weakening that watchdog or changing mods.
The saved-world adapter rejects mall manifests and vice versa. Process arguments,
loopback ports, immutable baseline/mod hashes and scenario configuration are
verified before interaction.

`training/laya_mall_supply.py` defines the independently tested integer ledger.
Supply accrues in 120ths of a plate at exact engine ticks. Paused engine steps
are at most 60 ticks per delivery quantum; fractional/blocked credit persists.
Observed insertions are recorded as **fixture input**, never factory production.
No console callback or event handler is installed.

Crafting credit excludes seed stock, ordinary recipe consumption, deployed and
reserved construction items, earlier upgrades and customer deliveries. Upgrade
and delivery items must also be physically available in provider/storage
inventories. Requester/machine/inserter WIP cannot substitute. Each consumption
removes real items and updates an engine-owned ledger atomically, with durable
nonce-bound receipts. A repeated identical nonce returns its old receipt without
changing the world; a different intent cannot reuse it. Interrupted or partial
mutations block subsequent mutation pending review. Python independently checks
every supply quantum and consumed-upgrade ledger against the pure accounting
implementation. Saves retain rates, fractions, customers, queue and receipts;
initialization of a restored episode does not reseed it.

## Decisions and measured outcomes

The initial experimental demand fixture varies origin, queue order, quotas and
belt release across seeds. Customers request 12/16/24 circuits and 24/32/48 belts,
with due times at 12,000 and 18,000 elapsed ticks respectively. Belts release at
0/1,200/2,400 ticks. Episodes are bounded at 36,000 ticks. These are curriculum
fixtures, not user-specified demand rates. The finite joint goal is both customer
quotas delivered, at least one consumed-batch upgrade and zero pending ghosts.

The palette covers gears, cable, circuits, belts, regular/long inserters, drills,
splitters and AM1s. Legal actions build or change a quad slot, pause/resume an
individual machine, reorder product priority, deliver a customer batch, earn an
upgrade, or wait. Bot-funded ghosts and configuration of observed builds reuse
the saved-world execution primitive. Live collision, current-stock reservation,
network, recipe-category and scope validators prune illegal actions. Machine
controls change only existing owned assemblers. No build-order rule selects a
strategy. The bounded eight-option menu rotates uniformly within action families.

The prioritized unfinished/released goal is expanded through live recipes into
remaining iron/copper needs, accounting for eligible stock and current rates;
that observation chooses the upgrade supply, with iron winning exact ties.
The actor chooses queue order. Observations expose stock versus WIP, batch
credit, supply rates, demand release/due/service, machine module/side/status,
robot counts and measured flows. Outcomes retain engine production rates,
fixture inputs, actual customer removals, elapsed ticks, construction cost and
lateness; late completion does not erase lateness.

Existing lab/health/saved-world checkpoints are refused before connection or
inference, including unrecorded-profile overrides. **No mall-compatible Laya
checkpoint is trained yet.** Mechanics replays are explicitly diagnostic, not
model wins or training labels. Next: independent scenario-grouped replay labels,
held-out calibration, compatible fine-tuning and matched baseline evaluation.

## Verification — October 3

77 focused tests pass across mall, supply, saved-world, input-profile, quad and
sequence contracts. Live evidence is under
`data/laya-saved-world/mall-live-20261003-f/` and `mall-live-20261003-f-restored/`:

- Exact starter kit, 50+50 bots and zero initial crafting; no early vertical slot.
- Engine/Python supply equality throughout manufacturing. A complete physically
  removed batch changed iron from 1 to **2.5 plates/s**, leaving copper at 1.
  The next two seconds delivered exactly five iron and two copper plates.
- Receipt replay left the paused world unchanged, including after save/reload.
- A reserved top slot was bot-built using one manufactured AM1 and two
  manufactured long inserters. No regular-arm substitution or pending ghosts.
- Customers received all **12 circuits and 32 belts**, with one earned upgrade
  and zero pending ghosts at tick 30,180. This mechanics sequence was late;
  it proves the economy works, not an optimized policy or on-time service.
- The developed real-tokenizer request fits at **1,389/2,048 tokens** with eight
  options and the unchanged 256-token head budget; no truncation/model inference.
- A copied paused prefix restored identical full facts without regranting stock,
  then retained receipt idempotence and exact upgraded plate delivery.
- The source save remains SHA256
  `9763436071d2ad138e2844a81c7bbd6891218f612c9bf69dc5bede6517c6d5bb`.
  All test hosts, including failed diagnostic hosts, are stopped.

Earlier failures exposed container lookup/API differences and the lab namespace
watchdog. Their stopped roots preserve evidence and are not learning data.

## Running a fresh episode

```bash
.venv/bin/python tools/run_laya_mall.py prepare \
  --root data/laya-saved-world/mall-example --seed 0
.venv/bin/python tools/run_laya_mall.py observe \
  --root data/laya-saved-world/mall-example
.venv/bin/python tools/run_laya_mall.py replay \
  --root data/laya-saved-world/mall-example \
  --recipe iron-gear-wheel --module 0 --side west
.venv/bin/python tools/run_laya_mall.py stop \
  --root data/laya-saved-world/mall-example
```

Use a fresh root for preparation; existing evidence is preserved. Default private
ports are game 34196/RCON 27016. `run --checkpoint ...` requires recorded
`mall-world-v1` training support. Supplied power, robots, research and plates are
explicit fixtures: this is separate from exact-save gameplay, sustained demand
validation and self-sustain/production promotion evidence.

Lifecycle: start a new mall Python consumer/fresh disposable episode. No mod
redeploy, GUI change or real-base restart is required. No background mall
training job or automatic promotion was launched by this implementation.

## Live Factorio spectator viewing

A connected GUI client can watch an owned mall at normal engine speed:

```bash
.venv/bin/python tools/run_laya_mall.py watch \
  --root data/laya-saved-world/mall-example \
  --observer YOUR_FACTORIO_NAME --watch-seconds 600
```

The server must already be running. Connect in Factorio to its manifest game
endpoint (default `127.0.0.1:34196`) using the exact frozen server mod set.
`watch` waits for the named observer, moves that player to the isolated mall
force/surface in spectator mode, and advances exact 60-tick supply quanta at
speed 1. Omitting `--observer` selects only a single unambiguous connected player.
No model or build decisions run. Disconnecting pauses further steps while the
viewer waits, with a bounded connection timeout. The requested duration is
clamped to the existing episode tick budget; expiry leaves the world paused and
the server available for inspection. `live-view.json` records the actual view
state, observer, scope and advanced ticks. Stop the server with the usual `stop`
action when finished.

On October 3, `mall-view-20261003-a` was started from an immutable copy of the
verified completed-mall prefix. A separate GUI profile under that root's
`client/` uses copied, hash-matched mods; the normal GUI profile was not redeployed.
Factorio 2.1.20 joined as `LeonidasUIP`, and the engine confirmed the player on
`laya-mall/0-9f99e638` / `laya-mall-0-9f99e638` with spectator control. Two normal-speed
seconds delivered exactly five iron and two copper plates, with zero pending
ghosts. Four focused viewer tests cover scope selection, bounded duration,
disconnected/ambiguous observers and speed validation. This is live mechanics
viewing of the completed mall, separate from training or policy evaluation.

Lifecycle: the private mall server and isolated GUI client are intentionally
left available for the user; normal-speed viewing pauses at the remaining
episode budget. No mod deployment or real-base lifecycle action is required.
