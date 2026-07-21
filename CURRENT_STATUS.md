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

## [2026-07-18] Factorio 2.0 mod port verified live — first closed loop
- Files: factorio_mod/control.lua, factorio_mod/info.json, scripts/deploy_mod.ps1, tools/rcon_client.py, docs/10_checklist_todo.md
- What: Mod ported to 2.0 API and verified end-to-end against Factorio 2.0.77 — headless server, /snapshot via new stdlib RCON client, output validated by validate_snapshot.py and consumed by inspect_metrics.py. Extra 2.0 fix found live: get_recipe() now hard-errors on non-crafting entities (and a command error kills a dedicated server), so recipe read is gated by entity type.
- Why: The game bridge was the blocking gap; the system had never observed a live game before today.
- Next: script-output watcher + top-level orchestrator loop (snapshot → metrics → supervisor → planner → authorization).

## [2026-07-18] Orchestrator: first full observe→plan→authorize→execute cycle
- Files: orchestrator/{__init__,game_bridge,run_cycle}.py, tests/fixtures/orchestrator_*.json, docs/10_checklist_todo.md
- What: One-shot cycle CLI against a live server — RCON /snapshot (906,697 entities from the 1M megabase save), schema validation, metrics, supervisor signals, intent routing/planning, then authorized ghost execution: 50 ghosts placed on planner-sandbox and observed back, all schema-gated.
- Why: Converts the offline library into an agent loop; execution authority stays with the deterministic authorizers.
- Next: Fix the capacity-model flaw — build_progress_state pins committed=ultimate so auto-derived phasing yields delta 0 (cycle currently needs a hand-authored --progress-state). Then a recurring loop daemon. Note: dedicated server must run with auto_pause=false (mod commands queue forever on a paused server); megabase /snapshot takes ~60s so RCON needs a long socket timeout.

## [2026-07-18] Capacity model fixed — autonomous ghost projection from pure observation
- Files: core/{progress_state,execution_readiness,block_prototypes}.py, planners/city_planner/ghost_projection_phase.py, factorio_mod/control.lua, orchestrator/run_cycle.py, tests/test_capacity_model.py, requirements.txt
- What: committed_capacity = current + pending sandbox ghosts (was pinned to ultimate); ghost delta and execution readiness now use fill-the-active-phase semantics (was phase-jump-only, permanently inert); current_capacity derived from snapshot placeholder prototypes; empty ghost observation serializes as [] not {}. First test suite added (9 passing). Verified live on the 1M server: cycle 1 auto-projects 50 ghosts, cycle 2 observes them pending and holds at delta 0 — convergent with zero hand-authored state.
- Why: The February pipeline could never emit a ghost from real observations; this makes the expansion loop genuinely autonomous while keeping phase advancement behind explicit authorization.
- Next: recurring loop daemon; wire construction execution + authorized phase advance into the cycle; broaden tests.

## [2026-07-18] First live-watched build: 50/50 constructed by bots on screen
- Files: core/{sandbox_zoning,block_prototypes}.py, factorio_mod/control.lua, tests/test_capacity_model.py
- What: With the user connected as a player watching, the full chain ran live: cycle projected 50 ghosts, /execute_construction authorized, bots built 30 assemblers + 20 furnaces to zero remaining ghosts. Two product bugs found and fixed: zone stride was hardcoded 2x2 so 3x3 placeholders overlapped and could never be revived (stride now derives from prototype footprint + spacing); the sandbox surface used default mapgen producing unbuildable alien terrain/debris (new surfaces now generate with lab tiles).
- Why: Real-time observation immediately exposed placement bugs that offline schema validation could not.
- Next: Fold construction execution + scaffolding provisioning (power/roboports/bots/materials) into the orchestrator loop instead of manual RCON steps; recurring daemon; authorized phase advance in-cycle.

