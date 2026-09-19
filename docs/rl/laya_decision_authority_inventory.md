<!-- Path: docs/rl/laya_decision_authority_inventory.md | Purpose: M0 inventory of deterministic policy authority to migrate into the Laya director and mechanics to retain as the Factorio world interface. -->

# Laya M0 decision-authority inventory

**Status:** M0 audit; implementation input for the Laya director migration.

**Scope:** This inventory separates strategic policy from reusable world
interface mechanics in the current deterministic runtime. It does not change
runtime behavior and does not authorize a live deployment. The target is that
Laya owns all non-safety decisions; the deterministic code remains an
observation, option-compilation, validation, execution, and measurement layer.

## Authority test

An existing function is **strategic policy** when it answers any of these
questions from hand-authored rules, fixed ordering, thresholds, or ratings:

- Which goal, prerequisite, recovery, research, or reserve should run next?
- Whether to build, repair, duplicate, expand, upgrade, retire, route, wait, or
  switch strategy?
- Which site, route, scale, tier, transport mode, or layout variant wins?
- Which guideline or fixed lifecycle phase should override another eligible
  option?

An existing function is **mechanics** when it observes facts, enumerates legal
alternatives, realizes a selected operation, validates it, submits it through
the authorized interface, waits for construction, or measures the outcome.
Mechanics may reject an illegal or unsafe request and may report that no legal
option exists; they must not silently select a different strategic option.

The same module can contain both. The migration disposition below is per
function or policy responsibility, not a claim that an entire module should be
deleted.

## Strategic authority to retire from the deterministic controller

