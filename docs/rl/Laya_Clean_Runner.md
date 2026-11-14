<!-- Path: docs/rl/Laya_Clean_Runner.md | Purpose: Current capabilities, decision/evidence contracts and acceptance gaps for the separate disposable Laya runner. -->

# Laya runner contract

Status: migration prototype, updated October 8, 2026. Product intent and
ownership live in [architecture](../architecture.md); delivery order lives in
[the implementation plan](Laya_Implementation_Plan.md). The deterministic
workflow remains usable until a separately authorized replacement passes
matched evaluation. Read [current evidence](Laya_Current_Handoff.md) for run state.

## Runtime and decision authority

`tools/run_laya_clean.py` prepares and runs disposable copies of
`mod_playground`. Its two action paths share the saved-world lifecycle,
status board, guided policy, journals and console, but expose different choices.

| Path | Current choices | Remaining limits |
| --- | --- | --- |
| `--actions geometry` (default), `training/laya_clean_runner.py` | Land plan, funded starter/mine/smelting/assembly alternatives, coverage and waits; mall slots are switched by the automatic slot manager. | Bounded site/scale menus; general fluid, research, upgrade and repair choice coverage remains incomplete. Component fixtures are separate evidence. |
| `--actions deterministic`, `training/laya_ops_runner.py` | Plate starter/foundation/extension/retirement, baseline mall item, shortage batch, steel fence/seed and waits. | `training/laya_ops.py` still calls strategic builder wrappers that choose prerequisites, lender, site, scale, transport and recovery. Coverage can occur inside a request rather than as a separate offered choice. |

The target split is **preview legal alternatives → Laya chooses → execute the
selected transaction → reconcile measured outcome**. Current wrapper choices
are migration debt, catalogued in [decision authority](laya_decision_authority_inventory.md).
They are not proof that policy authority has already moved to Laya.

Both paths currently use `GuidedPolicy`: shown rule preferences multiply and a
15% uniform exploration floor preserves legal alternatives. `--policy exploration`
is the uniform comparator. The guided sampler does not read prose. No learned
checkpoint is fitted to ops `decision-states.jsonl` by the current campaign;
L1–L3 lab models and supplied-input mall learning are separate runtimes/profiles.

## Tools and results

Reuse observations from `GameBridge`/`live_base`, planner realizers, material and
collision validators, bot construction/configuration, and measured counters.
Reuse choosing wrappers only behind an explicitly documented migration boundary;
extract their alternatives rather than adding another hidden priority branch.

The required result contract distinguishes submitted, constructing,
prerequisite advanced, batch active, output delivered, producer ready and failed.
Receipts must retain transaction identity, selected parameters, typed cause,
ownership, resource use and observed postconditions. A successful return is not
by itself proof of delivered output or a standing producer.

Current geometry transactions preserve submitted plans and pre/post facts.
Current ops wraps ordinary returns, including `None`, as `ok=True` and records
only part of the result. Its history/lifecycle features and error containment do
not yet satisfy the complete contract. See the [audit](Laya_System_Audit_2026-10-08.md)
for reproductions; documentation cleanup does not repair these implementation gaps.

## Decision facts and rules

The target compact projection keeps facts needed to discriminate available choices:

| Keep in the decision | Preserve outside the decision |
| --- | --- |
| Objective, current gap and measurement window | Full mission chronology and previous observations |
| Accessible stock, reservations by owner and requester WIP | Complete inventory and material ledgers |
| Active transaction, remaining bill, blocking input and next event | Full plans, receipts and execution reports |
| Option cost, feasibility and production displaced by a loan | Detailed surveys and alternative geometry |
| Recent relevant outcomes and facts changed since failure | Full attempt history, rationale and rule-to-fact mappings |

Unknown evidence stays unknown. The ops path currently lacks bills, requester
draws and lender consequences in its menu/state; several scarcity features
therefore default to false. Add those facts before training on their values.

Use the [strategy document](Laya_Strategy_Guidelines.md) for preferences.
Geometry uses `laya_mission_guidelines.json` and ranks applicable rules for its
count limit. Ops normally uses `laya_ops_guidelines.json`, then keeps rules in
catalog order within the character room left after its brief. The existing brief
contains GOAL/NOW/NEXT/MAJOR/RECENT/TRIED/SINCE LAST BUILD and rule-ID annotations.
Character budgets are not tokenizer guarantees. Further compaction or selection
changes need shared training/inference representation and a recorded comparison.