## [2026-07-18] Loop daemon + live dashboard: 120/120 autonomous build
- Files: orchestrator/loop_daemon.py, tools/{dashboard_server.py,dashboard.html}, factorio_mod/control.lua, planners/city_planner/ghost_projection_phase.py, core/phase_advance_evaluator.py
- What: Recurring daemon completed a 120-structure intent in 3 iterations with zero manual steps: recognized 50 pre-built, authorized phase advances (100 → 120, clamped), projected only per-block remainders at continuing zone cells, provisioned per-anchor scaffolding/materials, bots built everything. Web dashboard (http://127.0.0.1:9137) shows the agent's full decision trace live: capacity math, phasing verdicts, execution gates, authorizations, per-block ledger, sandbox map.
- Why: Live observability keeps exposing bugs offline validation cannot (per-block re-projection, zone index restarts, starved logistic networks — all found by watching).
- Next: Known cosmetic quirk: _derive_active_phase can report a phase above ultimate on a satisfied intent (250 for a 120 target) — harmless, done-check fires first, but clean up. Then: bigger multi-phase intents; nauvis supervision feeding real build intents instead of fixtures.

## [2026-07-18] First functioning production line — LocalLayoutPlanner is real
- Files: planners/local_layout_planner.py, schemas/build_plan.schema.json, factorio_mod/control.lua, orchestrator/game_bridge.py, tools/build_line.py, tests/test_capacity_model.py
- What: generate_line_layout emits a deterministic belt-fed line (belts, directional inserters, machines with recipes, dual power rows, feed/collect endpoints) through the previously-dormant build_plan contract; /build_layout_plan executes it at explicit positions. Verified live: 8-machine iron-gear-wheel line sustaining ~3 gears/s (180/min) into the collector. Live debugging found: feeder drop tile needs belt coverage; one pole row can't power both inserter rows.
- Why: Prior builds placed disconnected placeholders; this is the first build where items move — input → craft → transport → collect.
- Next: multi-ingredient recipes (multiple input lanes), recipe DAG chaining (line feeding line), collector throughput (multiple drain inserters), and wiring line construction into the loop daemon.

## [2026-07-18] Multi-ingredient lines — electronic circuits flowing
- Files: planners/local_layout_planner.py, tools/build_line.py, docs/21_external_game_knowledge.md
- What: Recipes carry ingredient lists; two-ingredient recipes feed via the input belt's two lanes (opposite-side feeders). Wiki knowledge ingested as data-only reference. Verified live first-try: 6-machine electronic-circuit line, first output 36.5s, 72 circuits/120s.
- Why: Circuits are the gateway to automated science; dual-lane feeding is the deterministic pattern for all 2-ingredient assembly.
- Next: line chaining (gear line output belt → science line input), then automation-science-pack line feeding labs — the first full science chain. Then teach the loop daemon to build lines instead of placeholder grids.

## [2026-07-18] Raw-to-product chain: mining → smelting live; electric-only invariant
- Files: planners/local_layout_planner.py, core/block_prototypes.py, factorio_mod/control.lua, tools/build_line.py, docs/20_system_invariants.md (§12), tests
- What: Miner-fed smelting lines (drills drop ore directly on the input belt; drill drop tile = center+2, learned live). Ore patches seeded via scaffolding. 6-drill/6-furnace line: ~3.8 iron plates/s, ore belt saturated. Electric-only equipment is now invariant §12 with a loud validation guard; smelting placeholder → electric-furnace.
- Why: First production with zero scripted item sources — the user's specified chain (mine → belt → smelt → belt → assemble) minus only the final assembly hop.
- Next: chain plate belts into assembler lines (smelter output feeding the gear/circuit lines), then the full science chain; teach the loop daemon lines.

## [2026-07-18] Throughput engineering: 16x via tiers + demand-scaled feeders
- Files: planners/local_layout_planner.py, tools/build_line.py, docs/21_external_game_knowledge.md, tests
- What: Belt/inserter tier parameters; recipe amounts + craft times power per-ingredient demand math; feed points scale as ceil(demand/feeder_rate). Same circuit line measured: 0.6/s (yellow/fast/1 feed) → 7.7/s (express/stack) → 9.6/s steady (demand-scaled feeds) = the 6-machine cap.
- Why: User-diagnosed rotation-bound feeder bottleneck (first machines strip the belt, rest starve) — confirmed live and encoded as planner math.
- Next: chain lines (smelter plate belt → assembler input), science chain to labs, teach loop daemon to build lines.

## [2026-07-18] Orchestrated wave: quality/modules, bottleneck diagnosis, sideload + chaining
- Files: core/{quality_modules,bottleneck_diagnosis}.py, planners/local_layout_planner.py, tests/ (+50 tests, 70 total), docs/21, tools/build_line.py
- What: Three parallel sub-agent tasks (Opus 4.8 geometry; Sonnet 5 data/diagnosis) integrated and live-verified. Quality/module tables with jam guard (exact-quality-match) — module slots verified against live 2.0.77 prototypes (am1=0). Deterministic per-line bottleneck verdicts → catalog actions. Sideload feed style + generate_chain_link. Live: sideload lane assignment confirmed; measured feed tradeoffs — chest/express 9.6/s, sideload/express 8.27/s (lane-capped), sideload/turbo 9.07/s.
- Why: N1 mechanics + the RL layer's observation (diagnosis) and action-effect data (feed tradeoffs).
- Next: split local_layout_planner (565 LOC > 500); inserter-utilization measurement (diagnosis rule 2c); live chain-link test needs southern scaffolding anchors + a no-feeder "chained" feed style; science chain to labs.

## [2026-07-18] PHASE N2 CLEARED — ore to research, end to end
- Files: planners/local_layout_planner.py, factorio_mod/control.lua, tools/build_science_chain.py, docs/22 (force isolation), tests (80 green)
- What: The agent's own factory researched 4 technologies (automation, electronics, steam-power, electric-mining-drill) from ore it mined. Chain: drills → ore belt → electric furnaces → plate belts → gear line → (lane 1 copper / lane 2 gears) → science line → labs. Measured ~0.4 research units/s with 4/4 labs working, 8 packs consumed per 20s. Nothing scripted in the ingredient path.
- Why: docs/22's terminal reward (research per time) now exists as a real, attributable signal — the gate the RL decision layer was waiting on.
- Live-found fixes: substation beyond medium-pole wire reach (9 tiles) leaves a line silently unpowered; a descent loop through the junction row stacks two belts on one tile and dead-ends the connector. Both regression-tested.
- Force isolation is now REQUIRED (docs/22): on a completed save the reward is identically zero and researched techs cannot be un-researched; the agent runs on force "planner" with the automation-science-pack trigger tech bootstrapped.
- Next: action catalog + greedy baseline policy + transition logging (docs/22 steps 1-2); loop daemon builds lines instead of placeholder grids.

## [2026-07-18] Autonomous decision-making + expansion: the RL decision layer runs
- Files: core/{action_catalog,baseline_policy}.py, orchestrator/{expansion_daemon,chain_telemetry}.py, planners/local_layout_planner.py (generate_line_extension), tools/dashboard*, tests (116 green)
- What: The expansion daemon ran 8 unattended cycles on the live factory: measured every line (status counts, belt fills, real production statistics), diagnosed per-line verdicts, built a costed action catalog with predicted effects, chose by walking the chain backwards to the BINDING constraint, executed, and logged the full transition. Result: lab row grew 4 -> 20 labs while research climbed 3 -> 9 technologies, all targets self-selected.
- Why: docs/22 steps 1-2 (action catalog + deterministic baseline + transition logging) are now real, generating (observation, action, reward) traces for a future learned policy to train on and beat.
- Live-found: a chained line has no terminal chest by design, so telemetry's collectors_full default faked a permanent drain verdict (now corrected per-line); the agent exhausted all 13 red-science technologies, which is a legitimate terminal state - the genuine relief is more lab capacity, which add_collectors now builds.
- Dashboard moved to port 9137 (8765 collided with a user app) and gained an Agent decisions panel + decisions timeline.
- Next: executors for tier upgrades / new mines; diminishing-returns awareness (the baseline repeats one action while it stays binding - the first thing a learned policy should improve); a second science type to extend the tech ceiling.

## Audit snapshot (2026-07-18) — where things stand
- Mature: core/ (~2.7k LOC — metrics, progress state, authorization, phasing, advisory policies); schema validation pervasive.
- Partial: CityPlanner (symbolic decisions only, no geometry; 2 of 9 intents have phase chains); Lua mod logic complete but targets Factorio 1.1 and was never deployed.
- Absent: PlanetPlanner, InterplanetarySupervisor, real LocalLayoutPlanner layout math (stub inspector only), rail standard, block deployment, any transport (no RCON), any orchestrator/main loop, all automated tests, any actual learned RL (heuristics only; sole dep is jsonschema).
