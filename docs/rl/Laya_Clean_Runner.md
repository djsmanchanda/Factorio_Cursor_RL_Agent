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
| `--actions geometry` (default), `training/laya_clean_runner.py` | Land plan, funded starter/mine/smelting/assembly alternatives, mall retargets, coverage and waits. | Bounded site/scale menus; general fluid, research, upgrade and repair choice coverage remains incomplete. Component fixtures are separate evidence. |
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

Oil facts (November 7, 2026; unit and Lua-fixture tests only): the saved-world survey exports enabled recipes with a fluid ingredient or product as typed `facts["fluid_recipes"]` (ingredients/products with type and amount, energy ticks, category, compatible machine speeds); `facts["recipes"]`, the solid build menu, is unchanged. The status board expands fluid goals through them (`production_recipes`): per product, the shortest chain to raw resources, ties to a recipe already running, then fewer ingredients. Plastic at 0.5/s reads as one chemical plant, 5 petroleum gas/s from one oil refinery (basic oil processing) and about 11 crude/s; crude oil and water are raw. A read-only `facts["oil_field"]` survey (512 tiles of generated terrain around the lowest roboport, else spawn) reports the nearest crude-oil patch (centre, distance, wells, total amount, yield, crude/s with one pumpjack per well) and the nearest water tile; its compact form is `observation_digest.oil_field`. No oil operation exists yet, so fluid needs stay in the menu's `blocked` as `no_candidate`. Fluid flow is not measured, so crude oil and petroleum gas never read as satisfied from counters. A candidate may declare its own `scope_bounds` (unit and Lua-fixture tests only); the Lua validator then checks that candidate's actions against it instead of the global ±128 bounds. The adapter refuses a declared scope before Lua (`scope_malformed`, `scope_unsurveyed`, `scope_excludes_actions`, `scope_too_wide`, `scope_out_of_reach`, `scope_off_route`) unless it tightly contains the candidate's actions, lies within the oil search radius of the survey anchor, and touches the surveyed oil-field area, the global bounds or a 64-tile corridor along the base-to-oil line (reach hops); the option's state `scope` shows it. Candidates without one are unchanged.

## Oil options: `reach` and `oil` families (November 7, 2026; unit tests with recorded planner outputs only)

`tools/laya_oil_options.py` adds two option families that follow the "Oil facts" survey above (`Settings.oil_options`, default on). `tools/run_laya_clean.py` injects it as `run(..., oil_options=...)`, because `training/` may not import `orchestrator.stage_*` (`training/isolation.py`). It is an adapter only: it reads the disposable world through the deterministic read-only functions and drives the pure planners `plan_reach_step`, `plan_power_bridge` (`orchestrator/stage_services.py`) and `plan_oil_cell_intent` (`orchestrator/stage_chemical.py`), answering each survey they name, then rerunning them. The families read nothing and the menu is unchanged unless `facts["oil_field"]` is found and plastic, petroleum gas or crude oil is an unmet board need.

- **`reach`** (need: the most upstream unmet item of crude oil, petroleum gas, plastic): while the oil-field centre, then any planned cell action, lies outside construction coverage (Euclidean distance from a powered port, within its construction radius), one candidate is the next `plan_reach_step` hop: its power bridge and roboport as ghosts. It avoids the planned cell footprint. Pending ghosts are blocked as `reach_in_flight`, missing ports as `reach_no_source`, and geometry failures as `reach_plan_failed`.
- **`oil`** (need: plastic first): once every cell action is covered, the opening plastic cell becomes one atomic plan. Its packets run in order as phases (`<packet>:<phase>`), with one `oil_power_bridge` phase from the first backbone substation inserted after the backbone. Direct placements become funded ghosts, as in the deterministic `_submit`. The family is blocked when no planner-managed belt coal mine exists (`needs:coal_source`) or the mine is incomplete (`coal_source_incomplete`). It is also blocked while the cell is uncovered (`needs:construction_coverage`) and when landfill or other non-ghost actions are needed (`needs_landfill`, `non_ghost_action`, `unsupported_action_field`). Siting and planning failures give `no_cell_site`, `no_plastic_site`, `no_shoreline` and `oil_plan_failed`, and an unpowered grid gives `oil_unpowered`. The cell intent is cached per run, keyed by oil well, cell site and coal source, for 36,000 ticks.
- Each candidate declares `scope_bounds` (action bounds plus 4 tiles) and carries its own power, so it receives no relay variants. The land check reads all phases. A candidate that overlaps a footprint Laya has already submitted is blocked as `already_submitted`. `reach` may cross ore, smelting and oil land like `expand`, and the mall stays reserved. Validation is the usual live check: `item_shortfall`/`missing` become stock needs, so roboports, poles, pumpjacks, refineries, chemical plants, pipes and other short items fund the mall. `pipe-to-ground`, `offshore-pump` and `substation` were added to the clean runner's exported recipes; only enabled recipes appear.
- **Facts and journal:** `oil_facts` on each option record (reach: target, `hops_left`, `distance_to_oil`, bill; oil: packets, bill, `cell_draw_kw` nominal estimate, `power_capacity_verified: false`, bridge). `menu["oil_options"]` (facts, offered scopes and hops, blocked reasons) is written to the decision record and to `observation_digest.oil_options`. A failed read is blocked as `oil_options_error`, never as a failed decision.

Known gap: Laya's `mine` family builds drill-to-provider coal mines, but the cell planner needs a belt source. Until a belt coal-mine operation exists, `oil` stays blocked by `needs:coal_source` in Laya-only worlds. Not verified live: survey cost and RCON command size for the cell plan of about 230 actions, multi-phase atomic submission through the layout executor, coverage at the coal-belt midpoint, and grid capacity for the roughly 2.6 MW cell.