## Objectives and acceptance

`training/laya_mission.py` currently advances **Laya rate proxies** named
C0/C1/C2/C3/C4/C6/S1. They use production counters and a 95% rate threshold;
the measurement can be shorter than its requested window. Reached phases are
retained. S1 tests science-pack production, not completed research.

These proxies do not establish `orchestrator/checkpoint_milestones.py` acceptance:
its C1 requires healthy starters and four working mall assemblers; C2 requires
healthy productive foundations, delivered output and retired metal starters.
It also includes C5 and advanced-circuit consumption. Reports must name the
predicate version, starting state and sustain window, and separate production,
delivery and consumption. Do not compare proxy flags with deterministic
checkpoint flags as though they were identical.

Replacement requires the same starting save/kit/research, mods, objective and
budgets as the incumbent, shared milestone predicates, sustained service,
completed `mining-productivity-4`, zero safety violations and held-out
non-regression. A phase marker or fixture result grants no production authority.

## Operation and evidence

Console: `tools/laya_ops_server.py`, normally loopback `127.0.0.1:9140`.
CLI: `tools/run_laya_clean.py prepare|run|stop --root data/laya-saved-world/<name>`.
Live execution is the default; `--decisions 0` removes the decision-count cap,
not stall/game/wall limits. Ops tooling requires a live clock because it waits
on bots in wall time; geometry also has the paused `--stepped` path.
Lifecycle and mod procedures remain in [Factorio operations](../factorio_operations.md).

Artifacts: `logs/laya-runner.log`, PID/heartbeat sidecars,
`evidence/clean-decisions.jsonl`, `evidence/decision-states.jsonl`,
`evidence/latest-status.json`, and geometry transaction receipts. The local run
index is `data/laya-saved-world/laya-runs.jsonl`; the shared ledger is maintained
by `tools/run_ledger.py`. The helper is optional (`--no-helper` disables it).
Read terminal evidence before a cached status board or experiment report.

Geometry supports hash-verified earned-C1 export/resume with
`--export-checkpoint`/`--resume-bundle` and `training/laya_checkpoint.py`.
This is not general restart recovery: the ops runner does not restore its
history and deterministic ledgers through that geometry checkpoint contract.

Geometry defaults to a 12-decision stall limit; ops uses 18,000 game ticks
without its tracked progress signals. These signals can miss useful work.
Unexpected caught tool exceptions and typed blocks are different: a reported
zero `failures` is not zero operational defects. Escaping errors can skip the
current run index. Complete-cohort crash/interruption accounting remains a
required harness fix before interpreting promotion comparisons.

Next reusable work: truthful receipts/economic facts, the partial-foundation
steel cycle, explicit loan alternatives, faithful measured labels and a
compatible learned policy; then missing goal actions and matched acceptance.
Coordinate shared mall/adapter files through their latest ownership handoff.

Oil facts (November 7, 2026; unit and Lua-fixture tests only): the saved-world survey exports enabled recipes with a fluid ingredient or product as typed `facts["fluid_recipes"]` (ingredients/products with type and amount, energy ticks, category, compatible machine speeds); `facts["recipes"]`, the solid build menu, is unchanged. The status board expands fluid goals through them (`production_recipes`): per product, the shortest chain to raw resources, ties to a recipe already running, then fewer ingredients. Plastic at 0.5/s reads as one chemical plant, 5 petroleum gas/s from one oil refinery (basic oil processing) and about 11 crude/s; crude oil and water are raw. A read-only `facts["oil_field"]` survey (512 tiles of generated terrain around the lowest roboport, else spawn) reports the nearest crude-oil patch (centre, distance, wells, total amount, yield, crude/s with one pumpjack per well) and the nearest water tile; its compact form is `observation_digest.oil_field`. No oil operation exists yet, so fluid needs stay in the menu's `blocked` as `no_candidate`. Fluid flow is not measured, so crude oil and petroleum gas never read as satisfied from counters. A candidate may declare its own `scope_bounds` (unit and Lua-fixture tests only); the Lua validator then checks that candidate's actions against it instead of the global ±128 bounds. The adapter refuses a declared scope before Lua (`scope_malformed`, `scope_unsurveyed`, `scope_excludes_actions`, `scope_too_wide`, `scope_out_of_reach`, `scope_off_route`) unless it tightly contains the candidate's actions, lies within the oil search radius of the survey anchor, and touches the outpost (128 tiles around the surveyed oil field, for the cell and its coal mine), the global bounds or a 64-tile corridor along the base-to-oil line (reach hops); the option's state `scope` shows it. Candidates without one are unchanged.