| Current authority | Evidence in current tree | M0 disposition | Target Laya operation family |
| --- | --- | --- | --- |
| Fixed production-prep order and standing baseline | `orchestrator/autonomous_builder.py:_prep_intermediate` calls `baseline_build_order()` and takes the first pending recipe; `orchestrator/baseline_production.py:baseline_build_order`, `BASELINE_MACHINES`, `PLATE_FOUNDATION_BUILD_ORDER`, and `BOOTSTRAP_FURNACE_CAPS` encode fixed opening choices. | Retire the ordering, standing counts, furnace caps, and fixed phase ownership. Keep catalog lookup and stage construction as registry capabilities. | `serve_goal`, `next_operation`, `assemble`, `smelt`, `mine` |
| Fixed iron → copper → stone foundation sequence | `orchestrator/autonomous_builder.py:run` explicitly calls `_prep_plate_foundation`, tests all `PLATE_FOUNDATION_BUILD_ORDER`, and later gates belt, steel, core mall, and extraction phases. | Retire as policy. Compile the currently legal foundation candidates and let Laya choose goal/operation/variant. Preserve lifecycle evidence and safety gates. | `serve_goal`, `next_operation`, `variant`, `wait/escalate` |
| Goal-first versus background-reserve arbitration | `orchestrator/autonomous_builder.py:_serve_ready_pass` serves `task`, then `_advance_the_goal`, then `_serve_background_mall_task`; comments explicitly define mission priority. | Retire this hard-coded arbitration. Expose goal-stack entries, reserve obligations, and consequences as bounded choices. | `serve_goal`, `interrupt`, `next_operation` |
| Priority ratings, default item order, promotion, and retry scheduling | `orchestrator/priority_list.py:PriorityList.next` sorts by rating/progress/age; `_DEFAULT_RATINGS` and `PriorityList.promote` assign fixed priority 100 to blocking prerequisites. `orchestrator/autonomous_builder.py:_survey_pass` calls `priorities.sync`, `promote`, and `next`. | Retain durable task identity/status/retry persistence and tick accounting. Retire rating tables, `next` ranking, automatic promotion as authority, and fixed retry horizons; Laya selects from eligible tasks. | `serve_goal`, `continue_or_wait`, `interrupt`, `recovery` |
| Mall target generation and reserve policy | `orchestrator/autonomous_builder.py:_open_the_run` calls `mission_mall_targets`; `_survey_pass` adds ghost bills and evergreen stockouts; `_prep_post_metal_stack_reserves`, `_prep_core_mall`, `_rationed_mall_batch`, and `_ensure_mall_item` encode finite batches, anchors, reserve floors, and mall promotion. `orchestrator/parts_mall.py:mission_mall_targets` and `orchestrator/baseline_production.py:RATIONED_MALL_BATCH_ITEMS` provide policy data. | Retire fixed reserve targets, anchor exceptions, pre-core/post-metal phase rules, and automatic mall admission. Keep bill calculation, stock observation, reservation accounting, and mall cell operations as registry tools. | `serve_goal`, `assemble`, `reserve-space`, `upgrade`, `wait/escalate` |
| Automatic goal expansion diagnosis | `orchestrator/build_decisions.py:expansion_target` recursively follows the scarcest ingredient to a mineable recipe; `diagnose_replenishment` chooses missing producer versus extraction. `orchestrator/autonomous_builder.py:_serve_mall_task` calls them and automatically builds upstream or expands mining. | Retire the deterministic “deepest scarce input” and recovery choice. Preserve recipe graph traversal and measured shortage evidence as option features/candidate generation. | `recovery`, `mine`, `assemble`, `next_operation` |
| Automatic goal advancement and shortage response | `orchestrator/autonomous_builder.py:_advance_the_goal` calls `ensure_produced` for the single `goal_item`, queues shortages, and parks chemical rungs. `run` repeatedly invokes it until output exists. | Retire single-target recursive authority. Keep the typed shortage/deferred reports and make the goal stack a Laya input/output. | `serve_goal`, `recovery`, `continue_or_wait` |
| Item-name dispatch to fixed strategy branches | `orchestrator/autonomous_builder.py:ensure_produced` dispatches coal, plastic/sulfur, sulfuric acid, battery, steel chest, fast belt, pipe, automation science, and generic recipes through hard-coded branches and gates. | Retire dispatch as policy. Register each executable capability as an operation family with eligibility predicates, generated variants, validators, and expected effects. Keep individual builders as realizers behind the selected operation. | `mine`, `smelt`, `assemble`, `research`, `variant`, `recovery` |
| Chemical ladder and prerequisite sequencing | `orchestrator/stage_chemical.py:ensure_oil_cell`, `ensure_sulfuric_acid_cell`, `ensure_battery_cell`, `ensure_coal_mine`; `autonomous_builder.py:_ensure_chemical_ladder_predecessor`, `_advance_parked_chemical_rung`, and `_core_mall_prerequisites` choose predecessor order and parking behavior. | Retire fixed ladder order and automatic predecessor selection. Retain fluid/source surveys, route generation, purity validation, stage construction, and typed wait reports. | `serve_goal`, `assemble`, `route`, `recovery`, `continue_or_wait` |
| Capacity thresholds and production-promotion choice | `orchestrator/autonomous_builder.py:_plan_line` calls `promoted_line_machine_count`, computes backlog/saturation, and uses `prefer_direct_transport`; `_promotion_upstream_shortfall` and `_build_assembled_stage` impose steel/fast-belt/iron capacity gates. | Retire fixed promotion thresholds and transport-energy preference as policy. Keep rate/backlog/energy calculations as observation features and candidate estimates; Laya chooses promotion, repair, or continued requester flow. | `assemble`, `route`, `upgrade`, `recovery` |
| Repair-before-duplicate and line-state choice | `orchestrator/autonomous_builder.py:_repair_stalled_line` declares an existing idle line a repair job; `_serve_healthy_line` upgrades/returns an existing line; `stage_recovery.py:repair_existing_ingredient_transport` is invoked only under fixed conditions. | Retire the automatic classification as final authority. Keep structural health/status probes and repair transactions; expose repair, duplicate, resize, and wait as alternatives. | `recovery`, `repair`, `assemble`, `wait/escalate` |
| Fixed site/scale/inserter/tier choice | `orchestrator/build_decisions.py:_heaviest_source` selects the heaviest input as the site anchor and `_stage_inserter_type` selects the cheapest stocked tier; `autonomous_builder.py:_plan_line` and `_build_assembled_stage` choose machine count, belt mode, and mall/direct mode. | Retire winner selection. Keep rate calculations, candidate-site enumeration, stock reads, and plan generation; present bounded legal variants to Laya. | `variant`, `assemble`, `route`, `upgrade` |
| Fixed mining/refinery expansion policy | `orchestrator/autonomous_builder.py:_prep_plate_extraction`, `build_mining_stage`, and `_prep_plate_foundation` choose baseline plate order, doubling phases, expansion timing, and deferred target behavior. `orchestrator/stage_extraction.py:plan_local_extraction` and `choose_mining_origin` include candidate selection. | Retire expansion timing, phase sizes, and selected-origin authority. Preserve patch surveys, candidate geometry, ownership/reservation checks, and legal plan generation. | `mine`, `smelt`, `variant`, `reserve-space`, `recovery` |
| Fixed transport mode and belt-tier preference | `orchestrator/stage_transport.py:ensure_ingredient_transport`, `choose_belt_tier`, `_choose_route_belt_tier`, and `_transport_mode` choose belt/logistics mode and tier using deterministic stock/rate rules; `prefer_direct_transport` adds a deterministic energy choice. | Retire mode/tier winner. Preserve transport feasibility surveys, route candidate generation, belt/pipe realization, collision and fluid checks, and cost/energy evidence. | `route`, `variant`, `repair`, `wait/escalate` |
| Fixed steel and science progression gates | `orchestrator/autonomous_builder.py:_prep_steel_district`, `_promote_mall_after_iron`, `_post_starter_phase`, `_metal_science_transition_status`, and `_core_mall_prerequisites` enforce phase transitions and prerequisites. | Retire fixed progression. Retain measured output, lifecycle, recipe/technology availability, and construction contracts as eligibility observations. | `serve_goal`, `assemble`, `research`, `recovery` |
| Fixed research candidate ranking | `orchestrator/research_scheduler.py:rank_research_candidates` sorts eligible technologies by a deterministic score derived from goal unlock value/cost and returns one selection. `tools/autonomous_run.py:_research` and `_research_queue` perform target/queue control. | Retain allow-list validation, research status observation, queue persistence, and authorized actuator. Retire score-based selection; Laya chooses among eligible technologies unless the user explicitly supplies a queue scope. | `research`, `serve_goal`, `continue_or_wait` |
| Ordered research queue as strategic authority | `orchestrator/research_queue.py` validates an ordered persisted queue; `tools/autonomous_run.py:_research_queue` processes it in order. | Keep queue schema and actuator for explicit externally supplied scope. When the director owns research, make queue entries model-selected proposals with validation, provenance, and interrupt/rollback semantics. | `research`, `interrupt`, `wait/escalate` |
| Fixed phase-router/city-planner decisions | `planners/city_planner/intent_router.py`, `planning_gate.py`, `phase_orchestrator.py`, and `plan_skeleton.py` define named phases including `transport_strategy_selection`; `capability_resolver.py` resolves phase capabilities. | Retire phase ordering and phase-level strategy selection as hidden policy. Reuse capability discovery, topology/geometry candidate generation, and phase result validation as registry/compiler mechanics. | `next_operation`, `variant`, `route`, `reserve-space` |

