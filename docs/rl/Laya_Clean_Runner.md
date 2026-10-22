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
| `--actions deterministic`, `training/laya_ops_runner.py` | Plate starter/foundation/extension/retirement, baseline mall item, shortage batch, steel fence/seed, guarded existing-cell stock-gate raises and waits. | `training/laya_ops.py` still calls strategic builder wrappers that choose prerequisites, lender, site, scale, transport and recovery. Coverage can occur inside a request rather than as a separate offered choice. |

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

Ops session startup clears run-local caches, loads live recipe/prototype facts,
validates the goal and restores persisted work. It does not run the deterministic
mission opener, create its priority scheduler or allocate new mission reserves.
The selected `reserve_steel_seed` action explicitly requests steel intent and
reserves the planner-derived pole bill in the material ledger. Reopening a
session preserves existing reservations; this is not general ops history recovery.
The action refreshes allocations against current stock before reporting
`reserved`; an underfunded bill returns `material_shortage` and remains retryable.
A seed already planned, committed to construction or producing reports
`seed_committed` or `seed_producing` instead of re-running the two-pole fence:
the seed's own plan bill (one pole for the belt-side seed) stays in force.

The required result contract distinguishes submitted, constructing,
prerequisite advanced, batch active, output delivered, producer ready and failed.
Receipts must retain transaction identity, selected parameters, typed cause,
ownership, resource use and observed postconditions. A successful return is not
by itself proof of delivered output or a standing producer.

Shared service failures now distinguish `infrastructure_collision` (plan and
observed occupant evidence) from `power_route_unavailable` (endpoints, pole,
blocked tile count and route reason). Both remain failed bug outcomes; typing
them does not establish a viable route or resolve the underlying placement.

[Stock-gate raises](Laya_Stock_Gate_Control.md) are explicit, bill-backed
configuration alternatives on observed owned standing cells. They require the
deployed executor's configuration-precondition capability, reject stale entity
facts before writing, and record submission separately from verified readback.
They do not create cells, retarget recipes, or prove delivered output. Activating
this operation requires deliberate mod deployment and Factorio reload followed
by a fresh runner; old executors do not offer it.

Current geometry transactions preserve submitted plans and pre/post facts.
On the October 17 reset branch, ops receipts distinguish a `None` production
return (`prerequisite_advanced`) from a standing producer. The selected opening-oil
transaction also carries its immutable identity and current remaining construction
bill into decisions and before/after evidence. These changes do not establish the
complete fulfillment or finite-cell release contract. See the
[reset integration record](Laya_Reset_Integration_Decision_2026-10-17.md) for tested scope.

## Decision facts and rules

The target compact projection keeps facts needed to discriminate available choices:

| Keep in the decision | Preserve outside the decision |
| --- | --- |
| Objective, current gap and measurement window | Full mission chronology and previous observations |
| Accessible stock, reservations by owner and requester WIP | Complete inventory and material ledgers |
| Active transaction, remaining bill, blocking input and next event | Full plans, receipts and execution reports |
| Option cost, feasibility and production displaced by a loan | Detailed surveys and alternative geometry |
| Recent relevant outcomes and facts changed since failure | Full attempt history, rationale and rule-to-fact mappings |

Unknown evidence stays unknown. Ops carries reported shortage bills and, on the
reset branch, the selected opening-oil manifest's remaining bill, exact installation
credits, foreign claims and missing items. Named prerequisites and selected bill
items remain choices outside the displayed phase. Every legal reported shortage,
including a small mall-cell construction blocker, remains selectable beside larger
unrelated bills; shortage counts do not truncate the menu.
Recipe/counter queries follow mission and observed demand. Requester draws,
complete lender consequences and source-network-aware allocation remain incomplete;
surface-wide transferable stock does not prove logistic reachability.

