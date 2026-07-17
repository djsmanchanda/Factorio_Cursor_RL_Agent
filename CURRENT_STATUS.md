# Path: Factorio_Cursor_RL_Agent/CURRENT_STATUS.md
# Purpose: Append-only project status log (AGENTS.md §5). First thing any agent reads when resuming work.

# CURRENT_STATUS

Append-only. Newest entries at the bottom. Entries before 2026-07-18 are
backfilled from git history because this file did not exist yet.

## [2026-01-28] Foundation (backfilled)
- Files: docs/00–20, schemas/, agent.md, README.md, factorio_mod/, planners/, core/metrics.py, tools/
- What: Docs suite, invariants, ~40 JSON schemas, snapshot export mod, metrics + supervisor bot policy.
- Why: Phase 0 foundation for the deterministic planning system.

## [2026-02-02..05] CityPlanner scaffolding + execution gating (backfilled)
- Files: planners/city_planner/*, core/progress_*, core/execution_*, core/*_executor.py, factorio_mod/control.lua
- What: Intent routing → plan skeleton → phase orchestrator (symbolic decisions, no geometry); ghost projection, sandboxed Lua ghost sink, progress reconciliation, execution readiness/authorization, bot-assisted construction/upgrade/deconstruction execution paths.
- Why: Phase 2.6 progress & phasing spine with strict authorization gates.

## [2026-02-13..14] RL advisory layer (backfilled)
- Files: rl_advisor.py, rl_feedback_builder.py, rl_*.schema.json, core/{metrics,target_selector,capacity_allocator,ghost_slice_planner,sandbox_zoning,zone_fill_tracker,*_policy}.py
- What: Non-authoritative deterministic RL advisor: enriched observation (spatial/throughput/pressure/gap metrics), damping signals (bot capacity, construction pressure, zone saturation, material supply), expansion target selection, phase budget allocation, zone fill telemetry, feedback/reward builder.
- Why: Phase 2.5 metrics/policies; advisory-only per invariants (RL never plans structure).

## [2026-02-23] Economic safety signal — LOST (backfilled)
- Files: __pycache__/*.pyc only
- What: Commit d47af00 accidentally committed only bytecode; EconomicSafetySignal source + schema were never added.
- Why: Recorded here so the feature is re-implemented, not assumed to exist.
- Next: Re-implement EconomicSafetySignal from the commit message spec.

## [2026-07-18] Project resumed — audit + repo repair
- Files: AGENTS.md (replaces agent.md), .gitignore, CURRENT_STATUS.md, removed 17 tracked .pyc
- What: Full 4-agent audit of repo state after 5-month gap; charter moved to AGENTS.md; bytecode untracked; this status log created.
- Why: Restore the charter-mandated continuity artifact and an honest repo state before new work.
- Next: Port factorio_mod to Factorio 2.0 (info.json 2.0, global→storage, game.*→helpers.*, created_entity→entity, module payload shape) and deploy to %APPDATA%\Factorio\mods.

## Audit snapshot (2026-07-18) — where things stand
- Mature: core/ (~2.7k LOC — metrics, progress state, authorization, phasing, advisory policies); schema validation pervasive.
- Partial: CityPlanner (symbolic decisions only, no geometry; 2 of 9 intents have phase chains); Lua mod logic complete but targets Factorio 1.1 and was never deployed.
- Absent: PlanetPlanner, InterplanetarySupervisor, real LocalLayoutPlanner layout math (stub inspector only), rail standard, block deployment, any transport (no RCON), any orchestrator/main loop, all automated tests, any actual learned RL (heuristics only; sole dep is jsonschema).
