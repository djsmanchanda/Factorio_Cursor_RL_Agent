<!-- Path: docs/rl/Laya_Clean_Runner.md | Purpose: Current capabilities, decision/evidence contracts and acceptance gaps for the separate disposable Laya runner. -->

# Laya runner contract

Status: migration prototype, updated October 9, 2026. Product intent and
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
Ops receipts distinguish producing output from prerequisite progress and
preserve mapping results; legacy retirement strings remain unverified.
Retirement receipts report `released` only for a structured released lifecycle;
other normal returns report `retirement_pending` with `ok=false`. They remain
retryable work, rather than completed retirement or a tool exception.
History/lifecycle features and error containment do not yet satisfy the
complete contract. See the [audit](Laya_System_Audit_2026-10-08.md)
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

Unknown evidence stays unknown. Ops exposes previously reported shortage bills,
active reservations and live loan identities. Each legal option now carries the
prospective bill it last reported (`bill`), whether that bill is known
(`bill_known`), and items missing at that attempt
(`last_attempt_available_items` and `last_attempt_missing_items`). Current
`available_items`, `missing_items` and `inputs_missing` are populated only when
the reported project, current network stock and active reservations establish
availability; otherwise they stay `None`. `reservation_owner`
names the reported claimant, and a changed owner claim clears an otherwise
unchanged material failure (`unchanged_failure`), including after JSON persistence. A `no_geometry` refusal stays unchanged until a later receipt changes the layout (a submitted plan, `progress=true`, or a successful starter/foundation/extend/steel/retire operation) or the built machine/pole count differs from the snapshot taken at the refusal. A queued or idempotent request, such as research, does not reset it.
`demand_point`/
`demand_distance` relay the shared `orchestrator/demand_siting` read (never
training-local geometry). Per option, `last_outcome` is one of `delivery`,
`constructing`, `no_geometry`, `material_failure`, `unchanged_material_failure`,
`failed` or `unknown`, and survives unrelated waits. Zero throughput from full
output is not scarcity. Most machines `full_output` with accessible (unreserved)
network stock covering 120 s of demand reads as `output_backed_up`; without that
stock it reads as `output_blocked` (undelivered). Neither condition offers new
plate capacity. The menu's `facts` block
adds `shortfall_feasible` (whether each short item can be made),
`measured_plate_net` and `measured_force_consumption` from the measurement
window. Missing per-item rates stay `None`; force consumption is not attributed
to mall consumers. `free_slots` is for the separately integrated mall slot-growth
work. Full prospective requester draws and measured displaced production remain
missing; scarcity features can remain false without bill evidence. Add those facts
before training on their values.

`disabled_by_control_behavior` is reported as `control_disabled`, preserving
the measured rate. It does not prove either ingredient scarcity or satisfied
throughput. On every decision, including waits, ops reconciles shared standing
gates after completed loans restore. Current phase target products run ungated
while their rates are measured, as specified by `mission.stock-gates`; on phase
change they rejoin demand-derived caps. Borrowed cells retain their finite
loan gates. This configuration never substitutes stock for measured output.

Ops exports episode-scoped bootstrap district lifecycle and submission facts
in the brief, menu features and recorded decision state. A submitted replacement
remains a `retire_starter` reconciliation alternative until its district is
released, including after satisfied rates, later proxy phases and shortage
returns. Local foundation receipts provide a fallback when no district fact
is available; a scoped district fact takes precedence. Submission does not
certify readiness. The existing tool still checks owned geometry, construction,
owned replacement craft output and bot deconstruction. Every operation
pass also applies that same evidence, so a producing replacement's starter
machinery retires even when `retire_starter` is not chosen, and a retained
starter provider is reclaimed once empty ([planning](../deterministic/planning.md)). New starter,
foundation and extension capacity remains demand-driven. The policy chooses
among these alternatives; pending lifecycle work has `goal_need=true` because
it serves mission completion. Catalog weights and exploration are unchanged.

