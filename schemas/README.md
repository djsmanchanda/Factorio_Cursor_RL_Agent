<!-- Path: schemas/README.md -->
<!-- Purpose: Document JSON schemas and provide comment-free JSON guidance. -->

# Schemas

This directory contains JSON schemas that define inter-layer contracts.

All schema files are strict JSON and therefore do not include comments.
Documentation for each schema is maintained in this README.

## Files

- `snapshot.schema.json` — Lua → Planner snapshot contract
- `build_plan.schema.json` — Planner → Lua build plan contract
- `goal.schema.json` — Instruction → Planner goal contract
- `block.schema.json` — City/Planet block contract
- `intent.schema.json` — Supervisor → Planner intent contract
- `plan_skeleton.schema.json` — Planner readiness → plan skeleton contract
- `phase_result.schema.json` — Phase planner → phase result contract
- `planning_bundle.schema.json` — Phase orchestrator → planning bundle contract