## Mechanics to retain, narrow, and expose through the registry

These components should not be removed merely because they currently sit
under the deterministic controller. Their interfaces should be narrowed so a
selected Laya operation supplies intent/variant parameters and the component
does not re-rank alternatives or silently substitute a different strategy.

| Reusable mechanic | Evidence in current tree | Migration disposition | Target operation family |
| --- | --- | --- | --- |
| Live world and catalog observation | `orchestrator/live_base.py` exposes entity/line/status/inventory/recipe facts; `orchestrator/game_bridge.py:GameBridge` and `load_json` access Factorio reports; `_open_the_run` loads live recipe, machine, and stack catalogs. | Retain as versioned observation adapters. Normalize unknown values and attach surface/force/save/episode provenance to `laya_state`. | `observe/diagnose` |
| Resource, terrain, occupancy, and infrastructure surveys | `planners/resource_survey.py`, `orchestrator/resource_patches.py`, `orchestrator/stage_extraction.py`, `orchestrator/transport_occupancy.py`, `planners/transport_occupancy.py`, and `planners/land_value.py` survey legal world facts and candidate space. | Retain as option compilers. Return complete candidate sets with hashes and evidence; remove any implicit “best” choice from the boundary. | `observe/diagnose`, `mine`, `route`, `reserve-space` |
| Geometry and layout realization | `planners/local_layout_planner.py:LocalLayoutPlanner`, `planners/fluid_layout_search.py`, `planners/fluid_layouts.py`, `planners/refinery_blueprints.py`, `planners/refinery_basic_blueprints.py`, `planners/smelter_block.py`, `planners/mall_layout.py`, and `planners/zoning_geometry.py` generate structured plans. | Retain as parameterized realizers. Given a selected candidate, produce a deterministic plan; if several legal candidates are returned, the registry owns the option list and Laya selects. | `smelt`, `assemble`, `route`, `variant` |
| Hard plan and legality validation | `planners/plan_validation.py:actions`, `ENTITY_FOOTPRINTS`; `planners/plan_validation.py` and `orchestrator/stage_services.py:validate_builder_target`, `assert_affordable`; fluid/transport validators in `planners/fluid_routing.py`, `planners/belt_bridge.py`, and `planners/pipe_bridge.py`. | Retain as a non-negotiable safety shield. Validators may reject, request re-observation, or safe-stop; they may not rank or choose a fallback operation. | all operation families |
| Material funding and reservation ledger | `orchestrator/material_reservations.py:MaterialReservationLedger`, `plan_material_bill`, `orchestrator/construction_stock.py`, `orchestrator/parts_mall.py:add_demands`, `wait_for_stock`, and `orchestrator/bootstrap_supply.py`. | Retain actual-item accounting, project identity, reservation, shortage reports, and construction affordability. Convert shortages into observations/options instead of automatic policy branches. | `observe/diagnose`, `assemble`, `mine`, `smelt`, `wait/escalate` |
| Blueprint-ghost submission and bot construction | `orchestrator/stage_services.py:_submit`, `_wait_for_ghosts`, `orchestrator/autonomous_builder.py:bring_stage_up`, `orchestrator/recoverable_retirement.py:retire_entities_via_bots`, and `GameBridge` execution endpoints. | Retain as the execution actuator. Require registry operation IDs, scope, authorization, plan hash, and rollback metadata; no direct placement or item gifting. | all build/repair/retire families |
| Power and logistics service primitives | `orchestrator/stage_services.py:extend_power`, `ensure_logistic_coverage`, `extend_roboport_coverage`; `orchestrator/power_district.py:ensure_power_capacity`; `orchestrator/roboport_placement.py` and `roboport_coverage.py`. | Retain as explicit service operations and validators. Laya chooses whether/where service is needed; the primitive only realizes the selected legal extension. | `power`, `logistics`, `repair`, `route` |
| Transport and fluid execution primitives | `orchestrator/stage_transport.py:ensure_ingredient_transport`, `_direct_single_belt_feed`, `_publish_output_chest`; `orchestrator/stage_recovery.py:repair_existing_ingredient_transport`; `planners/fluid_routing.py` and `planners/pipe_bridge.py`. | Retain candidate generation/realization and reports. Split mode/site selection from execution and prevent automatic alternate strategy substitution. | `route`, `repair`, `variant` |
| Upgrade, retirement, and ownership-safe mutation | `planners/assembler_tiers.py:entity_upgrade_plan`; `orchestrator/recoverable_retirement.py:retire_entities_via_bots`; `orchestrator/refinery_state.py:assert_refinery_removals_owned`; `orchestrator/mine_retirement.py:retire_depleted_mines`. | Retain exact-position/source-prototype/recipe guards, bot recovery, ownership, and rollback. Laya chooses whether and which eligible target to upgrade/retire. | `upgrade`, `retire`, `recovery` |
| Measurement, diagnostics, and typed failure evidence | `orchestrator/build_diagnostics.py`, `orchestrator/chain_telemetry.py`, `orchestrator/mission_state.py`, `tools/run_evidence.py`, `tools/run_context.py`, `orchestrator/stage_services.py:_diagnose_machines`, and typed `StuckError`/`ProductionPrerequisiteDeferred`. | Retain and extend into `laya_decision`/outcome records. Attribute failure to selected option, validator, execution, environment, or policy; never turn a diagnostic into an automatic policy rule. | `observe/diagnose`, `recovery` |
| Runtime isolation and explicit authorization | `docs/system_invariants.md`; `tools/autonomous_run.py` connection/surface/force/episode handling; `orchestrator/controller_budget.py`; `orchestrator/bootstrap_profiles.py`. | Retain as hard boundaries. Add model/guideline/tool-registry provenance and deny cross-runtime or unscoped actions before execution. | all operation families |
| Controller budgets and no-progress protection | `orchestrator/controller_budget.py:ControlBudget`, `begin_run_budget`, `consume_plan_submission`, `consume_wait`, `consume_diagnosis`; `autonomous_builder.py:_livelock_step`, `_refuse_to_spin`, `_controller_ground_progressed`. | Retain bounded safety/operational controls and typed stop reports. Do not treat their thresholds as a strategy or as a reward signal; feed stops to training/evaluation. | `continue_or_wait`, `wait/escalate`, all families |
| Mission and durable lifecycle state | `orchestrator/mission_state.py:MissionStateLedger`, `orchestrator/bootstrap_district.py:BootstrapDistrictLedger`, `orchestrator/work_state.py:WorkStateSignal`, and `orchestrator/material_reservations.py`. | Retain identity, lifecycle, provenance, and recovery records. Expose state to Laya; do not let persisted stale ratings or policy decisions silently control the next run. | `observe/diagnose`, `recovery`, `retire` |
| Research status and authorized actuator | `tools/autonomous_run.py:_research`, `orchestrator/research_queue.py:load_queue/update_item`, and `GameBridge` research methods. | Retain live status, technology allow-list/schema validation, queue updates, and exact actuator. Move target selection to Laya or explicit user scope. | `research` |