`replacement_planned` distinguishes a stored foundation from a submitted one.
A provisioning district with a planned, unsubmitted replacement keeps its
`foundation` continuation even when starter rates are satisfied or its coverage
ghosts have finished. That transaction retains bootstrap construction admission
until submission; afterward the ordinary retirement reconciliation applies.

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
Lab production and lab mall-slot options are absent until both measured C4 and
C6 are retained. This scenario admission applies before shortage ranking and
to final options; recipe availability or existing stock does not satisfy it.
Shared standing mall limits never decrease and grow by at least 20% when
increased; exact finite loan budgets remain separate.

These proxies do not establish `orchestrator/checkpoint_milestones.py` acceptance:
its C1 requires healthy starters and four working mall assemblers; C2 requires
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
by `tools/run_ledger.py`. The default helper is read-only OpenCode Go DeepSeek
V4.1 Flash/high, started at RUN START and continued through final review in one
saved session per run. `--no-helper` explicitly disables it. OC persistent runs
enable it with an independent user service and mutable evidence paths outside
frozen code.
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
Helper permissions allow file readers and deny all edits, shell, MCP and delegation.
It returns a complete Markdown report between FINDINGS_REPORT_BEGIN and
FINDINGS_REPORT_END; the wrapper validates and atomically publishes that response.
The wrapper also owns evidence packets and lifecycle records. A placeholder or
truncated response cannot establish observer completion.

## Pending bootstrap construction and shared mall queue

Ops keeps the deterministic runtime's persistent priority queue, surveys it
before decisions and services completed recipe loans. Current queue targets
replace historical shortage receipts for production admission; operations
receive absolute targets, not deficit counts. Pending material work and its
recipe prerequisites carry explicit priority facts into the quoted doc rules.
Seeded exploration is retained. Bootstrap completes the active owned plan before
admitting another plate construction or unrelated chemical/lab work; its
material suppliers, needed chemical precursors and retirement/reconciliation
operations remain available.
All real placements must exist before that construction fact clears, even if
first delivered output has already permitted starter retirement. Steel site
refusals are geometry evidence: unchanged layout does not imply bots progressing.

Plate stock shortages use the starter/foundation/extension operation family;
they do not create assembler mall-slot or generic produce options. Calling the
cached plate producer repeatedly cannot add capacity.

Research queue membership is a live option fact. A target already current or
queued is not offered for re-queueing, including its canonical ranged
technology name; removal from the queue restores the choice. Queue membership
never proves completed research.

Only current phase products below their measured rate goal run ungated. A met
peer rejoins its standing limit while the phase's remaining products advance;
falling below the goal reopens measured production. This prevents an unfinished
stone goal from leaving gear/cable output unbounded throughout construction.

## Ops queue contract (October 12, 2026)

The deterministic action path now uses a durable queue. Laya selects enqueue
and reorder choices from the legal operation menu; wait choices continue the
executor. Each pass advances several ready jobs, services physical loan and
construction continuations, then observes again after 120 game ticks. Shared
operations run cooperatively, returning a typed pending state after one survey
or remedy instead of holding the controller through a long build window.

Queue state is stored under the world's `server/logs/laya-work-queue.json`
([schema](../../schemas/laya_work_queue.schema.json)). The journal records each
executor receipt separately from the strategic choice; status and summary expose
job priority, state, dependencies, delivered credit, reservations and blocker.
World reset archives the ledger; runner restart retains it.

A finite production job completes from usable stock plus its owned dispersed
output, never global production counters. Still-held output is not also delivery
credit. Four delivered drills of a six-drill bill leave two. Meeting an assembler
stock bill of29 with31 usable items completes it without another production call.
Optional spare output yields when more urgent committed work remains. Standing
producers retain their monotonic limits and measured phase gates.

Distinct unmet committed product jobs can request another standing mall lane.
This operation bypasses rotational-batch routing while keeping the shared funded
bill, slot cap and geometry validators. Future chemistry forecasts cannot hoard
chests away from current executable work. Unsupported supply and dependency cycles
remain visible blockers; they do not license manual placement or item grants.

Preemption verifies a fully funded urgent input bill and atomically checks the
exact loan machine's recipe, force and zero craft progress before holding it.
Its completed output and input claims persist while the lender is restored. An
in-flight craft is left running until an observed boundary. A restarted controller
releases only its recorded machine holds before resuming queue reconciliation.

