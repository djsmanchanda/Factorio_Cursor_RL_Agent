<!-- Path: schemas/README.md -->
<!-- Purpose: Document JSON schemas and provide comment-free JSON guidance. -->

# Schemas

This directory contains JSON schemas that define inter-layer contracts.

All schema files are strict JSON and therefore do not include comments.
Documentation for each schema is maintained in this README.

## Files

- `snapshot.schema.json` - Lua to Planner snapshot contract; planner-owned bounded surfaces include versioned resource and water observations
- `science_status.schema.json` - Lua to deterministic science observer contract for one force's research state and lab input/status telemetry
- `rocket_launch_observation.schema.json` - Lua to optimizer contract for scope-explicit `on_rocket_launched` events (rocket finishes ascent, not cargo-pod delivery) and their correlated scope responses; observation evidence only, not victory or execution authority
- `rocket_silo_observation.schema.json` - Lua to optimizer contract for request-correlated, read-only observation of existing rocket silos for one explicit surface and force; reports stable unit identity, status, parts, recipe, auto-launch, rocket and cargo-pod facts with explicit absent/unavailable states against runtime API 2.1.21, without launch or mutation authority
- `research_queue.schema.json` - persisted Linux deterministic runner intent and per-technology progress contract
- `build_plan.schema.json` — Planner → Lua build plan contract
- `goal.schema.json` — Instruction → Planner goal contract
- `block.schema.json` — City/Planet block contract
- `intent.schema.json` — Supervisor → Planner intent contract
- `plan_skeleton.schema.json` — Planner readiness → plan skeleton contract
- `phase_result.schema.json` — Phase planner → phase result contract
- `planning_bundle.schema.json` — Phase orchestrator → planning bundle contract
- `capability_resolution.schema.json` — Capability resolver → resolution contract
- `planning_gate.schema.json` — Planning gate → readiness decision contract
- `planning_request.schema.json` — Intent router → planning request contract
- `build_intent.schema.json` — Planner → build intent contract
- `ghost_plan.schema.json` — Ghost projection → ghost plan contract
- `optimizer_consumer_observation.schema.json` — Scoped consumer recipe and lifetime-counter observation; no proof of connection, consumption or commissioning
- `electronics_world_spec.schema.json` - Survey to LocalLayoutPlanner coordinates and observed capacity contract
- `world_spec.schema.json` - deterministic bounded planner surface, resources, and starter-kit contract
- `training_scenario.schema.json` - isolated mini-environment, objective, construction budget, constraints, and reward-weight contract
- `training_transition.schema.json` - observation, candidate choice, measured outcome, failure class, and decomposed reward contract
- `training_episode_report.schema.json` - training-mod provision, observation, and asynchronous recycle reports; all rates are per tick
- `laya_state.schema.json` - bounded director input containing goals, normalized facts, guidelines, options, budgets, and complete provenance
- `laya_case.schema.json` - typed fine-tuning case with source evidence, train/calibration/held-out split, gold distributions, and label provenance
- `laya_decision.schema.json` - append-only model decision, validation, authorization, execution, outcome, reward, and attribution evidence
- `laya_tool_registry.schema.json` - declarative operation inventory; it names required authorization actions but never grants them
- `laya_guideline_pack.schema.json` - versioned hard/soft task guidance with source provenance and evidence-gated promotion
