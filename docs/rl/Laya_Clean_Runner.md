<!-- Path: docs/rl/Laya_Clean_Runner.md | Purpose: Current capabilities, decision/evidence contracts and acceptance gaps for the separate disposable Laya runner. -->

# Laya runner contract

New ordinary cohort trials use the shared **7,200-second wall allowance**;
longer research windows remain explicit. See the
[allowance and safe activation contract](Laya_Host_Scheduler.md#trial-wall-allowance-2026-10-24).
Explicit existing freeze and coherent-recovery budgets remain unchanged unless
a supported safe transition records otherwise. The wall allowance does not
extend the game-tick window or cohort deadline.

Status: migration prototype, integrated October 26, 2026. Product intent and
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
| `--actions deterministic`, `training/laya_ops_runner.py` | Plate starter/foundation/extension/retirement, baseline mall item, shortage batch, steel fence/seed, guarded existing-cell stock-gate raises and waits. | `orchestrator/operation_session.py` still calls strategic builder wrappers that choose prerequisites, lender, site, scale, transport and recovery. Coverage can occur inside a request rather than as a separate offered choice. |

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
mission opener or create its priority scheduler. The existing one-chest plastic
output reserve remains a documented startup constraint; steel intent is selected explicitly.
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
Ops receipts distinguish producing output from prerequisite progress and
preserve mapping results; legacy retirement strings remain unverified.
Retirement receipts report `released` only for a structured released lifecycle;
other normal returns report `retirement_pending` with `ok=false`. They remain
retryable work, rather than completed retirement or a tool exception.
History/lifecycle features and error containment do not yet satisfy the
complete contract. See the [audit](Laya_System_Audit_2026-10-08.md)
for reproductions; documentation cleanup does not repair these implementation gaps.

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

Unknown evidence stays unknown. Ops exposes previously reported shortage bills,
active reservations and live loan identities. Each legal option now carries the
prospective bill it last reported (`bill`), whether that bill is known
(`bill_known`), and items missing at that attempt
(`last_attempt_available_items` and `last_attempt_missing_items`). Current
`available_items`, `missing_items` and `inputs_missing` are populated only when
the reported project, current network stock and active reservations establish
availability; otherwise they stay `None`. `reservation_owner`
names the reported claimant, and a changed owner claim clears an otherwise
unchanged material failure (`unchanged_failure`), including after JSON persistence.
`demand_point`/
`demand_distance` relay the shared `orchestrator/demand_siting` read (never
training-local geometry). Per option, `last_outcome` is one of `delivery`,
`constructing`, `no_geometry`, `material_failure`, `unchanged_material_failure`,
`failed` or `unknown`, and survives unrelated waits. The menu's `facts` block
adds `shortfall_feasible` (whether each short item can be made),
`measured_plate_net` and `measured_force_consumption` from the measurement
window. Missing per-item rates stay `None`; force consumption is not attributed
to mall consumers. `free_slots` is for the separately integrated mall slot-growth
work. Full prospective requester draws and measured displaced production remain
missing; scarcity features can remain false without bill evidence. Add those facts
before training on their values.

Ops exports episode-scoped bootstrap district lifecycle and submission facts
in the brief, menu features and recorded decision state. A submitted replacement
remains a `retire_starter` reconciliation alternative until its district is
released, including after satisfied rates, later proxy phases and shortage
returns. Local foundation receipts provide a fallback when no district fact
is available; a scoped district fact takes precedence. Submission does not
certify readiness. The existing tool still checks owned geometry, construction,
replacement output in its provider and bot deconstruction. New starter,
foundation and extension capacity remains demand-driven. The policy chooses
among these alternatives; pending lifecycle work has `goal_need=true` because
it serves mission completion. Catalog weights and exploration are unchanged.

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
main uses C1 `c1-v2`, which counts working, output-full or recently crafting mall
assemblers; it also exports retired `c1-v1` on the same sample for comparison.
C29–C37 used `c1-v1`; their counts are not direct `c1-v2` results. C2 requires
healthy productive foundations, delivered output and retired metal starters.
It also includes C5 and advanced-circuit consumption. Reports must name the
predicate version, starting state and sustain window, and separate production,
delivery and consumption. Do not compare proxy flags with deterministic
checkpoint flags as though they were identical.

Ops experiments can use `--until-accepted C2` to stop when that unchanged
shared predicate passes. This option is separate from `--until-phase C2`,
which stops at the rate proxy; the two cannot be combined. Explicit experiment
budgets still apply, and periodic samples do not prove an unsampled condition
was never met. Reports retain both proxy and accepted outcomes.

### Natural goal contract

The latest user goal is a **completed research technology** (`--research-goal`,
default `mining-productivity-4`). A goal run ends only on:

- a verified `target_completed` for the requested level from the live
  `research_status` payload (`training/laya_goal.py`), or
- a hard failure (three consecutive typed tool errors), or
- a **measured complete stall**: the relevant useful state is unchanged across
  the whole declared observation window (`--stall-observation-ticks`, default
  18000 game ticks).

Rate proxies are observations, never the goal: reaching the C6/S1 rate ladder,
a high science-pack rate, a running research or a *queued* target does not end a
goal run, and the 12-decision choice-count cutoff is not applied. Relevant
useful state is ghost/construction progress, delivery or crafting of required
inputs, research progress, funded plans and loans -- not arbitrary surplus
output. Runs expose `goal` evidence and the `useful-state-window-v1` stall
version/counters in the summary. A natural campaign episode sets no total
game/wall cap and no 6h wall assumption by default; those remain explicit
`--until-accepted`/`--max-game-ticks`/`--run-wall-seconds` experiment options.
The 8h campaign deadline only prohibits *new* episodes and fixes; an active
natural run and its terminal evidence may finish beyond it.

The ops runner saves versioned acceptance samples under
`evidence/acceptance/<run-id>/` at startup, proxy transitions, before and after
retirement attempts, every ten decisions and termination. Each sample retains predicate versions, individual
true/false/unknown checks, raw observed machine status/position/counters,
starter evidence, the survey's start/end ticks, scope, mall targets and loan/
reservation observations. Run summaries index the latest checks and sample
path. Current checks remain separate from monotonic reached status: healthy
stocked cells can idle after an earlier pass. These diagnostics preserve the
existing pass criteria and policy weights; they do not establish sustained
delivery or turn missing telemetry into failure. Machine surveys still cover
recognized baseline recipes/layouts, so inspect raw saved-world evidence when
their coverage is disputed. Detailed sampling adds read-only survey work in
live game time.
`machine_health` counters combine completed crafts scaled by 1000 with fractional
craft/mining progress; the packet declares this unit. They are not delivered
item counts or an unscaled production rate. Structured operation results,
including retirement lifecycle evidence, remain mappings in receipts.

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
Live execution is the default; `--decisions 0` removes the decision-count cap.
A natural goal run uses `--research-goal <technology>` and has no default stall,
game or wall limit beyond the measured-window stall. Ops tooling requires a live
clock because it waits on bots in wall time; geometry also has the paused
`--stepped` path.
Lifecycle and mod procedures remain in [Factorio operations](../factorio_operations.md).

Artifacts: `logs/laya-runner.log`, PID/heartbeat sidecars,
`evidence/clean-decisions.jsonl`, `evidence/decision-states.jsonl`,
`evidence/latest-status.json`, and geometry transaction receipts. The local run
index is `data/laya-saved-world/laya-runs.jsonl`; the shared ledger is maintained
by `tools/run_ledger.py`. The helper is optional (`--no-helper` disables it).
Read terminal evidence before a cached status board or experiment report.

### Helper evidence packet

`tools/laya_helper_agent.py` builds one bounded packet per run with exact
`run_id`/revision/root identity, the versioned acceptance status vector (per
checkpoint `predicate_version`, `current`, `latched`, `accepted`, `missing`),
retired-predicate comparisons such as `c1-v1` beside the active `c1-v2`, and
`wall_seconds` separated from `game_seconds`. `accepted` is the monotonic
predicate pass; it is not a rate proxy and must not be reported as one. The
prompt states these rules explicitly so the model cannot reinterpret an accepted
checkpoint as a proxy or read game time as wall time.

The caller may supply explicit comparator run records (the paired prior run,
including roots outside this repository's isolated index) through the packet API
or `--comparators <records.json>`; `previous_run` selects from those plus the
local index, and provided roots are listed verbatim so an archived deterministic
reference is never silently substituted for the actual prior pair. The helper
writes `helper-state.json` (`running`/`complete`/`failed`, with report,
comparators and error) under `~/.local/share/factorio-rl/laya_helper/<run-id>/`
and appends one completion or failure line to the runner log.

New disposable-world preparation optionally reads
`data/laya-saved-world/admission-control.json` (`laya-admission-v1`, integer
`limit` 0–40, and explicit experiment-directory `scopes`). A shared locked
FIFO counts live worlds and in-flight startup reservations. Lowering its cap
lets existing worlds finish and holds replacement preparations until capacity
is available. It does not signal a server, change policy choices, or extend
the campaign deadline. Record concurrency changes with the experiment evidence.

Geometry supports hash-verified earned-C1 export/resume with
`--export-checkpoint`/`--resume-bundle` and `training/laya_checkpoint.py`.
This is not general restart recovery: the ops runner does not restore its
history and deterministic ledgers through that geometry checkpoint contract.
The separate coherent ops-generation protocol described below restores both,
with exact code/mod/objective hashes and cumulative budgets.

Geometry defaults to a 12-decision stall limit; a non-goal ops run tracks
decision-idle game time (waits and time outside blocking operation execution)
plus a 12-decision bound on choices without genuine progress, resetting on
measured goal progress or a new funded plan committed through the shared
material ledger (a versioned `decision-idle-v2` contract). A **goal** run uses
the `useful-state-window-v1` contract instead: no choice-count cutoff, and a
stall only when the relevant useful state (ghosts, required-input crafting or
delivery, research progress, funded plans, loans) is unchanged over the entire
declared observation window. A pending status alone never resets it; an
attempted-but-unfunded ghost is not a reset. Independent total game
(`--max-game-ticks`) and wall caps are off by default for natural goal runs and
only apply when an experiment explicitly sets them. These signals can miss
useful work.
Unexpected caught tool exceptions and typed blocks are different: a reported
zero `failures` is not zero operational defects. Escaping errors can skip the
current run index. Complete-cohort crash/interruption accounting remains a
required harness fix before interpreting promotion comparisons.

Next reusable work: truthful receipts/economic facts, the partial-foundation
steel cycle, explicit loan alternatives, faithful measured labels and a
compatible learned policy; then missing goal actions and matched acceptance.
Coordinate shared mall/adapter files through their latest ownership handoff.

Campaign helpers accept explicit `--config`, `--report-root`, `--data-root`, and
`--comparators` JSON records, so findings and state stay outside frozen code.
Muse permissions allow file readers and deny all edits, shell, MCP and delegation.
It returns a complete Markdown report between FINDINGS_REPORT_BEGIN and
FINDINGS_REPORT_END; the wrapper validates and atomically publishes that response.
The wrapper also owns evidence packets and lifecycle records. A placeholder or
truncated response cannot establish observer completion.

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

## Current supply backs unfinished-loan credit (2026-10-23)

On observed final-step ingredient starvation, an unfinished finite loan clips
unsupported direct-input credit and replans its missing prerequisite. Backing is
allocatable transferable stock plus its own requester, machine, feeding hands
and assigned incoming deliveries. Other consumers' WIP is excluded. A fed next
craft or assigned cargo preserves the batch; missing telemetry leaves credit
unknown and unchanged. Reduced credit is persisted in the existing loan tag by
the normal configuration transaction. Finished-batch credit spending remains a
separate rule. The incoming probe uses the engine's
[logistic-point delivery API](https://lua-api.factorio.com/2.1.23/classes/LuaLogisticPoint.html#targeted_items_deliver).

## Research objective and recovery contract (2026-10-23)

`--actions deterministic --research-target mining-productivity-4` selects
`laya-research-consumption-v1`. Existing frozen cohorts and the default legacy
science-readiness ladder retain their conditions; their science-only
`mission_complete` is not a research completion. The new objective stops as
`research_complete` only on fresh requested/canonical level evidence after an
incomplete start, with newly manufactured and lab-consumed packs and measured
research progress. Already-complete starts remain ineligible. Wrong-force,
stale, regressing or incomplete observations cannot earn success.

S1 remains science readiness; R1 is research completion. Pack requirements and
cost come from the force, including repeatable technology aliases. Selection is
an explicit operation with expected-current/queue guards, declared replacement
and readback. Repeated selection of an already queued/current target is
idempotent. No research cascade or research/item grant is available as a policy
operation. Separate operations construct funded bot-built labs, configure their
requesters after construction, connect normal power/logistics and request
production capacity from the shared deterministic line operation. Sandbox lab
scaffolding is omitted. Missing bills stay normal production needs.

Science demand starts at **0.25** ingredient sets per game second and doubles
through 0.5, 1, 2 and onward. Pack rates use the live ingredient coefficients;
the status board propagates recipe demand upstream. A rung requires 18,000
observed ticks of manufacture, delivery into labs, lab consumption and useful
research units at its target, with essential service, supporting stock and lab
buffers maintained and no new tool/safety fault. Alternate pack consumers make
attribution unknown. Target increases are never progress. The achieved rate,
current demand, windows and prior rungs are separate persisted facts.
`--fixed-science-target` freezes the initial 0.25 target for a matched contrast.
Lab capacity uses live research energy, normal lab speed and force speed bonus;
measured consumption, rather than theoretical capacity, controls advancement.
Research progress handles repeatable-level resets. The research objective also
bounds craft progress by owned finite material/batch obligations; background
production cannot perpetually credit the same bill. Summaries identify the last
progress reset and current shortages. Legacy stall behavior is unchanged.

For coherent ops generations, supply finite `--max-game-ticks` and
`--max-wall-seconds`, plus `--ops-checkpoint-dir <repository-data-path>`.
`--ops-checkpoint-ticks` defaults to 54,000. Captures occur at complete operation
boundaries and final shutdown, using a paused unique save, completion barrier,
authoritative sidecars, controller/rate/progress/research/evaluator/C6 state,
policy RNG and logical identity. Atomic manifests bind source/schema/catalog,
engine, mods, tick and every copied file. Prior complete generations remain
available. Geometry earned-C1 bundles and `--jumpstart` are separate protocols.

Prepare a fresh owned root with `--ops-resume-bundle <generation>` and the same
private mod/runtime/port configuration, then run it with deterministic actions
and the same bundle. The runner restores the frozen policy, seed, decision
limit, objective and cumulative budgets; preparation/capture overhead consumes
remaining active-wall allowance. Queued downtime is excluded. It verifies the
actual restored world and external state before any operation. Incompatible,
tampered or incomplete state is an infrastructure failure; it cannot silently
become a fresh attempt. Code-changing continuation requires a separately
versioned explicit migration and is currently rejected. A coherent mechanics
fixture is not an earned frontier: corpus eligibility and lineage clustering
must still be preregistered independently.

New batch arms may opt into `ops_checkpoints: true`. Their queue recovery uses
the newest complete generation, retains the interrupted attempt and caps the
remaining wall budget. Missing generations are explicitly
`incompatible_checkpoint`. Existing/default `--resume` remains fresh-world
queue rescheduling. All planned jobs, cuts, attempts and unknown outcomes stay
in the denominator; conditional continuations and fresh starts are reported
separately. No repository change mutates active frozen campaigns or grants
real-base authority.

## Optional mall reconciliation and wait pacing

The [October 23 candidate contract](Laya_Mall_Utilization_2026-10-23.md) adds
opt-in `mall_loan_reconciliation` and `decision_wait_schedule` settings for
deterministic actions. A selected reconciliation re-observes one completed
prerequisite/pending release and uses the existing shared loan transaction;
its receipt proves configuration change, not delivered output. It skips
unselected post-operation mall upkeep.

The user-selected timing candidate is linear: divide selected waits by
`min(4, 1 + .03 * elapsed_game_minutes)`, rounded up with a 60-tick floor.
Defaults remain off; ordinary operations have no added loop delay. Exact ops
recovery retains the original game clock and settings. Decision journals retain
nominal/effective waits and typed reconciliation receipts; current status and
summary expose actual decision rates and an instantaneous assembler status
sample with explicit known denominators. These are separate experimental factors,
not adopted defaults or measured time utilization.

Restored evaluation controls: `policy.epsilon=0` removes only the uniform
exploration component; choices remain seeded weighted samples, not argmax.
The default epsilon remains 0.15. A geometry refusal's `unchanged_failure`
fact persists across waits and idempotent queued requests, and clears after
measured layout change or an operation that actually changes the layout.
