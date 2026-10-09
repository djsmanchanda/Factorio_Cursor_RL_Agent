<!-- Path: docs/rl/Laya_System_Audit_2026-10-08.md | Purpose: Evidence-backed audit of the Laya ops controller, documentation conflicts and performance priorities; no runtime changes. -->

# Laya system audit — October 8, 2026

The active Laya ops controller is a useful experiment, but it is not yet a learned independent director. Its main obstacles are missing decision facts, ambiguous operation results, inherited strategic choices and bootstrap dependency cycles. More prompt text or more weight sweeps will not resolve those obstacles by themselves.

This is an audit and recommendation, not a change to product intent or an implementation. Existing dirty files, handoffs, services, saves and installed mods were preserved. No policy was adopted, model fitted, episode launched, service restarted or live world mutated.

## Scope and verification

- Reviewed the last **200 dated status updates**, counting both dated headings and standalone dated entries. This spans `CURRENT_STATUS.md:3003` through the final October 7 23:55 entry at line 3609. Also read nearby earlier entries to resolve historical dependencies. Counting headings alone would give a different, much larger historical window.
- **82/200 updates are generic repeated campaign records** saying `Prediction: sustained plastic. Reason: fix.` These are not 82 independently substantiated improvements. The log mixes durable milestones, duplicate campaign/test records and dated bullets; raw update counts are a poor progress metric.
- Read the October 8 handoff, architecture/isolation contracts, director/coexistence plans, clean-runner and strategy docs, guideline catalogs, variant/adoption docs, earlier relevant handoffs, land model, C2 audit and training reports.
- Traced the current ops runner, deterministic adapter, weighted policy, experiment/indexing path, milestone definitions and relevant loan/steel machinery. Six central files checked against the active frozen copy were byte-identical: ops runner, ops adapter, guided policy, experiment CLI, clean CLI and ops catalog.
- Reanalysed the completed **312-run history campaign** and its **24,605 decision records**. The mf30 arm alone has 4,075 decisions and 24,963 offered-option records.
- Checked the live user-systemd unit read-only: active/running, start October 7 23:52:39 IST, main PID 2524758, hard runtime cap 8h05m. Inspected active campaign/runner identities and advancing evidence files. No new RCON commands were needed.
- Snapshot at **October 8 00:27:21 IST**: 38 indexed completions from the active loan campaign; table below. Results remain interim. Its cached `report.json` still described the first 20 completions, so this audit reads the run index directly.
- **32 existing focused tests passed**: `test_laya_ops_runner.py`, `test_laya_guided_policy.py`, `test_laya_variants.py`, `test_laya_knowledge.py`. Offline probes reproduced null-result success, successful-result failure counting, short measurement windows and a wait-only plastic menu. No new live replay or GPU evaluation was performed.
- All quoted passages in the three catalogs currently exist in their sources: 8 general, 48 mission and 30 ops rules. Quote integrity passes; semantic consistency does not follow from it.

## What the experiments actually establish

| Completed history arm | Runs | Laya phase C1 | Laya phase C2 | Stop outcomes |
| --- | ---: | ---: | ---: | --- |
| Old baseline | 52 | 2 | 0 | 51 stalled, 1 window timeout |
| Mall-first-30 | 52 | 20 | 0 | 52 stalled |
| + follow-through | 52 | 19 | 0 | 50 stalled, 2 timeouts |
| + no-repeat | 52 | 20 | 0 | 50 stalled, 2 timeouts |
| + unstick | 52 | 22 | 0 | 52 stalled |
| + all history | 52 | 24 | 0 | 48 stalled, 4 timeouts |

Matched C1 discordances: mf30 beats the old baseline on 19 seeds and loses on one (two-sided exact McNemar p≈0.000040). All-history beats mf30 on ten seeds and loses on six (p≈0.454). The first supports a large improvement to this C1 proxy on this copied world; the second does not establish a history gain. Neither establishes C2 success, held-out terrain generalization or deterministic parity. Multiple experimental comparisons were made; these p-values are descriptive, not a promotion gate.