## Migration rules for M0 and later milestones

1. **Freeze policy growth in the listed branches.** A bug fix may repair a
   safety, legality, isolation, accounting, observation, execution, or
   measurement defect. It must not add another fixed priority, threshold,
   seed-specific layout, recipe exception, or recovery branch.
2. **Split before replacing.** For every retired function, first expose its
   read-only facts and candidate alternatives in the registry. Then add a Laya
   decision boundary. Only after parity/evaluation should the old branch be
   disabled as a decision authority.
3. **Candidate generation is not policy.** A generator may enumerate every
   legal site, route, scale, tier, or operation variant and annotate cost,
   risk, dependencies, and expected effects. It may not select a winner for
   strategic reasons. A single legal candidate may be returned as a fact, but
   “best” must not be hidden in the generator.
4. **Validation cannot become a fallback planner.** On rejection, return the
   reason and remaining candidates to Laya. If no candidate remains, issue a
   safe stop/escalation with evidence.
5. **Preserve mechanical invariants.** Real Nauvis, disposable training, and
   legacy runtime boundaries remain explicit. New construction remains
   blueprint-ghost, materially funded, bot-built, ownership checked, and
   rollback-scoped.
6. **Log the complete choice set.** Every future director decision records the
   eligible options before and after validator pruning, selected option, model
   probabilities/calibration, plan/configuration hash, execution report,
   measured outcome, and attributed cause.