## Automated mall slots and decision cadence (November 8, 2026; unit tests, replay and a live smoke)

User intent, 2026-11-08: Laya decides *what to make and in what quantity*; slotting is automated, and the decision
rate rises over time with decisions in parallel on different aspects.

- **Slot manager** (`training/laya_mall_slots.manage`, run before each decision): it has the mall's assemblers and
  maximizes output and working occupancy. Priority: overdue request work, construction shortfalls,
  bottleneck chains, then other unmet board items; missing precursors come before their consumers. It frees idle, impossible
  and off-board cells, keeps cells already set to a demanded item, gives each item ceil(gap / machine rate) cells,
  prefers W/E cells for the most urgent items, and applies all switches in one configure. Switches leave
  `retarget` receipts with authority `slot_manager`. Laya gets no retarget options (`mission.rotating-mall` and
  `mission.slot-settle` are inert for this runner).
- **Automatic pending requests** (2026-11-14): active compiled logistics filters on requester/buffer
  chests, characters and spidertrons are observed with local inventory and assigned deliveries. The
  allocator persists each continuous shortage's age in `evidence/mall-requests.json`. At 1,200 game ticks
  (20 game seconds), an unfilled request becomes production work on the next runner observation,
  oldest first and with missing precursors first. Transferable stock is allocated once per network,
  after construction reserves and assigned pickups. Existing producers run ungated; feasible overdue
  work can displace a recent lower priority batch. Fulfillment/removal clears the age; a world clock
  rollback starts new ages. Ghost construction shortages retain immediate demand. This is allocator
  automation, independent of policy selection or strategy weights. Rates, machine availability and
  recipe legality still limit execution; smelting/fluid inputs remain upstream needs, and unsupported
  quality requests are reported explicitly. Request ages, quantities and queue status appear in decision
  records and latest status. No exact 20-second reaction is promised between observations.