| Active loan arm, snapshot only | Indexed runs | C1 | C2 |
| --- | ---: | ---: | ---: |
| New default | 8 | 4 | 0 |
| All history | 7 | 3 | 0 |
| No idle loans | 8 | 3 | 0 |
| Other slots ×1 | 8 | 0 | 0 |
| Both loan changes | 7 | 0 | 0 |

All 38 indexed runs stopped `stalled`; the runner's exception counter was zero. The 40 run output files checked contained no traceback or recorded wall-clock timeout at inspection. This supports **no recurrence of the brief crash in the observed interval**, not an eight-hour crash-free claim. Arms have unequal completed counts and two runs were not indexed yet; do not select a winner from this table.

Evidence: `data/laya-experiments/ops-history-20261007/code/data/laya-saved-world/laya-runs.jsonl`, its `exp-ops-history-c2-20261007/` journals, and corresponding files under `ops-loans-20261008b/code/`. Frozen revision is 19b0ef5 plus the recorded dirty tree, not a clean 19b0ef5 checkout.

## Implementation findings, in priority order

### 1. The loan A/B changes core-slot weights as well as other-slot weights

`laya_ops_guidelines.json` has two preferences under `mission.ops-mall-slots`: core products ×30, and **all** mall slots ×6. `GuidedPolicy.distribution()` multiplies every matching preference. Core products therefore receive **×180**, before other rules, while belts receive ×6.

The `ops-slots-others-1` patch changes the generic factor to ×1. It consequently changes core products to **×30**, a sixfold reduction, as well as belts to ×1. The combined loan variant does the same. Their descriptions say core slots stay ×30, but the comparison does not hold their effective prior constant. The poor interim C1 performance cannot be attributed solely to reducing idle belt loans.

Smallest correction for the next experiment: make product groups mutually exclusive, or preserve the current effective core multiplier while changing only non-core slots. Record the effective per-option weights, rather than naming only one factor. Preserve the running experiment as evidence of its actual intervention.

Sources: `training/laya_guided_policy.py:distribution`, `docs/rl/laya_ops_guidelines.json`, `docs/rl/laya_variants/ops-slots-others-1.json` and `ops-no-idle-loans-others-1.json`.

### 2. A second C2 blocker is a persisted steel/power dependency cycle

The current default seed 301 reaches the Laya C1 proxy and then exhibits this chain:

1. Iron foundation places mine ghosts, then power repair reports medium poles missing.
2. A pole batch needs steel (`steel-plate=40`, available and usable both zero).
3. Steel returns `steel_iron_retirement_wait`.
4. The final iron district is `provisioning`, with a planned replacement origin but **`replacement_submitted=false`**. The steel fence still owns two reserved medium poles.

`_build_compact_steel_seed()` already has a generic cycle-break path using verified pioneer output before retirement. It requires `replacement_submitted=true`. That flag is set after refinery submission in `_build_initial_plate_smelter()`, whereas mine power repair can fail earlier. Thus the existing escape is unreachable in this observed episode. The ops menu also offers explicit steel only after a foundation call succeeds.

This is a transaction/lifecycle problem, not proof that every steel stall has the same cause. Fix and replay the partial-submission boundary: represent planned, mine-submitted, refinery-submitted and producing states truthfully, and make the authorized funded steel seed available on its real prerequisites. Do not falsely mark the full refinery submitted or relax funding/ownership checks.

Evidence: `.../exp-ops-loans-c2-20261008b/baseline-s301/logs/laya-runner.log` at +229–240s; `server/logs/deterministic-bootstrap-districts/laya-baseline-s301/ef34c5f1e6d0be37.json`; material ledger `server/logs/deterministic-material-reservations/laya-baseline-s301.json`. Sources: `orchestrator/autonomous_builder.py:_build_compact_steel_seed`, `_build_initial_plate_smelter`, `build_mining_stage`; `training/laya_ops_runner.py:compile_options`.

### 3. The ops decision interface hides the economics needed to choose well

Ops options carry no construction bill, requester draws or chosen lender facts. The state explicitly supplies `saved_world.provider_stock={}`. Unlike the geometry runner, it has no reservation projection, slot permanence/goal-production facts or quantified cost of taking a producer away.

