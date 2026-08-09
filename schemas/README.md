<!-- Path: schemas/README.md -->
<!-- Purpose: Document JSON schemas and provide comment-free JSON guidance. -->

# Schemas

This directory contains JSON schemas that define inter-layer contracts.

All schema files are strict JSON and therefore do not include comments.
Documentation for each schema is maintained in this README.

## Files

- `snapshot.schema.json` - Lua to Planner snapshot contract; planner-owned bounded surfaces include versioned resource and water observations
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
- `electronics_world_spec.schema.json` - Survey to LocalLayoutPlanner coordinates and observed capacity contract
- `world_spec.schema.json` - deterministic bounded planner surface, resources, and starter-kit contract
- `training_scenario.schema.json` - isolated mini-environment, objective, construction budget, constraints, and reward-weight contract
- `training_transition.schema.json` - observation, candidate choice, measured outcome, failure class, and decomposed reward contract