As of October 18, a failed mall-slot claim retained by a borrow deferral carries
its material bill, owner, allocatable stock, network stock and reservation
owners in `details.slot_claim`. These bills feed existing produce demand with
a network target fixed at the failed claim. A claim whose material stock has
not increased also sets the existing `unchanged_failure` option fact; a bare
handoff without a material snapshot does not establish that fact. This covers that failure path;
other requester bills and lender consequences remain incomplete.

The [mall economics projection](Laya_Mall_Economics.md) now records slot
headroom, reservation owners, network plate stock and measured loan craft
progress in the decision digest and bounded brief. These are independent
observations; free slots do not establish affordability or borrowability.

The [fluid observation contract](Laya_Fluid_Observation.md) adds typed recipe
observations alongside the solid-only operation catalog. The v2 board separates
unknown, disabled and unsupported recipes, shows fluid prerequisites as leaves,
and estimates supported chemical-item capacity from observed prototype speeds.
Fluid routing and delivered throughput remain unknown; these facts grant no
new fluid construction operation.

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

Ops summaries retain `accepted` as the historical union of predicates that
passed, also named `accepted_ever`. `accepted_final` lists predicates passing
at the final observation; it is null (logged as `unknown`) when observation
fails or is unavailable. Full final statuses remain in `acceptance` and the
journal. Batch reports disclose the final-observation denominator; old logs
have unknown final acceptance. A final pass alone does not establish sustain.

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

## First advanced-circuit bill (2026-10-20)

Before a standing advanced-circuit producer exists, the shared deterministic
operation walks the oil/plastic ladder, then borrows an existing owned paired
mall cell for the requested circuit stock target. It reuses the cell's chests,
sets no spare reserve, and restores the original recipe after transferable
circuits satisfy the bill. A loan cannot take the last producer of any
transitive ingredient. Craft counters alone do not prove seed delivery, and a
working temporary seed does not unlock permanent-mall capability. Logistic
chest preparation accepts delivered circuit stock and requests the ingredient
bill for its current chest demand, breaking the chest/circuit construction
cycle without claiming a standing circuit producer.

This remains behind the documented shared-wrapper migration boundary. It adds
no Laya-specific layout or preference, and does not establish C6 acceptance: the
real downstream consumer must still craft.

## Laya C6 output achievement (2026-10-21)

Ops C6 uses [explicit mall choice followed by measured circuit output](Laya_C6_Choice_Output_2026-10-21.md), version `laya-c6-choice-output-v2`. It requires no consumer or sustained rate. Its journal/status receipt is separate from unchanged deterministic C6 acceptance; legacy cohort counts are not comparable. Fresh frozen runners pick up this Python-only change.

## Shared operation boundary and service observations (2026-10-22)

`orchestrator/operation_session.py` owns the explicit selected-operation API,
ledgers and typed receipts. `training/laya_ops.py` is its public compatibility
facade; training no longer imports the strategic builder directly. The session
initializes operation facts and executes the selected map entry; it never calls
the deterministic mission opener or run loop. Inherited choosing wrappers remain
the documented migration debt, not newly transferred policy authority. The pure
economics projection lives in `orchestrator/operation_observation.py`, so the
shared session has no dependency back into training.

`item-service-observation-v1` records network stock, scoped production and
consumption deltas, rates and the observation window beside acceptance records.
A `stocked_plateau` means measured stock with no observed production or
consumption in that interval. It grants no sustained-service acceptance; missing
counters, changed scope/mods or regressing time/counters remain unknown. Existing
C5 and both C6 predicates are unchanged. The controlled plateau/renewed-demand
regression proves observation semantics, not a live plastic throughput result.

Active finite loans now check one craft's physical inputs and their own requester,
assembler and inserter WIP before surrendering their cell for a missing rotating
prerequisite. An insufficient whole remaining bill alone does not trigger a
restore while the cell can run. Exhausted prerequisites can still hand off using
the existing executor; historical credits remain governed by the C18 accounting
fix. This needs fresh frozen runners for live verification.