In **all 24,963 offered options** in the mf30 history arm, `scarce`, `starves_goal`, `inputs_missing`, `dips_goal_reserve`, `slot_permanent` and `slot_from_goal` were false. Those values represent missing inputs to the feature builder, not measured absence of scarcity. Several related strategy rules are also absent from the slim ops catalog. The numeric stock gathered by the runner is used for some eligibility/retry checks, but is not provided to these economic features.

Expose network-local accessible stock, reservations by owner, requester WIP, the remaining exact bill, current production/demand and each loan's displaced output. Keep unknown facts distinguishable from false. This is the most valuable observation improvement before fitting a policy.

Sources: `training/laya_ops_runner.py:compile_options` and `run`; `training/laya_guided_policy.py:option_features`.

### 4. Operation success does not mean the requested operation completed

`ensure_produced()` explicitly returns `None` after performing one missing prerequisite or structural step. `DeterministicSession.run()` treats every ordinary return, including `None`, as `ok=True`. The runner then treats that as a completed mall slot, stops offering it, and uses successful calls to establish plate/steel progress. The training record drops the actual returned `result` and its execution identity.

Related defect: successful session results have no `code="ok"`; `repeat_fails()` looks only at `code`, defaults missing code to `error`, and ignores `ok`. A successful result is therefore counted as one failed attempt by this function. `since_build()` also counts any successful non-wait call as a build, even a reservation or a null-return step.

Use distinct receipts for submitted, constructing, prerequisite advanced, batch active, output delivered, standing producer ready and failed. Completion must reference observed entities/output and the selected operation's postcondition. Base history and lifecycle eligibility on those receipts. The existing tests pass without enforcing this distinction.

Sources: `orchestrator/autonomous_builder.py:ensure_produced`; `training/laya_ops.py:DeterministicSession.run`; `training/laya_ops_runner.py:plate_stages`, `repeat_fails`, `since_build`; outcome projection in `run`.

### 5. The adapter retains substantial deterministic strategy

`training/laya_ops.py` says deterministic decisions are not reused. It nevertheless invokes `ensure_produced()`, `build_mining_stage()` and their choosing wrappers. They pick deepest prerequisites, lender, site, scale, transport, promotion/repair and fixed chemical progression. A `produce X` request can execute work for Y without a distinct Laya choice. The authority inventory already identifies these choices as policy to extract.

The ops menu itself imposes starter-before-foundation eligibility and an iron-foundation gate on steel. Some constraints reflect current ledger limitations; they still need explicit temporary migration contracts instead of being presented as universal Factorio legality. Laya currently chooses a broad request, with many important decisions made inside the tool.

Extract **preview → choose → execute exact transaction → reconcile** seams incrementally. Begin with loan lender/batch size and the first foundation/steel transaction. Expose lawful alternatives and consequences; keep the incumbent's policy available in its own runtime. This does not require rewriting the entire builder first.

Sources: `docs/rl/laya_decision_authority_inventory.md`; `training/laya_ops.py:_operations`; `orchestrator/autonomous_builder.py:ensure_produced`, `_borrow_free_mall_cell`, `_start_bootstrap_loan`.

### 6. The declared mission exceeds the available actions and terminal predicate

`compile_options()` offers plate lifecycle operations, four baseline mall recipes, steel, material-shortage batches and waits. It does not generally offer a goal-driven plastic/fluid, science, research, upgrade or repair action. A C4 plastic goal with the baseline slots completed and no retained material shortage produces **only `wait.300` and `wait.1800`** in an offline reproduction. Internal recursive tool work might incidentally construct a later ingredient; that is not a general executable goal interface.

The final `S1` phase checks science-pack production. Completing the phase list yields `mission_complete` without verifying research selection, lab delivery or completed `mining-productivity-4`. Explicit coverage exists in the session but is not generally offered by the ops compiler.

Publish a per-run capability matrix. Offer current goal requirements and their live prerequisites directly, not only errors from prior calls. Add a real research terminal predicate and expose unavailable later mechanics as capability gaps. Training cannot learn to select an absent action.