Live smoke tightened continuation semantics: ready work precedes deferred
polling, and deferred peers poll by oldest last attempt before priority.
`infrastructure_construction_wait` retains exact pending entities and parks
that job until a batched survey confirms them built. Cooperative mall creation
persists the selected cell half and submits its funded ghosts before power
work can yield; restart/retry uses that same allocation.

A stock gate can freeze a partial craft. Boundary preparation now parks the
exact feeding inserters, returns unused real machine/held inputs to the cell's
requester, and temporarily disconnects that assembler's stock gate. The in-flight
craft completes with no next input; only then may configuration hold/change it.
Feeder flags and the previous gate connection live in the world-scoped runtime
checkpoint and restore after the transaction. This does not change standing
numeric limits or manufacture an item/progress credit.

Feeder-drain cleanup applies to same-recipe configuration as well as a changed
recipe. Maintenance restores drain records whose borrower has released or
moved to another recipe, so an abandoned pause cannot starve a standing source.

Built unconfigured mall cells belong to the executor's ready-work pool. It
assigns an unclaimed, recipe-capable lane to a funded queued item or its
missing producible ancestor, preserving another job's allocation. Installed
continuations retain their exact geometry, resume at the slot cap, and reserve
only missing construction plus startup inputs. They do not re-enter the
borrower search or pay for an already-installed assembler a second time.

The user's supplied four-slot cross blueprint is now the shared district
geometry beyond the opening bank. Up/down slots remain executor-assignable
throughout expansion; long-handed support shortages enter the precursor queue.

A fresh measured shortage from the same consumer reopens its fulfilled supply
job for the remaining bill while retaining earlier owned delivery credit. Toolkit
numeric `required` and `required_stock` both carry the real precursor quantity.
Completing a supply dependency wakes the consumer even when its output has
already been installed and transferable stock stayed zero. An unmet finite batch
can assign a funded empty lane as parallel `mall_capacity` even if another
producer or lender currently runs that recipe. The queued ops menu keeps other
starter goals selectable while an owned foundation waits; execution reservations
still decide which construction can actually proceed.

Committed plate stock deficits expose the appropriate starter/foundation/extension
option even when the phase's small measured rate target is already met. The
option carries the exact stock shortfall and goal urgency; full/backpressured
output still prevents unnecessary capacity. Raw plates remain in their shared
mining/smelting operation family, and Laya retains queue-order authority.

On restart, option compilation includes the same world's verified completed
construction receipts from the durable queue. It does not count an unfinished
job or carry prior decision choices/checkpoint claims into the new run. Active
measured queue material bills augment the legacy mall target map, so raw plate
shortages cannot disappear from the decision layer merely because the mall
omits raw products. Resolved/completed bills leave that target map again.

An exact ready reservation project can dispatch its owning queue job before a
shared aggregate precursor target is complete. This avoids waiting for eight
support inserters when a cell already owns its required two. Funding does not
bypass pending-entity waits, geometry blockers, or shared execution validators.

Mall preflight persists and claims a selected half before any ghosts exist.
All empty halves honor peer claims. Vertical cells submit under the same
`paired_mall_<item>` reservation as preflight. On resumption, unsubmitted
duplicate vertical reservation aliases are retired, and conflicting saved
claims are replanned only when no physical machine or ghost occupies the half.

The approved goal queue enables automatic mall growth in its execution scope.
The deterministic eight-slot opening preference does not constrain this queue;
actual material funding, protected reservations, coverage, recipe capability
and the shared lattice geometry still validate every added cell. Capacity
facts report this authorization consistently, including automatic backlog jobs.

Once observation verifies a job's pending bot-built entities, its continuation
can finish without refilling the consumed kit's shared supply target. The
checkpoint survives resumption; new shortages and geometry blockers still wait.
Pending proof may be recovered from the same world's exact construction receipt.

Mall bring-up covers both logistic chests and gated assembler positions.
An existing configured producer is dispatchable independently of obsolete cell
kit dependencies, without claiming its finite batch is complete. Shared repair
checks mall-machine logistic coverage before classifying a disabled gate as a
healthy demand wait, so a covered requester cannot mask a disconnected machine.

Shared provider lookup recognizes up/down slots through the declared cross
layout offsets, so their existing-cell maintenance uses the same ownership and
coverage path as left/right cells.