- **Obtainable now** (`laya_factory_status.missing_input`): nothing is offered, slotted or made a stock need
  whose inputs cannot be had now (raw, in stock or measured as produced; the item's own stock never counts).
  Before plastic, chests are blocked facts (`unobtainable:<input>`), not options.
- **Mall lattice** (`training/laya_mall_block.py`): module centres at anchor + i·(6,5) + j·(−3,10), the user's
  reference layout (one requester per four machines, one passive chest per two). Generator assembler sites are
  dropped once a mall block exists. Laya is offered one slot: W/E of the nearest modules first, then N/S beside a
  built W/E slot.
- **Line taps**: one topology per line. One fast tap while chests cannot be made; full rate afterwards.
- **Steel expansion**: the shared deterministic conversion-line planner offers two, four or six
  electric furnaces on common input/output belts, with one requester and one provider per line.
  No additional single-furnace chest cells are offered. Pending lines reconcile before another
  capacity offer; sufficient installed capacity waiting for iron does not trigger furnace growth.
- **Retired starter providers**: keep each exact owned provider available while its stock drains.
  Remove the machinery first; bots may recover the chest only below five occupied inventory slots.
  Partial stacks and all qualities count; unknown occupancy retains the chest. Receipts keep the
  deferred chest eligible on later observations and after a runner restart.
- **Cadence** (live play): each decision is submitted without waiting (`construction_pending`; reconcile finishes
  it). A cycle takes up to `parallel_aspects` (4) decisions on distinct aspects (production, smelting, coverage,
  oil, external) whose footprints and bills do not conflict, then advances
  `cadence_ticks(t) = 1800 / (1 + ln(1 + (t − 36000) / 36000))` ticks after a 36,000-tick delay, floored at 300.
  Stall patience is also kept in game time (`stall_decisions × 3600` ticks). Receipts are read once per change
  (`training/laya_receipts.py`), and each decision record carries `wall_s` per step.

## Oil options: `reach`, `coal-feed` and `oil` families (November 7, 2026; unit tests with recorded planner outputs only)

`tools/laya_oil_options.py` adds two option families that follow the "Oil facts" survey above (`Settings.oil_options`, default on). `tools/run_laya_clean.py` injects it as `run(..., oil_options=...)`, because `training/` may not import `orchestrator.stage_*` (`training/isolation.py`). It is an adapter only: it reads the disposable world through the deterministic read-only functions and drives the pure planners `plan_reach_step`, `plan_power_bridge` (`orchestrator/stage_services.py`) and `plan_oil_cell_intent` (`orchestrator/stage_chemical.py`), answering each survey they name, then rerunning them. The families read nothing and the menu is unchanged unless `facts["oil_field"]` is found and plastic, petroleum gas or crude oil is an unmet board need.

- **`reach`** (need: the most upstream unmet item of crude oil, petroleum gas, plastic): while the oil-field centre, then any planned cell action, lies outside construction coverage (Euclidean distance from a powered port, within its construction radius), one candidate is the next `plan_reach_step` hop: its power bridge and roboport as ghosts. It avoids the planned cell footprint. Pending ghosts are blocked as `reach_in_flight`, missing ports as `reach_no_source`, and geometry failures as `reach_plan_failed`.
- **`oil`** (need: plastic first): keep the planner's packet/phase boundaries. Release funded sections as materials appear, with outstanding ghost work bounded by the network's construction robots, their availability, observed roboport charge and logistic-bot availability. Infrastructure-only packets (belts, power or pipes) are real work: require a nonempty validated bill, without requiring a chemical plant in every packet. Future packet shortages remain mall demand, but do not reserve the entire district bill. Inspect exact entity and ghost positions before each release; persist the opening geometry and power bridge in `evidence/opening-oil-intent.json` across restarts. Keep the complete future footprint reserved for reach planning. Existing missing-source, coverage, landfill, legality, funding and power checks still apply.
- Each candidate declares `scope_bounds` (action bounds plus 4 tiles) and carries its own power, so it receives no relay variants. The land check reads all phases. A candidate that overlaps a footprint Laya has already submitted is blocked as `already_submitted`. `reach` may cross ore, smelting and oil land like `expand`, and the mall stays reserved. Validation is the usual live check: `item_shortfall`/`missing` become stock needs, so roboports, poles, pumpjacks, refineries, chemical plants, pipes and other short items fund the mall. `pipe-to-ground`, `offshore-pump` and `substation` were added to the clean runner's exported recipes; only enabled recipes appear.
- **Facts and journal:** `oil_facts` on each option record (reach: target, `hops_left`, `distance_to_oil`, bill; oil: packets, bill, `cell_draw_kw` nominal estimate, `power_capacity_verified: false`, bridge). `menu["oil_options"]` (facts, offered scopes and hops, blocked reasons) is written to the decision record and to `observation_digest.oil_options`. A failed read is blocked as `oil_options_error`, never as a failed decision.

- **`coal-feed`** (November 7, 2026; unit tests with recorded planner outputs and the real mine classifier only; need: the `oil` need it unblocks, plastic first; `product: coal`): while `oil` is blocked `needs:coal_source`, the belt coal mine `ensure_coal_mine` would build is planned by the pure `plan_coal_mine_intent` (`orchestrator/stage_chemical.py`; surveys: one bulk ore-origin read, then bounded `clear`/`drill_ore` probes, at most 128 reads) on the nearest viable coal patch to the cell centre. It is reach-targeted until covered, then offered as one scoped ghost plan with a `coal_power_bridge` phase after its substation. It sits under the plastic need so the guided policy reads it as a goal build (`goal_need`); `goal_blocker` reads only `item_shortfall` reasons, so a `needs:` block never sets it. Blocks: `no_coal_patch`, `coal_plan_failed`, `coal_needs_landfill`, `coal_power_plan_failed`, `coal_unpowered`. It may stand on `ore:coal` land. `find_resource_mine` recognizes mines by layout (drills beside a belt row, ghosts included), so the submitted ghosts read as `coal_source_incomplete` and the built mine as the belt source that unblocks `oil`. Laya reads it with `include_unclassified=False`, so lone drill-to-chest `mine` drills no longer pass for an incomplete belt mine. The declared-scope rule is unchanged: a coal patch off the base-to-oil corridor and outside the outpost area is refused `scope_off_route` by the adapter.

Not verified live: survey cost and RCON command size for the cell plan of about 230 actions, multi-phase atomic submission through the layout executor, coverage at the coal-belt midpoint, and grid capacity for the roughly 2.6 MW cell.

## Geometry capacity lifecycle (2026-11-14)

Pending construction shortfalls remain production demand after their original
option leaves the menu. They expose recipe dependencies on the status board and
lift the relevant mall gates until the network can fund the unfinished bill.
Ordinary pipes used as ingredients and ordinary pipes needed by ghosts must not
lose their producers when an underground-pipe batch starts.

Reserve steel conversion land in patch and grid plans. Steel expands through
the existing electric-furnace cell operation, with ordinary coverage, funding,
power and collision validation.

The CLI injects `GeometryMaintenance` for receipt-owned geometry. It orders bot
recovery only after matching recorded unit identities: starter recovery requires
real replacement furnace crafts and items observed in that replacement's exact
provider; exhausted drill recovery preserves shared belts and poles. Orders and
delivery evidence persist in `evidence/geometry-maintenance.json`. Recovery
orders resume bots immediately rather than waiting with the simulation paused.

New line receipts record their shared-template origin. Additive shared refinery
extensions and drill infill on the connected collector reuse the existing haul
and provider. Destructive cutovers and unrecorded origins are not inferred.
Extensions retain a soft preference over independent lines; competing legal
sites remain available. A line under construction is reconciled before opening
another line for the same plate. If extension space, power, ore or legality is
unavailable, a separate line can still be offered.

Mining proposals prefer well-surrounded, resource-rich survey sites. Exact live
validation rejects an empty mining area or any mining area containing multiple
resources, using the engine's mining radius rather than the drill's footprint.

Evidence: focused regression tests plus read-only live validation. No production
improvement or acceptance milestone is established before a fresh frozen cohort.

## Ore-haul obstacle detours (2026-11-14)

Line generation adapts the shared `planners.belt_bridge.surface_belt_route` operation when a haul
collides with existing infrastructure. Full entity footprints and the proposed mine, furnace body
and output taps constrain the route. Both belt interface headings and the 90-tile haul limit remain
required. A collision in a machine body still requires another site; an impossible detour remains
blocked. All returned belt actions are ghosts, and the original land, coverage, power and live
placement/funding validators still run. No pole is removed or reconfigured by this operation.

## Owned line expansion — 2026-11-14

For six-furnace ore-fed lines, each six-furnace extension includes enough new
drills to bring the owned surviving drill count to the target furnace count.
Extend the paired collector away from its haul head, preserving the existing
haul. Every drill still passes exact mining-area, mixed-resource, collision,
land, power and coverage checks; reject incomplete supply packets. An older
twelve-furnace/eight-drill receipt gets a four-drill repair and terminal
collection before further furnace expansion. This is a construction contract;
counts alone do not establish measured throughput.

Collection follows the approved refinery's terminal interface. Shared
requester-fed electric steel lines grow along the existing belts, retaining
the requester. Once such a line exists, offer its extensions rather than
independent additional steel banks. Never guess ownership: reconstruct older
shared-line metadata only by matching the complete recorded deterministic plan.

Build nonconflicting additions and the new collection endpoint first. Persist
exact retirement references and remaining placements in the transaction. After
observing construction, recheck unit identities while paused and order shared
bot recovery of obsolete owned equipment. Leave passive providers until fewer
than five actual inventory slots remain occupied; unknown occupancy retains
them. Only after observing actual removal may Laya select the funded ghost
packet on the cleared tiles. Keep the product constructing through cutover,
including across restart. Repair/cutover packets may contain only infrastructure
and remain eligible after the product's phase target is satisfied.