Sources: `training/laya_ops_runner.py:compile_options` and `run`; `training/laya_mission.py:PHASES`, `current_phase`, `phase_reached`; `training/laya_ops.py:_operations`.

### 7. Reported C milestones are proxies, not the deterministic predicates

The clean-runner doc requires the same milestone predicates. The implementation uses production-rate targets and a 95% satisfaction threshold. Its own mission module acknowledges that mapping to deterministic predicates is separate evidence.

- Laya C0 measures iron/copper rates; deterministic C0 checks base validity.
- Laya C1 measures gear/cable/brick rates; deterministic C1 requires all three healthy starters and at least four working mall assemblers including gear/cable.
- Laya C2 measures plate rates; deterministic C2 requires healthy productive six-furnace foundations, delivered output and retired iron/copper starters.
- Laya skips C5, checks advanced-circuit production rather than consumption for C6, and has no final research check.

`rolling_measurement()` can accept a span shorter than the requested window as soon as two samples exist (60 ticks reproduced for a requested 7,200). Previously reached phases are retained rather than continuously revalidated. A later loss of circuits or earlier service need not revoke progress.

Keep cheap rate proxies for exploration, name/version them distinctly, and run deterministic predicates plus required simultaneous sustain for acceptance. Preserve counter production, delivered output and consumption as separate measurements. Historical C2 guided geometry results and today's ops results are also different action/runtime cohorts.

Sources: `training/laya_mission.py`; `training/laya_factory_status.py:goal_met`; `training/laya_clean_runner.py:rolling_measurement`; `orchestrator/checkpoint_milestones.py:_c0` through `_c6`; `docs/rl/Laya_Clean_Runner.md:Acceptance`.

### 8. Failure reporting has survivorship and classification gaps

`tools/run_laya_clean.py` indexes after a `try/finally`, not in an error-recording finalization path. An escaping exception closes the adapter and logs RUN END, but skips the index. `tools/laya_experiment.py:_job` ignores the run subprocess's return code; its report only scores indexed runs. A crashed arm can consequently disappear from its denominator. The handoff correctly excludes the aborted launch, but the harness has not repaired this general problem.

The reported `failures` counts caught unexpected tool exceptions. Typed `WorkStateSignal` results do not increment it. The history campaign says zero failures while recording **863 `untyped_stuck` outcomes** across arms (93 in mf30), plus many typed blocks and deferrals. These are not 863 independent crashes or safety violations; zero in that column is nevertheless not evidence of zero operational faults. Classification is returned by the session but discarded from the training outcome.

Register every planned job before launch and finalize it exactly once as success, policy stall/timeout, typed tool defect, crash, interruption or preparation failure. Preserve exit status and classification. Compare complete matched cohorts and report unresolved jobs explicitly. The never-crash contract also requires containment around observation, policy and projection failures, which currently sit outside the tool-call catch.

Sources: `tools/run_laya_clean.py:main`; `tools/laya_experiment.py:_job`, `report`, `score`; `training/laya_ops_runner.py:run`; `training/laya_ops.py:run`.

### 9. The history/stall signals do not fully represent current work

Mf30 selected an option with at least two same-code prior failures on **1,826/4,075 decisions (44.8%)** in the completed history campaign. The handoff's 60% refers to an earlier cohort; it should not be generalized to this one. History preferences reduced repetition in other analyses without establishing better completion.

`shortages()` reuses each operation's last reported required/available maps rather than reconciling current reservations and receipts. `WAIT_EXPIRY` expires a historical busy code after 1,800 ticks regardless of the real loan's state. The stall guard tracks machine-count peaks, historical shortage totals, output of items in that cached shortage set and a best-ever goal gap. Working production for a batch absent from that set, valid reconciliation, repairs that restore the same count, and delivery can be missed. Conversely, endless output of a tracked item can keep a run alive without meeting its obligation.