7. **Retire only after parity evidence.** The old cascade remains a frozen
   comparison baseline until the Laya director proves held-out improvement,
   zero safety violations, and correct safe escalation on unsupported or
   invalid operations. A model failure must not silently reactivate the old
   policy cascade.

## M0 acceptance checklist

- [x] Add versioned `laya_state`, `laya_case`, `laya_decision`,
      `tool_registry`, and `guideline_pack` schemas.
- [x] Add a registry-definition interface that separates capability metadata
      from policy selection, fails closed on missing safety metadata, and does
      not rank operations.
- [x] Record the strategic branches to retire and the mechanics to preserve.
- [x] Add focused tests for schema strictness, authorization requirements,
      guideline promotion safety, option bounds, and registry immutability.

M1 starts the runtime-facing work:

- [ ] Compile concrete seed-specific operation options with identity, scope,
      signatures, plan hashes, expected effects, and validator evidence.
- [ ] Instrument the listed policy seams without changing live behavior:
      goal arbitration, priority selection, operation dispatch, site/scale/
      route choice, recovery classification, and research selection.
- [ ] Produce a first fixture containing complete eligible/pruned candidate
      sets and deterministic outcome attribution.
- [ ] Add tests proving validators reject unsafe choices without selecting a
      replacement and that execution cannot bypass material, surface/force,
      ghost, ownership, or rollback contracts.
- [ ] Freeze the old deterministic controller as an evaluation baseline and
      label its decisions as contrastive historical evidence, not ground truth.

## Audit evidence

The inventory was verified against the current repository tree on 2026-09-19.
Representative definition checks used for this document include:

```text
rg --files orchestrator planners tools
rg -n "def ensure_produced|def _survey_pass|def _serve_ready_pass|def _serve_mall_task|def _prep_intermediate|def _prep_plate_extraction|def _advance_the_goal" orchestrator/autonomous_builder.py
rg -n "def expansion_target|def diagnose_replenishment|def _heaviest_source|def _stage_inserter_type" orchestrator/build_decisions.py
rg -n "class PriorityList|def next|def promote|def rank_research_candidates" orchestrator/priority_list.py orchestrator/research_scheduler.py
```

All paths and named functions in the tables are present in the checked-out
tree. This document is documentation-only; no runtime, save, mod, or external
service was changed.