Make progress transaction-specific: remaining construction bill, ghost completion, relevant crafts, delivered items, restored service and next feasible event. Use projected completion time and a bounded no-change check. Preserve hard game/wall budgets. Replaying a working batch with a longer stall horizon is a useful test; blindly increasing every timeout is not a demonstrated fix.

Sources: `training/laya_ops_runner.py:shortages`, `last_codes`, stall accounting in `run`.

### 10. There is no closed learning loop for these ops records

The active policy is `GuidedPolicy`, a seeded sampler of multiplicative JSON preferences with a uniform exploration floor. It does not read the brief's prose and no model is trained on `decision-states.jsonl` by this campaign. Earlier L1–L3 neural/feature training exists and remains separate; claiming that all Laya training is absent would also be wrong.

Recorded choices/outcomes alone are not trustworthy supervised best-action labels. Failed trajectories need faithful same-prefix alternatives or reviewed preference evidence; duplicating the sampled choice teaches the existing weighted policy and its errors. The new ops state also lacks the full versioned model contract/provenance expected by the established training path. Character budgets of 8k/16k are not tokenizer guarantees.

Reuse completion-first measured labels, scenario-grouped splits, profile compatibility, calibration and held-out gates from the existing lab workflow. Fit a small contextual feature scorer first, then distil qualified choices to Laya. It is faster to verify whether the new facts are useful with that scorer than to begin with another large neural run.

Sources: `training/laya_guided_policy.py`; `training/laya_ops_runner.py` state/outcome projection; `training/laya_dataset.py`, `training/laya_finetune.py`; `docs/rl/training.md` completion-first and representation contracts.

## Documentation contradictions and stale routing

| Documents | Conflict or drift | Required clarification |
| --- | --- | --- |
| `Laya_Strategy_Guidelines.md` vs actual preferences / October 8 handoff | Core slots described as ×30 while overlapping preferences produce ×180; “other slots ×1” changes core weights too. | Document effective factors and make variants isolate their stated intervention. |
| Variant README vs archive README / October 8 handoff | Adoption requires better long-window target success; mf30 was adopted with C2 0 in every arm. Handoff acknowledges the unmet condition for the next adoption. | Record mf30 as an explicit provisional C1 exception, or retain it as a challenger until the written gate passes. No automatic rollback recommended by this audit. |
| Strategy “Opening” / “Mall expansion” / “Operations” | Permanent gear/cable plus two rotating cells, “permanent” circuits/sticks, and “standing” circuits/belts overlap ambiguously. Backend protects only sole gear/cable anchors. | Separate permanent producer, standing but loan-eligible cell, and temporary batch; state when each applies. |
| Strategy vs deterministic planning on steel | Strategy allows steel as soon as the iron refinery stands. Planning says only after verified district and starter retirement. Implementation also permits the funded pioneer-output cycle break before retirement. | Document the actual lifecycle evidence for the intended cycle break and correct the misleading wait message. |
| Strategy grid rule vs `Laya_Land_Grid.md` and defaults | Strategy says every build stays in a 64-tile parcel; land doc says grid is optional and `Settings.land_model` defaults to patch. | Mark grid passages as conditional scope, and keep active policy context consistent with the selected model. |
| `Laya_Clean_Runner.md` acceptance vs `laya_mission.py` | Claims same predicates; phase proxies differ, skip C5 and omit the research terminal condition. | Publish proxy vs acceptance definitions and use the shared predicates for comparisons. |
| `Laya_Clean_Runner.md` current state vs CLI | Says checkpoint save/resume is unimplemented; CLI supports earned-C1 export/resume. | Describe the supported checkpoint path and its scope; ops does not restore `initial_reached` as geometry does. |
| `Laya_Current_Handoff.md` vs latest dated handoff | “Current” describes September 21's stopped state and resume commands, while October 8 campaign is active. | Make current handoff an index to the latest per-runtime handoff; retain dated history with an explicit historical label. |
| Clean-runner / architecture / authority inventory vs ops adapter | Tooling-only split is stated as current behavior although strategic wrappers remain. | Label current authority leakage as migration debt with precise extraction boundaries. |

Older classifier proposals, optimization pilots and lab-training handoffs are historical or alternative designs. Their different context sizes, inventory fixtures, GPU profiles and results are not contradictions by themselves when their version/runtime scope is explicit. In particular, supplied-input mall success, L1–L3 lab success, earned-C1 continuation success and raw-start ops success must stay separate.

## Highest-impact performance plan

Treat performance as **sustained milestone completion per wall-hour with finite materials**, then successful-run latency and tail cost. GPU throughput alone is not the present limiter. No speedup factor is established by this audit.

1. **Repair the experiment and operation contracts first.** Isolate core/non-core weight changes, account for every job and preserve typed outcomes. Add preview receipts containing costs, accessible stock, outstanding bills and displaced production. Predicted improvement: comparisons become interpretable and the policy can see the causes of its failures. Verify with exact synthetic contract cases and replay of preserved live decisions.
2. **Break the demonstrated foundation/steel cycle and make loans explicit.** Reproduce baseline-s301 from an isolated prefix, reconcile partial foundation submission truthfully, and expose funded steel plus lender/batch-size alternatives. Quantify a lender's dependency debt and restart cost. Allow legitimate finite self-feeding batches; a blanket ban on circuit lending would suppress some valid bootstrap sequences. Hard rejection belongs to demonstrable illegality/impossibility; producer preservation is otherwise a measured preference. Verify fewer pole/steel cycles, earlier drill/furnace delivery and sustained circuit service against the frozen default.
3. **Use event-driven transactions.** Start a bounded batch/build once, poll a compact receipt, and decide again on delivery, changed feasibility, blockage or expiry. Reconcile same-plan retries instead of resurveying and rebuilding service routes. Keep one consistent fact snapshot per decision and cache static recipe/geometry facts. Mf30 mall calls took a median 698 game ticks and as much as 36,397; foundation calls reached 39,163. These durations combine tool work, game advancement and live waiting; profile them before attributing all time to RPCs. Target a large reduction in calls and repeated work without hiding failures or removing exploration.
4. **Train on short, faithful alternatives at important forks.** Fork near the first resource, lender, scale or wait decision rather than repeatedly replaying a failed opening for hours. Restore the save plus material/district/loan ledgers and controller state; prove replay fidelity. Evaluate remaining useful output, sustained prior service, material costs, completion/timeouts and tail costs. Exclude all-failed fabricated preferences. Fit contextual features first, calibrate, then distil to a representation-compatible Laya model.
5. **Complete action coverage and evaluate on two tracks.** Fresh scarce-kit worlds establish bootstrap reliability; earned, hash-verified C1/C2 prefixes efficiently train later fluids/science/research without claiming fresh-start wins. Add goal-driven fluids and research actions, not more ad hoc prerequisite branches. Freeze terrain/save/mod versions and test genuinely new worlds as well as new policy RNG seeds. Use the shared deterministic predicates and simultaneous sustain before any replacement/promotion decision.

For the next bounded comparison, use the frozen current default and small separately attributable interventions: corrected weight isolation; receipt/fact improvements; cycle repair; explicit loan choices. Then test the combined candidate on untouched scenarios. Report complete paired cohorts, C1/C2 acceptance and proxies separately, retention of earlier service, crashes/typed tool faults, p50/p95 time, decision/RPC counts, loan switches and material cost. Choose cohort size from the minimum improvement worth adopting rather than treating a handful of seeds as decisive.

The fastest credible path to a drastic improvement is **better transactions and a learned contextual scorer over honest costs/consequences**, followed by Laya distillation. Prompt expansion and blind overnight sweeps currently spend experiments around hidden states and missing actions.

## Lifecycle and handoff

Audit only: **no lifecycle action required** and no diagnosis-only commit. The existing loan campaign retains its own cutoff and frozen code. Inspect its final complete-cohort results after the authorized window; do not adopt the ×1 arm under the assumption that it changed only other slots.

Future Python fixes require new affected runner processes and a new frozen comparison; they do not alter the active campaign. Lua changes, if any become necessary, require separately scoped mod synchronization and Factorio reload. No real-base promotion follows from this audit or the interim numbers.
