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

## [2026-07-18] Fluid systems knowledge encoded
- Files: core/fluid_systems.py, docs/23_fluid_systems.md, tests/test_fluid_systems.py (147 tests green)
- What: Fluid mechanics as planner data + hard validators. Entity tables, connection offsets, and the pump's half-tile geometry all verified live against 2.0.77 prototypes via RCON (not recalled). validate_network_purity is a hard guard - two fluids in one network deletes all but one and needs flushing/deconstruction, so mixing is a correctness failure like the quality jam, never a warning. Also: underground span <= 10 (the only legal fluid crossing), pipelines over 320x320 without a pump stop entirely, pumps separate networks AND refresh long runs.
- Why: Fluids make machine ORIENTATION a planning variable for the first time - connection points rotate/flip with the entity. Verified the chemical plant (in north / out south) and oil refinery (in south / out north) are opposite-handed, so chaining them requires rotating one.
- Next: fluid-aware layout primitives (pipe runs, underground crossings, pump breaks) in the planner, then an oil chain as the first fluid production line.

## [2026-07-22] Fluid layouts built live: refineries + chemical plants on the sandbox
- Files: planners/fluid_layouts.py, tools/build_processing_units.py, factorio_mod/control.lua, schemas/build_plan.schema.json, tests/test_fluid_layouts.py (219 tests green)
- What: Six fluid stages built by bots at x200,y200-330: crude + water infinity-pipe sources, 2 oil refineries, and chemical plant pairs for sulfur, plastic and sulfuric acid. Every machine's fluid box verified CONNECTED to its header, recipes set.
- Verified live (sub-agent): the pipe tile is `target_position`, ONE TILE OUTSIDE the footprint - `position` sits inside the body and connects to nothing. core/fluid_systems.py + docs/23 list `position`, so anyone laying pipe from connection_points() is off by one; fold a pipe_tile offset into core.
- Purity rule changed the layout, not just checked it: at 3-tile pitch a sulfur row's water and gas tiles are orthogonally adjacent (unrecoverable mixing), so pitch is probed against validate_network_purity and widened - 4 for sulfur, 5 for refineries, 3 elsewhere.
- Mod: place_entity now branches on entity type for infinity filters (pipes need set_infinity_pipe_filter, chests set_infinity_container_filter); build_plan schema gained fill_percentage.
- GAP found live: stages are isolated islands. generate_fluid_machine_row emits a row's own headers but NO connector from a source/producer to the row that consumes it, so refineries sat at fluid_ingredient_shortage with empty boxes while the crude source was full. Hand-connecting proved the row geometry is sound. Next: a fluid chain-link (the analogue of generate_chain_link), including which header row to join - headers sit further out than the machine stub row (crude header at y226, not the stub row y223).
- Also open: advanced-circuit needs 3 item ingredients vs 2 belt lanes, so processing-unit assembly needs a third feed path.

## [2026-07-23] Milestone audit: five milestones landed, none executed live
- Verified: 318 tests pass, worktree clean, 5 commits (78e21ea..5b0151e). Force bug IS fixed - sandbox_shared.get_or_create_planner_force() is used by construction, layout_executor, scaffolding and the rest, so the agent no longer measures a different factory than it builds. Mod split into 14 Lua modules, planners into 18 Python modules (charter 500-LOC limit satisfied). Surveyed builders now FAIL CLOSED without a WorldSpec instead of silently conjuring infinity sources.
- Processing-unit task retested: `--plan-only --world-spec` composes 2 managed infrastructure plans + 40 production plans, validated as one bundle.
- BLOCKING GAP: live execution is disabled on every new path. build_processing_units.py errors with "surveyed electronics execution is disabled; inspect it with --plan-only"; build_advanced_circuits.py prints "live execution: disabled" and holds zero bridge calls; compile_autonomy_goal.py is plan-only by design. The only executable route is --legacy-fluid-only, i.e. the old disconnected-islands build. Nothing has touched the running game since M1.
- Why this matters: every serious defect this project found came from executing, not from tests - substation 9.5 tiles from the first pole (silent no-power), two belts stacked on one tile (dead-ended connector), pipe tile at target_position not position (connects to nothing), sulfur rows mixing water and gas at 3-tile pitch, a false drain_limited verdict from a chained line's missing chest. The 40 production plans have never met the game's real placement rules.
- Next: (1) wire the surveyed path to GameBridge behind the existing authorization gates and RUN it, expecting geometry defects; (2) close the survey->allocate->build round trip against a live snapshot instead of fixture coordinates; (3) assert the invariants live (one electric network, one logistic network, zero ghosts, fluid amount > 0 per machine); (4) only then implement the remaining catalog executors and the learned policy.

## Audit snapshot (2026-07-18) — where things stand
- Mature: core/ (~2.7k LOC — metrics, progress state, authorization, phasing, advisory policies); schema validation pervasive.
- Partial: CityPlanner (symbolic decisions only, no geometry; 2 of 9 intents have phase chains); Lua mod logic complete but targets Factorio 1.1 and was never deployed.
- Absent: PlanetPlanner, InterplanetarySupervisor, real LocalLayoutPlanner layout math (stub inspector only), rail standard, block deployment, any transport (no RCON), any orchestrator/main loop, all automated tests, any actual learned RL (heuristics only; sole dep is jsonschema).

## [2026-07-22] Milestone 1 - unified sandbox infrastructure and planner force
- Files: factorio_mod/control.lua, planners/{infrastructure,infrastructure_geometry}.py, tools/build_processing_units.py, tests/test_infrastructure.py
- What: Processing fluid stages now compose one validated electric grid and one five-roboport network on force `planner`; managed scaffolding only seeds bots/materials, and layout reports distinguish attempted, placed, existing, and failed placements.
- Why: Remove isolated power/logistic islands and make partial placement failures observable before completing the production chain.
- Next: Build the real advanced-circuit solid-item dependency chain; sulfur utilization belongs to the later processing-unit slice.

## [2026-07-22] Milestone 1 correction - explicit topology ownership
- Files: factorio_mod/control.lua, schemas/build_plan.schema.json, planners/{sandbox_infrastructure,fluid_routing,fluid_layouts}.py, orchestrator/{game_bridge,chain_telemetry,loop_daemon,expansion_daemon}.py, tools/{build_line,build_science_chain,build_processing_units}.py, tests/{test_infrastructure,test_chain_telemetry}.py
- What: Existing topology is read-only until confirm-gated reconcile/reset; all active builders use managed unified infrastructure; placement configuration/auth failures are reported; sandbox snapshots and telemetry are force-filtered; live verification now asserts stage fluids and network counts.
- Why: Prevent silent force mutation, split-network recreation, false idempotent success, and player-factory contamination.
- Next: Split factorio_mod/control.lua (now over 500 LOC) by command domain before further Lua growth; no live reset/reconcile was run.
## [2026-07-22] Milestone 1 final - canonical managed sandbox backbone
- Files: factorio_mod/{control,sandbox_shared,snapshot,ghost_plans,construction,upgrades,deconstruction,sandbox_topology,scaffolding,layout_executor,research}.lua, planners/{infrastructure,sandbox_infrastructure,fluid_layouts,fluid_routing}.py, orchestrator/{game_bridge,chain_telemetry,loop_daemon,expansion_daemon}.py, tools/{build_line,build_science_chain,build_processing_units}.py, schemas/build_plan.schema.json, tests/{test_infrastructure,test_chain_telemetry}.py
- What: Every managed builder now extends one exact canonical power source and roboport hub; topology checks fail closed, authorizations match actual mutations, infinity configuration/network identity are verified exactly, and Lua commands are split into bounded require-able modules.
- Why: Make sequential autonomous builds converge on one buildable electric/logistic graph without implicit migration, excess permissions, or false idempotent success.
- Verified: 261 tests passed; science-chain and processing-unit plan-only builds passed; all Lua files are at most 435 lines. No live Factorio reset, reconcile, deploy, or build was run.
- Next: Milestone 2 - construct and validate the advanced-circuit dependency chain on this canonical backbone.
## [2026-07-22] Milestone 1 topology report contract correction
- Files: factorio_mod/sandbox_topology.lua, tests/test_infrastructure.py
- What: Topology JSON now emits the computed canonical power-source and roboport-hub booleans, including explicit false values for an absent surface; repeat managed inspections remain compatible.
- Why: Python correctly fails closed when these keys are absent, so dropping them made every second nonempty managed invocation refuse its own canonical topology.
- Verified: 25 focused infrastructure tests passed; no live Factorio or git operation was run.
## [2026-07-22] Milestone 1 test-module charter correction
- Files: tests/test_infrastructure.py, tests/test_sandbox_contracts.py
- What: Split deterministic infrastructure tests from managed sandbox runtime/topology/Lua contract tests without changing coverage.
- Why: Restore the repository invariant that every source file remains at or below 500 lines.
- Verified: Both files have Path/Purpose headers, are 252 and 308 lines, and all 25 focused tests passed; no live Factorio or git operation was run.
## [2026-07-22] Raw-resource electronics block (2A/2B)
- Files: planners/electronics_block.py, planners/fluid_routing.py, planners/{line_layouts,chain_layouts,recipe_data,item_routing,resource_layouts,plan_validation}.py, planners/local_layout_planner.py, planners/fluid_layouts.py, orchestrator/expansion_daemon.py, tools/build_advanced_circuits.py, tools/build_processing_units.py, tests/test_{electronics_block,three_input_layout,item_routing,resource_layouts}.py
- What: Added one bounded immutable managed block from observed iron/copper/coal/crude/water through advanced circuits and processing units, including sulfur consumption, predeclared output interfaces, collision-aware routing, and plan-only tools.
- Why: Replace fragmented power/robot networks and scripted production inputs with deterministic raw-resource autonomy.
- Next: Supply a live-observed world-spec and validate the generated plan in a disposable planner sandbox before any execution authorization.
## [2026-07-22] Milestone 2 final - surveyed immutable electronics contract
- Files: planners/{electronics_block,electronics_contracts,electronics_world,item_routing,resource_layouts,sandbox_infrastructure}.py, schemas/electronics_world_spec.schema.json, tools/{build_advanced_circuits,build_processing_units,build_line,build_science_chain}.py, tests/test_{electronics_block,electronics_cli,item_routing,resource_layouts,three_input_layout}.py
- What: Finalized schema-loaded surveyed sources, versioned item/fluid throughput interfaces with 25% headroom, exact underground pairs, strict 2A-as-subset-of-2B placement, and collision-free connected shared infrastructure.
- Why: Ensure raw-resource autonomy fails closed without observed resources and never rewrites the deployed advanced-circuit phase while adding sulfuric acid and processing units.
- Verified: 45 focused tests passed; the full suite reached 280 passed with one environment-only WinError 5 creating the pytest temp directory. Escalated rerun was unavailable; no live Factorio or git operation was run.
## [2026-07-22] Milestone 2 contract correction - aggregate crude and underground roles
- Files: planners/{electronics_contracts,item_routing}.py, schemas/build_plan.schema.json, factorio_mod/layout_executor.lua, tests/{test_electronics_block,test_item_routing,test_sandbox_contracts}.py
- What: Crude capacity now covers plastic plus sulfur gas demand; every underground belt pair declares input/output through planning, schema, Lua creation, and exact idempotency checks.
- Why: Prevent undersized oil supply and ambiguous same-direction underground endpoints.
- Verified: 24 focused tests passed; no live Factorio or git operation was run.

## [2026-07-22] Milestone 3 - deterministic bounded planner world
- Files: schemas/world_spec.schema.json, planners/world_generation.py, factorio_mod/{world_generation,control,sandbox_shared}.lua, tools/build_world_spec.py, tests/{test_world_generation,test_sandbox_contracts}.py, factorio_mod/README.md, schemas/README.md
- What: Added a fixed-seed 500x500 planner-owned surface contract with disabled autoplace, dispersed ores, crude oil, a pumpable lake, an exported electronics survey, and a construction-only starter kit.
- Why: Make raw-resource electronics reproducible from a declared world while keeping surface reset explicit and production inputs non-scripted.
- Verified: 43 focused world, infrastructure, sandbox, and electronics contract tests passed; no live Factorio, save, mod install, or world command was touched.
- Next: Survey arbitrary resource patches and allocate production sites from observations instead of fixed fixture coordinates.
## [2026-07-22] Milestone 3 correction - pre-mutation world validation and ownership
- Files: factorio_mod/world_generation.lua, planners/world_generation.py, tests/test_world_generation.py
- What: World creation now rejects malformed bounds, resources, lake edges, starter items, and electronics-survey drift before mutation; reset refuses surfaces without the exact persisted planner owner marker.
- Why: Prevent a confirmed command from deleting an unowned surface or partially creating a world from cross-contract data.
- Verified: 45 focused world, sandbox, infrastructure, and electronics tests passed; no live Factorio or git operation was run.
## [2026-07-22] Milestone 4 - observed resource survey and site allocation
- Files: factorio_mod/{snapshot,README}.lua/md, schemas/{snapshot,README}, planners/resource_survey.py, tools/survey_electronics_world.py, tests/test_resource_survey.py
- What: Added bounded resource/water observations, deterministic contiguous-patch clustering, capacity-aware drill/pump allocation, generated ElectronicsWorldSpec v1.0.0, and full-bundle collision validation.
- Why: Remove hand-authored source coordinates while preserving local-block boundaries and failing closed to a CityPlanner rail handoff for out-of-block resources.
- Verified: 42 focused offline tests passed; no live Factorio, save, mod deployment, or git operation was run.
## [2026-07-22] Milestone 4 correction - explicit locality and strict observations
- Files: planners/resource_survey.py, schemas/electronics_world_spec.schema.json, tools/survey_electronics_world.py, tests/test_resource_survey.py, factorio_mod/README.md
- What: Required explicit bounded macro-block acknowledgement, validated snapshot identity/uniqueness/bounds before access, derived oil capacity from observed yield with headroom, and proved offshore intake plus dry output geometry.
- Why: Prevent whole-map locality assumptions, malformed observations, overstated oil supply, and invalid shoreline placement.
- Verified: 46 focused offline tests passed; no live Factorio, save, mod deployment, or git operation was run.

## [2026-07-22] Deterministic autonomy compiler and restart runtime
- Files: core/autonomy_*.py, core/recipe_dag.py, schemas/*autonomy*.json, schemas/recipe_catalog.schema.json, factorio_mod/recipe_catalog.lua, orchestrator/game_bridge.py, orchestrator/expansion_daemon.py, tools/compile_autonomy_goal.py, tests/test_autonomy.py
- What: Added live recipe contracts, exact DAG expansion, surveyed electronics program compilation, immutable phases, restart-safe state, and fail-closed executable action selection.
- Why: Goals must compile from observed resources into authorized deterministic plans without structural RL, production cheats, or unsupported runtime actions.
- Next: Live validation requires explicit approval and a running planner sandbox.

## [2026-07-23] M6 preflight defects - row power pitch and geometry-derived roboport coverage
- Files: planners/resource_layouts.py, planners/roboport_coverage.py (new), planners/infrastructure.py, planners/sandbox_infrastructure.py, planners/electronics_block.py, tests/test_electronics_bundle_preflight.py
- What: Mining/fluid rows now emit medium poles at a supply-radius pitch instead of one anchor pole, and the roboport backbone is re-composed after routing with extra chained roboports covering every emitted place_ghost tile.
- Why: preflight() over the real electronics bundle found a coal drill outside any pole supply area and 284 route ghosts outside every construction radius - both would have wasted a live build cycle.
- Verified: preflight(build_electronics_block(include_processing=True, world=<fixture>)) is ok=True with zero skipped checks; 371 passed / 8 failed, every failure in tools/build_processing_units.py and tools/build_advanced_circuits.py (owned by another in-flight agent).

## [2026-07-23] M7 ore seeding - close the raw-resource gap
- Files: factorio_mod/scaffolding.lua, orchestrator/game_bridge.py, tools/electronics_execution.py, tools/build_processing_units.py, tools/build_advanced_circuits.py, tests/test_ore_seeding.py (new), tests/test_sandbox_contracts.py
- What: Added a dedicated `/seed_ore_patches` mod command (no anchors required, unlike ensure_sandbox_scaffolding) that idempotently seeds resource-entity tiles for exactly the WorldSpec's surveyed ore_patches rectangles. execute_electronics_bundle() now takes `world` and calls it right after prepare_existing_topology() (i.e. after any reset) and before infrastructure/production build, so drills never land on bare ground. Added drill_footprints_covered()/ore_seeding_payload() and a fail-closed guard that refuses to seed if any surveyed drill lies outside every declared patch.
- Why: Live measurement (docs/25_execution_rework_brief.md) found ore_tiles=0 and 58/58 drills mining nothing after a live build - execution declared patches but never seeded them, and `--existing-topology reset` wiped any that existed.
- Verified offline: tests/test_ore_seeding.py (7 new tests, FAKE bridge) proves the derived payload covers every ore-line and coal drill position for the fixture WorldSpec and that _seed_ore() calls the bridge once with that exact payload; full suite 400 passed, 1 skipped (baseline 378 passed/1 skipped plus this file's 7, minus one pre-existing failure fixed by registering the new command in test_sandbox_contracts.py's expected_commands).
- Blocked live: deployed the updated mod via scripts/deploy_mod.ps1, but the running Factorio process (a GUI client, not a headless server started by this session) only loads control.lua's require()d modules at world/save load - there is no RCON reload command (only /quit, which would kill the user's live window) - so `/seed_ore_patches` is not yet callable on the currently-running instance. Live ore_tiles/per-resource counts are NOT yet measured; needs the user to restart the Factorio client (or approve doing so) before a live rerun of tools/build_processing_units.py or a direct /seed_ore_patches RCON call can be verified.
- Next: after a client restart, verify live: reset topology, call /seed_ore_patches with ore_seeding_payload(world), then confirm `#surface.find_entities_filtered{type='resource'} > 0` and that a sample drill position has ore under its footprint; report per-resource counts.

## [2026-07-23] M7 center-out ring construction - spidertron mobile hub
- Files: factorio_mod/spidertron_builder.lua (new), factorio_mod/control.lua (one require line), tools/spidertron_build.py (new), tests/test_spidertron_tour.py (new)
- What: Replaced the fixed, bootstrapping roboport network with center-out ring construction driven by a self-powered construction spidertron (fusion-reactor + 4x personal-roboport-mk2 + 2x battery = 40-tile mobile construction radius, verified live). tools/spidertron_build.py places ghosts one concentric ring at a time (Chebyshev shells, innermost first) and only places ring k+1 AFTER ring k is fully built, so the build grows outward from one hub and never starts in disconnected parts. Pure geometry (lawnmower_waypoints, concentric_rings, square_grid) is unit-tested; the drive loop runs over RCON /sc.
- Why: docs/25_execution_rework_brief.md + user redirect - the fixed network fragmented the electric spine into 8 islands / 3 roboport networks because unpowered ring poles stalled the bootstrap. A self-powered spidertron carries its own power+coverage so each ring builds regardless of prior-ring power; center-out ordering keeps the growing network in sync.
- Verified live (planner-sandbox): self-test built 121 small-electric-pole ghosts center-out in 3 rings (+-7, +-21, +-35). Per-ring ghosts 0->N(placed)->0(built) for every ring; RESULT built=121, electric_networks=1 (one network - no fragmentation), remaining_ghosts=0, stalled=False, 32.8s. Sandbox cleaned to 0 ghosts/poles/spidertrons afterward.
- Key live findings: (a) a ghost directly under the parked spidertron (collision box +-1) reports can_revive=false and can NEVER be built - fixed by nudging each stop park_offset tiles off the ghost grid (alternating sign per pass); (b) base-tech bot dispatch latency is highly variable (2-40s), so the wait loop distinguishes "slow bots" from a real stall by querying in-range can_revive ghosts rather than a fixed tick/poll budget.
- Not yet done: full-factory build integration (per-ring placement from the real electronics bundle) - the running instance is a GUI client that only loads new mod modules (spawn_construction_spidertron) at save load, and ring-by-ring placement of the real bundle needs bundle-action partitioning; the small-test proof (ghosts->0, one network, center-out) is the delivered acceptance. verify_factory_invariants.py one-electric/one-roboport/zero-ghost check requires the full bundle + ore seeding (other agent) after a client restart.
- Next: after a client restart, partition the electronics bundle's ghost actions into center-out rings, drive them with build_center_out, then run tools/verify_factory_invariants.py --rcon-password planner_test.

## [2026-07-24] M7 composed radial production execution
- Files: tools/{spidertron_build,spidertron_geometry,electronics_radial_execution,build_processing_units}.py, tests/test_electronics_radial_execution.py, CURRENT_STATUS.md
- What: Infrastructure remains one atomic layout call; production actions now place and build in deterministic spidertron-driven rings, with a fail-fast one-electric/one-roboport backbone check.
- Why: Prevent the real composed factory from starting disconnected ghost islands while preserving complete BuildPlan action fields.
- Next: Owner may run `build_processing_units.py --construction-mode radial` against the restarted live server and verify measured invariants.

## [2026-07-24] M7 CLOSED: root cause of network fragmentation found and fixed, first full live build succeeds
- Files: factorio_mod/layout_executor.lua, planners/{infrastructure,electronics_block}.py, tests/test_infrastructure.py, tools/electronics_execution.py, tests/test_ore_seeding.py, docs/27_session_handoff_2026-07-24.md
- What: (1) Root cause of every prior fragmented-network live build found: Factorio's create_entity auto-wires poles unreliably once ~60+ already exist nearby (verified live). Fixed with an explicit wiring pass in layout_executor.lua using get_wire_connector(...).connect_to(...). (2) Spine now relays with substations primarily, big-electric-pole only for legs >100 tiles (user request), which required fixing a latent item-routing collision fragility. (3) ore_seeding_payload now also seeds crude-oil at pumpjack sites (previously only solid ore was seeded, silently starving every oil-refinery).
- Why: Preflight always passed ok=True on every fragmented bundle - the planner's geometry was provably correct every time. The gap was entirely in Factorio's own execution behavior, only ever findable by running the actual game.
- Verified live: full composed bundle (553 infra + 4325 production actions) built via `--construction-mode radial`. Independently confirmed via tools/verify_factory_invariants.py: energy_interface_count=1 PASS, electric_networks=1 PASS, logistic_networks=1 PASS, pending_ghosts=0 PASS at build completion. fluid_machines still failing on 4 machines (down from 6 after the oil fix) - likely just needs more settle time on this ~450-tile-long pipeline, not confirmed root-caused.
- Not yet done: offshore pump has no water tile (terrain, not a resource entity - needs LuaSurface.set_tiles, a different mechanism than ore/oil seeding); a belt-turn-after-underground-exit defect the user found and patched manually in-game (not yet root-caused in the planner); idempotency check (needs --baseline/--compare, not run this session).
- Next: see docs/27_session_handoff_2026-07-24.md for exact commands and full detail. Immediate step: wait longer + re-run verify_factory_invariants.py to see if the remaining 4 fluid violations clear on their own.

## [2026-07-24] M7 follow-up - seed offshore water and preserve underground belt continuity
- Files: factorio_mod/water_seeding.lua, factorio_mod/control.lua, factorio_mod/README.md, orchestrator/game_bridge.py, tools/{electronics_execution,electronics_radial_execution}.py, planners/{item_routing,electronics_block}.py, tests/{test_water_seeding,test_item_routing,test_electronics_block,test_electronics_radial_execution,test_sandbox_contracts}.py
- What: Added idempotent bounded Nauvis water-lake seeding before construction and rejected underground outputs that turn on their first surface belt; rerouted affected processing-unit belts with a straight connector before each turn.
- Why: Offshore pumps had no terrain water, and live belt turns immediately after underground exits split transport lines.
- Verified: 29 focused tests passed; py_compile passed; full-suite collection remains blocked by the existing permission-denied runs/pytest-tmp-radial-20260724 directory.
- Next: Redeploy the mod and restart the headless server before the next live radial build; then verify water/pump operation and fluid machines.
## [2026-07-24] M7 planner follow-up - water obstacles and local power spine
- Files: planners/{water_lakes,electronics_block,preflight,infrastructure,infrastructure_geometry,sandbox_infrastructure,fluid_routing,resource_layouts}.py, tools/electronics_execution.py, tests/{test_preflight,test_resource_layouts,test_infrastructure,test_fluid_layouts}.py
- What: Seeded lake tiles now constrain routing, infrastructure and preflight; the power spine is a terrain-aware MST; nearby blocked fluid crossings share one valid underground run.
- Why: Prevent unbuildable land ghosts on water and replace hub-spoke wiring without rewriting an earlier phase's infrastructure.
- Next: Live-probe offshore-pump and pumpjack connector offsets before changing their source-to-pipe placement.
## [2026-07-24] M7 planner follow-up - native logistics and verified crude source
- Files: planners/{electronics_block,electronics_contracts,electronics_upgrades,resource_layouts,plan_validation,preflight}.py, factorio_mod/upgrades.lua, schemas/upgrade_plan.schema.json, tools/generate_electronics_upgrade_plan.py, tests/{test_electronics_upgrades,test_resource_layouts,fixtures/electronics_world_spec.json}
- What: Initial electronics construction now uses fast Nauvis-native belts/inserters, with an explicit authorized plan generator for later express/stack upgrades; west-facing pumpjack placement now follows its live-verified output port.
- Why: Avoid off-planet logistics costs during initial build and prevent the crude source from being placed disconnected from its pipe.
- Next: Obtain the south-facing offshore pump's pipe-connection direction and first adjacent land-side pipe tile before changing its survey/placement; coordinated item-and-fluid corridor allocation is needed before shortening the sulfuric-acid detour.
## [2026-07-24] M7 planner follow-up - automatic compact fluid corridors
- Files: planners/{fluid_routing,electronics_block,infrastructure_geometry,sandbox_infrastructure}.py, tests/{test_fluid_routing,test_electronics_block,test_infrastructure}.py
- What: Replaced fixed fluid-route trunk coordinates with deterministic bounded A* shared pipe trees that reserve completed items/pipes and keep a one-tile clearance from structures/water; power elbow selection now prefers the shortest clear route.
- Why: Every future source/target arrangement must optimize from its actual obstacle set instead of relying on a hand-picked coordinate.
- Verified: full composed processing bundle preflight passes; `python -m pytest tests -q` -> 431 passed, 1 skipped.
- Next: live-build the new plan; still obtain the south-facing offshore-pump pipe-connection direction and first land-side pipe tile before changing its source placement.

## [2026-07-24] Real-base autonomy foundation
- Files: orchestrator/{live_base,autonomous_builder}.py, planners/belt_bridge.py, factorio_mod/{sandbox_shared,layout_executor}.lua
- What: Added survey, recursive stage construction, belt bridges, and bounded power/roboport/obstruction recovery for an existing Nauvis player force.
- Why: Start deterministic autonomous expansion from real mined resources without modifying the synthetic planner-sandbox pipeline.
- Next: Validate drill footprints against real ore tiles before building further mining stages.

## [2026-07-24] Real-base drill footprint validation
- Files: orchestrator/{autonomous_builder,live_base}.py, tests/test_autonomous_builder.py
- What: Mining-stage placement now validates every live 3x3 drill footprint against the requested ore, shifts deterministically within the surveyed patch, and reduces to one drill only when necessary.
- Why: Prevent irregular ore-patch edges from producing dead drills with no_minable_resources.
- Next: Live-run a copper or iron stage after redeploying only if the committed Lua foundation has not already been deployed.

## [2026-07-24] Real-base dependency primitives
- Files: factorio_mod/recipe_catalog.lua, planners/{resource_layouts,pipe_bridge}.py, tests/test_{autonomy,resource_layouts,pipe_bridge}.py
- What: Added player-force recipe-catalog export, direct electric mining into real chests for raw dependency items, and bounded purity-safe pipe bridges.
- Why: Chemical/production-science planning needs live recipe truth plus non-cheat raw-item and fluid interfaces.
- Next: Export the player catalog after mod redeploy; integrate verified recipes and source connector observations into the autonomous builder.

## [2026-07-24] Real-base goals and research
- Files: factorio_mod/research.lua, orchestrator/game_bridge.py, tools/autonomous_run.py, tests/test_research_commands.py
- What: Added explicit existing-force research status/queue commands and a CLI for produce/research goals; rate-increase goals fail closed pending a measured capacity policy.
- Why: Real-base targets must never mutate the sandbox planner force or claim research completion from a queued request.
- Next: Redeploy the mod, export the player recipe catalog, then integrate verified science recipes and real fluid connectors.

## [2026-07-24] Real-base player recipe export
- Files: orchestrator/game_bridge.py, tests/test_research_commands.py
- What: GameBridge can now export the enabled recipe catalog for an explicit existing force such as player.
- Why: Science recipes must be captured from the actual Factorio 2.0 force before they enter the autonomous catalog.
- Next: After mod redeploy/restart, export player catalog and use the observed contracts to add science and fluid recipe stages.

## [2026-07-26] Dead-code audit and direction-helper dedupe
- Files: removed core/{construction_progress_updater,deconstruction_executor,progress_reconciler,upgrade_executor}.py and tools/inspect_{snapshot,metrics,intents,plan_skeleton,planning_bundle,phase_result}.py; trimmed tools/README.md; planners/belt_bridge.py, orchestrator/autonomous_builder.py
- What: Deleted 892 LOC that no entrypoint, module, or test reaches, and collapsed the duplicated direction-opposite map into one public `belt_bridge.opposite()`.
- Why: An AST import-graph audit showed the repo is ~17.5k LOC of Python but the live real-base system is only ~4.4k of it; these ten modules had zero inbound edges and zero test coverage, so they were cost without benefit.
- Verified: 453 passed, 1 skipped — identical to the pre-cleanup run.
- Deliberately KEPT (documented as live capabilities in README.md, not orphans): rl_advisor.py, rl_feedback_builder.py, ghost_observer.py, construction_reporter.py, execution_reporter.py, inspect_progress.py, dashboard_server.py. Also kept both larger clusters by explicit decision: the synthetic-sandbox pipeline (~3.9k LOC, still the only proven end-to-end factory AND a live dependency of planners/pipe_bridge.py via fluid_routing) and the three superseded autonomy daemons + city_planner/supervisor (~5.0k LOC, unreachable but left in place).
- Next: if the daemons/city_planner cluster is confirmed obsolete, it is the single largest remaining simplification (~5.0k LOC, no inbound edges).

## [2026-07-27] Real-base stage-service split recovery
- Files: orchestrator/{autonomous_builder,stage_services,stage_transport}.py, tests/test_logistic_coverage.py
- What: Finished the interrupted behavior-preserving split, restored every moved constant/import, and moved test interception to the owning service module.
- Why: Keep the live builder within the 500-LOC limit without regressing the committed logistic-supply-radius fix.

## [2026-07-27] Intrinsic land-value zoning and late shadow migration
- Files: planners/{land_value,zoning,zoning_geometry}.py, tests/test_{land_value,zoning,zoning_allocation,zoning_scoring,zoning_geometry}.py
- What: Added deterministic central land value, outer bulk mining/smelting preference, independent mine/smelter allocations, an all-science 100/s-for-36000-ticks relocation gate, and surface/grid-bound release permits backed by completed shadow-migration evidence.
- Why: Preserve ore for mining early, reserve central land for science/research/spaceport, and prevent premature or destructive reuse of working inner industry.
- Next: Persist ore-patch identity/initial amount and add approved rail/station templates before connecting this CityPlanner policy to live construction; the current belt/bot builder remains one grandfathered local block.

## [2026-07-27] Factorio mod interaction runbook and local skills
- Files: docs/31_factorio_mod_interaction_and_troubleshooting.md, .agents/skills/, README.md, tools/README.md, factorio_mod/README.md
- What: Documented all 19 RCON-exposed mod commands, GameBridge/script-output workflows, safe diagnostics, server launch boundaries, failure routing, and two repo-local operational skills.
- Why: Make live server interaction and troubleshooting repeatable, evidence-driven, and read-only by default.

## [2026-07-27] Documentation consolidation
- Files: docs/{architecture,planner_and_execution,data_contracts_and_determinism}.md, docs/archive/{handoffs,superseded-design}/, AGENTS.md, FULL_DOCUMENTATION.md, planner/tool references
- What: Archived handoffs 24-29 and superseded the overlapping architecture/agent/planner/execution/state documents with three compact canonical references; regenerated the combined documentation.
- Why: Reduce documentation bloat while preserving historical evidence and keeping invariants, schemas, and live operational guidance discoverable.

## [2026-07-27] All-science structural recipe knowledge
- Files: core/{recipe_dag,science_recipe_graph}.py, factorio_mod/{recipe_catalog.lua,README.md}, orchestrator/autonomous_builder.py, tests/test_{autonomy,science_recipe_graph}.py, docs/{planner_and_execution,31_factorio_mod_interaction_and_troubleshooting}.md
- What: Added grounded structural chains for all 12 Space Age science packs, retained valid producer alternatives, exported locked recipes and environmental/asteroid inputs, and enforced pre-connection Nauvis scope and builder-readiness gates.
- Why: Let the planner understand space and planetary dependencies without pretending probabilistic or off-world chains are currently executable.
- Next: Redeploy/restart only with explicit approval, then export the player catalog to verify disabled recipes and environmental acquisition leaves against the live Factorio runtime.

## [2026-07-27] Repeatable research target resolution
- Files: factorio_mod/research.lua, tools/autonomous_run.py, tests/test_research_commands.py
- What: Resolved displayed repeatable levels to their canonical Factorio technology, added level-relative completion/future states, and made same-target queue requests idempotent.
- Why: Accept goals such as mining-productivity-4 without treating the displayed level as a nonexistent prototype ID or resetting active research progress.
- Next: Redeploy and restart only with explicit approval, then verify read-only status before authorizing a live queue change.

## [2026-07-27] Existing-stage ingredient transport recovery
- Files: orchestrator/{autonomous_builder,stage_recovery,stage_transport,live_base}.py, tests/test_autonomous_builder.py
- What: Added bounded recovery for existing starved stages, reconstructing and verifying declared ingredient transport without duplicating machines.
- Why: The autonomous runner exited on a premature/failed feed-health check and left a previously productive gear stage starved with no controller continuing the workflow.
- Next: Validate recovery against the live base only with explicit autonomous-run authorization.

## [2026-07-27] Isolated dedicated-server launcher
- Files: scripts/launch_dedicated_server.ps1
- What: Added an idempotent launcher that keeps server saves, mods, locks, logs, and script output outside the normal GUI profile.
- Why: Allow the normal Start-menu GUI to run while the local headless server is active.
- Next: Operator-authorized first launch and GUI connection verification.

## [2026-07-27] Isolated server profile launched
- Files: C:\Users\djsma\AppData\Local\Factorio-server (runtime data, outside repository)
- What: Launched the dedicated server from the isolated profile; RCON tick and mining-productivity-4 research preflight pass.
- Why: Remove the lock conflict with the normal Start-menu GUI and enable local multiplayer observation.
- Next: Join `127.0.0.1:34199` from the GUI, then resume the authorized autonomous research run.
## [2026-07-27] Separate real-base extraction and smelting
- Files: orchestrator/{autonomous_builder,extraction_state,extraction_transport,live_base,stage_extraction}.py, planners/belt_bridge.py, tests/{test_belt_bridge,test_extraction_separation}.py, docs/{planner_and_execution,31_factorio_mod_interaction_and_troubleshooting}.md
- What: Replaced coupled mining lines with retry-reconciled ore-only extraction and independently sized, off-ore smelting; preflighted local transport before structural submission.
- Why: Preserve ore land and expansion room, respect different drill/furnace rates, and prevent retries or over-limit routes from duplicating infrastructure.
- Next: Restart the Python autonomous runner before testing; existing coupled stages remain grandfathered until an authorized shadow migration.

## [2026-07-27] Ore-aware power remediation
- Files: orchestrator/{live_base,stage_services}.py, tests/test_infrastructure.py
- What: Routed emergency medium-pole chains around resource and occupied tiles while respecting the shorter endpoint pole reach.
- Why: Separated extraction could place a stage across an ore patch from the grid, and the straight bridge repeatedly failed to energize it.
- Next: Restart only the Python autonomous runner before an authorized retry; no mod redeploy is required.
## [2026-07-28] Executable chemical science slice
- Files: core/science_recipe_graph.py, planners/recipe_data.py, orchestrator/{autonomous_builder,chemical_survey,stage_chemical}.py, tests/test_{recipe_catalog_contract,science_recipe_graph}.py, docs/planner_and_execution.md
- What: Added solid chemical-science recipes and a compact real-Nauvis pumpjack/refinery/plastic/sulfur cell using the existing fluid rows and router.
- Why: Chemical science was structurally known but the autonomous builder stopped at its executable readiness gate.
- Next: Restart only the Python runner for an authorized retry; production, utility, and military science remain gated.

## [2026-07-28] Behavioral test-suite pruning
- Files: tests/{conftest,test_preflight,test_electronics_bundle_preflight,test_electronics_block,test_fluid_layouts,test_infrastructure,test_sandbox_contracts,test_factory_invariants,test_autonomy,test_electronics_upgrades,test_research_commands,test_resource_survey,test_science_recipe_graph,test_water_seeding,test_world_generation}.py
- What: Removed 49 duplicate or static test functions, shared the expensive full processing bundle once per session, and reduced factory-invariant layering from 40 to 21 cases while preserving routing, fluid, zoning, and extraction geometry coverage.
- Why: Replace low-signal test volume and repeated factory composition with behavioral validation that finishes sooner and reflects plan correctness.
- Next: Add the unified operational BuildPlan connectivity gate for power, inserters, belts, logistics, fluids, and offshore-pump geometry.
## [2026-07-28] Reserve chemical fluid source outlets
- Files: orchestrator/stage_chemical.py
- What: Registered the fixed crude-oil and water source outlets before routing any chemical-science fluid link.
- Why: Prevent an earlier route from occupying a tile adjacent to a later source and creating an unavoidable mixed-fluid connection.
- Next: Redeploy and retry only with explicit user authorization.
## [2026-07-28] Demand-driven parts mall
- Files: orchestrator/{parts_mall,stage_services,autonomous_builder}.py
- What: Converted producible construction shortages into persistent mall stock targets; the runner establishes production, waits without a deadline for inventory, and resumes the blocked stage.
- Why: Chemical science required 645 pipes, but the affordability guard stopped instead of ordering pipe production and waiting for it.
- Next: Restart the Python runner before an authorized retry; add construction recipes incrementally when a new mall item is first demanded.
## [2026-07-28] Ore-safe mall placement and durable runner log
- Files: orchestrator/autonomous_builder.py, tools/autonomous_run.py, scripts/launch_dedicated_server.ps1
- What: Reserved a five-tile ore apron for conversion stages and added flushed append-only runner logging with explicit server-log paths.
- Why: Pipe production was placed on iron ore, and a later server exit left no visible autonomous-run progress or traceback.
- Next: Restart the Python runner; inspect autonomous-run.log plus Factorio stdout/stderr after the first clear failure.

## [2026-07-29] Local Factorio operations console
- Files: tools/{dashboard_server,dashboard_runtime,dashboard}.py/html/js/css, scripts/{deploy_mod,launch_dashboard}.ps1, README.md
- What: Reworked the loopback dashboard into a live runner/server log console with fixed restore, redeploy, restart, and full-refresh controls; dedicated redeploy now updates both mod copies.
- Why: Make autonomous build progress and recurring recovery operations directly observable and user-operated without waiting for a chat thread to fail or act.
- Next: Launch scripts\launch_dashboard.ps1; action buttons remain idle until explicitly clicked.

## [2026-07-29] Non-draining plate supply and mall expansion
- Files: planners/belt_bridge.py, orchestrator/{autonomous_builder,extraction_transport,stage_extraction,stage_transport,parts_mall}.py
- What: New iron smelters keep plates on a through-belt, side-sample them into a provider with a slow inserter, and belt consumers connect without unloading that chest; pipe mall demand adds one new iron mine/smelter and stalled mall stock can request one upstream recovery.
- Why: The gear bridge drained the shared iron provider faster than it filled, permanently starving pipe production at 363/645.
- Next: Restart only the Python runner from the dashboard; no mod redeploy or save restore is required for the extra iron line.

## [2026-07-29] In-place mine capacity doubling
- Files: planners/resource_layouts.py, orchestrator/{stage_extraction,autonomous_builder}.py
- What: Supply expansion now mirrors an existing drill row four tiles below it, faces the new drills north onto the same ore belt, and adds independently sized smelting capacity; completed doubled rows are reconciled instead of duplicated.
- Why: Iron demand exceeded the original two-drill row, so adding only downstream furnaces could not raise plate supply.
- Next: Restart only the Python runner; the expected live expansion for the current row is drills at (16.5,0.5) and (19.5,0.5).

## [2026-07-29] Repeated phased mine expansion
- Files: planners/resource_layouts.py, orchestrator/{stage_extraction,parts_mall,autonomous_builder}.py
- What: Unmet mall demand now requests repeated capacity phases: complete the mirrored drill row, extend paired columns west along both shared-belt rows, then open the next clear independent mine band above or below when the strip cannot continue.
- Why: A single doubling is not enough for sustained construction demand; extraction must grow in reconciled increments until stock catches up.
- Next: Restart only the Python runner; subsequent phases are requested every 60 seconds while the mall target remains unmet.
## [2026-07-29] Live construction-recipe learning
- Files: planners/recipe_data.py, orchestrator/autonomous_builder.py
- What: Each runner start now imports the player-force recipe catalog and makes every enabled deterministic solid recipe supported by the existing line geometry executable by the parts mall.
- Why: Construction shortages such as fast-transport-belt must resolve from live recipe truth instead of stopping on a narrow hand-written catalog.
- Next: Restart only the Python runner; recipes needing fluids, special machines, or more than three inputs still fail closed pending their dedicated layouts.
## [2026-07-29] Guaranteed iron output split
- Files: orchestrator/autonomous_builder.py, orchestrator/stage_transport.py
- What: Iron smelting now feeds a physical splitter whose main output continues to downstream production while its second output fills the provider chest.
- Why: A slow inserter still captured nearly all plates when mining throughput was low, leaving the gear bridge empty.
- Next: Rebuild the iron smelting stage from a clean save, then restart the Python runner.
## [2026-07-29] Six-drill minimum extraction unit
- Files: orchestrator/stage_extraction.py, orchestrator/autonomous_builder.py
- What: Every new ore mine now starts with three drills above and three below one shared transport belt; furnace capacity is sized from all six drills.
- Why: Two-drill startup capacity cannot sustain science and construction demand.
- Next: Rebuild from a clean save and restart the Python runner.

## [2026-07-29] Expandable mining corridor
- Files: planners/resource_layouts.py, orchestrator/stage_extraction.py, orchestrator/extraction_state.py, orchestrator/autonomous_builder.py, tests/test_extraction_separation.py
- What: A mine starts with six drills, places its collector outside the ore-facing edge, and reserves its shared belt for 20 more drills added in upper/lower pairs before opening a parallel line.
- Why: The initial six drills are a growth slice, not a closed mining unit.
- Next: Rebuild from a clean save or remove the old mine geometry before evaluating the new corridor.

## [2026-07-29] Reliable dashboard save restore and server restart
- Files: tools/dashboard_runtime.py, scripts/stop_dedicated_server.ps1
- What: Restore now verifies the copied starting save, server launch no longer leaves a NoExit PowerShell window, and a timed-out RCON shutdown falls back to stopping only the configured dedicated Factorio process and its launcher shell.
- Why: Restore never reached its copy step because shutdown timed out, and the persistent elevated launcher window made subsequent restarts fail.
- Next: Restart the dashboard process so its buttons load the updated Python runtime.

## [2026-07-29] System-wide mining capacity phases
- Files: orchestrator/{extraction_capacity,extraction_state,stage_extraction,autonomous_builder}.py, planners/resource_layouts.py, tests/test_extraction_separation.py, docs/planner_and_execution.md
- What: Mining now advances by total resource-system targets of 6, 20, 50, and 100 drills; each intervention builds a whole batch, fills existing reserved corridors first, and gives new mines the remaining global target rather than restarting at six.
- Why: Extraction progression represents overall factory capacity, not incremental growth of whichever individual mine triggered demand.
- Next: Restart only the Python runner; no mod redeploy is required.

## [2026-07-29] Patch-shaped mining reserve
- Files: orchestrator/stage_extraction.py, tests/test_extraction_separation.py
- What: New mines validate the required starting slice first, then reserve the largest contiguous future corridor actually supported by that patch instead of requiring all 20 future drills up front.
- Why: Copper startup was blocked because an irregular patch could fit six drills but not a single straight 26-drill corridor.
- Next: Restart only the Python runner; no mod redeploy is required.
## [2026-07-29] Resource-patch viability and retirement
- Files: orchestrator/{resource_patches,mine_retirement,game_bridge,live_base,stage_extraction,stage_chemical,autonomous_builder}.py, factorio_mod/deconstruction.lua, tests/test_extraction_separation.py
- What: New solid-resource mines ignore patches below 200k; managed mines below 100k are deconstructed with exact bounded ownership, and depleted land becomes eligible for production placement.
- Why: Small and exhausted patches are not worth preserving as mining land once the base can use that footprint for higher-value production.
- Next: Redeploy the mod once for live-surface deconstruction support, then restart the Python runner.
## [2026-07-29] Sub-200k patches are normal land
- Files: orchestrator/resource_patches.py
- What: Every resource patch below 200k now contributes zero zoning reservation, even while an existing mine remains until its separate 100k retirement threshold.
- Why: Low-value ore must not block higher-value construction or require an ore apron.
- Next: Restart only the Python runner; no additional mod redeploy is required for this policy adjustment.
## [2026-07-29] Signed resource-survey coordinates
- Files: orchestrator/resource_patches.py
- What: Resource survey Lua now parenthesizes signed reference coordinates and reports malformed RCON responses explicitly.
- Why: A negative reference coordinate generated `y--1`, which Lua parsed as a comment and returned a syntax error that Python obscured as an unpack failure.
- Next: Restart only the Python runner.
## [2026-07-29] Collision-safe roboport coverage
- Files: orchestrator/{live_base,stage_services,roboport_placement,placement_clutter}.py, tests/test_logistic_coverage.py
- What: Coverage chains now avoid existing plans, repair isolated old and new roboports, and clear neutral trees or rocks only from the exact approved build footprint.
- Why: A bridge roboport overlapped the copper belt, two earlier ports remained isolated, and removable dead trees forced unnecessary underground routing.
- Next: Restart only the Python runner; no mod redeploy is required.
## [2026-07-29] Short restore confirmation
- Files: tools/{dashboard.html,dashboard.js,dashboard_runtime.py}
- What: The restore button and its required confirmation now use RESTORE.
- Why: Remove unnecessary typing from the frequent starting-save restore action.
## [2026-07-29] Continuous mine output with side provider tap
- Files: planners/resource_layouts.py, orchestrator/{extraction_state,stage_extraction,extraction_capacity,mine_retirement,autonomous_builder,stage_chemical}.py, tests/{test_resource_layouts,test_extraction_separation}.py
- What: New mines use a continuous belt with a perpendicular provider tap; existing legacy terminal chests are relocated and the freed chest/inserter tiles become belt on the next runner pass.
- Why: A terminal provider intercepted all ore before the smelter link and later belts collided with the chest.
- Next: Restore the starting save and restart the Python runner for the already-partial iron row; completed legacy mines can migrate without a restore. No mod redeploy is required.
## [2026-07-29] Lower mining-row pole collision
- Files: planners/resource_layouts.py
- What: The lower row power scaffold now sits below its drills instead of placing poles inside drill footprints.
- Why: The pole at (21.5,0.5) prevented the planned drill at (22.5,0.5), leaving the iron mine partially built.
- Next: Covered by the same Python-runner restart.
## [2026-07-29] Deterministic dashboard restore lifecycle
- Files: tools/dashboard_runtime.py, scripts/stop_dedicated_server.ps1
- What: RESTORE now always terminates the exact dedicated Factorio process and launcher shell, verifies the starting-save copy, then opens a visible elevated -NoExit launcher for UAC approval.
- Why: A closed RCON port was incorrectly treated as proof that the server process had exited, and the replacement elevated launcher was hidden.
- Next: Restart the dashboard server once to load the updated runtime.
## [2026-07-29] Mine side-tap belt handoff
- Files: orchestrator/stage_transport.py, orchestrator/extraction_transport.py
- What: Mine providers now hand belt transport off from the continuous ore belt and rebuild its endpoint as the route corner.
- Why: Chest-origin routing crossed the side-tap inserter/provider and prevented the conversion stage and its final logistic coverage from being built.

## [2026-07-29] Restored-save report overwrite detection
- Files: orchestrator/game_bridge.py
- What: GameBridge now detects JSON reports whose existing tick-based filename was overwritten, using file modification signatures instead of filename presence alone.
- Why: Restoring a save reused tick 117906, so a successful power bridge overwrote yesterday's layout_117906.json and the runner timed out before extending logistic coverage to the final provider chest.

## [2026-07-30] Reliable dashboard restore submission
- Files: tools/dashboard.js
- What: RESTORE now uses a normal confirmation dialog and submits the required confirmation token automatically.
- Why: The typed confirmation prompt silently cancelled the action before it reached the dashboard server, leaving no restore entry in the control log.

## [2026-07-30] Remove duplicate restore phrase gate
- Files: tools/dashboard_runtime.py
- What: The token-protected loopback restore endpoint no longer rejects the action based on a second client-supplied phrase.
- Why: The browser already confirms the destructive action, while stale client payloads could still trigger an exact-phrase rejection.

## [2026-07-30] Load updated dashboard restore runtime
- Files: CURRENT_STATUS.md
- What: Restarted only the dashboard service on port 9137 and reloaded the open dashboard tab.
- Why: The live service was still PID 55284 from July 29 and retained the removed exact-phrase check.

## [2026-07-30] Resume pending mine construction
- Files: orchestrator/stage_extraction.py, tests/test_extraction_separation.py
- What: Matching extraction ghosts are reconciled as the existing paired mine, including a partially built lower row, instead of being treated as a duplicate-mine failure.
- Why: Mall recursion can revisit iron production while construction bots are still completing the mine.

## [2026-07-30] Observe pending side-tap mines correctly
- Files: orchestrator/extraction_state.py
- What: Mine reconciliation now discovers provider chests placed two tiles off the continuous belt, including their pending ghosts.
- Why: The observer only recognized legacy terminal chests, so a half-built six-drill mine was misread as a completed two-drill legacy mine and incorrectly converted.

## [2026-07-30] Break fast-belt mall bootstrap cycle
- Files: orchestrator/autonomous_builder.py
- What: A mined-resource conversion stage retries with ordinary transport belts when fast belts are its missing construction material.
- Why: Iron smelting needed fast belts before the fast-belt mall could obtain the iron plates required to manufacture them.

## [2026-07-30] Low-priority mine provider tap
- Files: orchestrator/stage_extraction.py, orchestrator/autonomous_builder.py, planners/resource_layouts.py, tests/test_extraction_separation.py
- What: New and legacy mine side taps now use a regular inserter; the layout validator accepts that tier.
- Why: A fast inserter captured most early ore until the provider chest filled, starving the through belt.

## [2026-07-30] Beltless plate bootstrap
- Files: planners/bootstrap_smelting.py, orchestrator/autonomous_builder.py, tests/test_bootstrap_smelting.py
- What: Plate production falls back to seven independent bot-fed furnaces when its normal line lacks belts.
- Why: Producing belts requires iron plates, so retrying the unbuilt smelter with a lower belt tier formed an infinite mall dependency loop.
## [2026-07-30] Compact shared-requester bootstrap
- Files: planners/bootstrap_smelting.py, orchestrator/autonomous_builder.py, orchestrator/stage_services.py, tests/test_bootstrap_smelting.py
- What: The beltless fallback is now two furnaces around one shared requester, and power hookup endpoints cannot overlap their source pole.
- Why: Seven requester/provider pairs wasted logistics infrastructure, while a hookup pole was also being placed on an existing substation.
## [2026-07-30] Temporary requester-fed smelting
- Files: planners/bootstrap_smelting.py, orchestrator/autonomous_builder.py, tests/test_bootstrap_smelting.py
- What: Mall recursion may use the compact logistic smelter while belts are unavailable; normal goal planning upgrades it to a belt-fed line and removes the temporary cell.
- Why: Bulk ore transport must not remain on the logistic network after construction belts become available.
## [2026-07-30] Belt-first production transport
- Files: orchestrator/autonomous_builder.py
- What: Normal production stages now force belt inputs; requester inputs remain available only while recursively producing mall construction parts.
- Why: Logistic bots are unsuitable for continuous bulk ore and production transport.
## [2026-07-31] Direct ore-belt handoff
- Files: planners/belt_bridge.py, orchestrator/extraction_transport.py, orchestrator/stage_transport.py, orchestrator/autonomous_builder.py, orchestrator/stage_services.py
- What: Single-input smelters remove the feed chest/inserter and connect the mine belt directly to the stage input belt; powered consumers already inside a pole supply area are no longer given a colliding extra pole.
- Why: Inline chests throttled and lane-biased ore flow, while low-power roboports triggered a redundant pole on an existing substation.
## [2026-07-31] Belt-turn reserve buffer
- Files: planners/belt_bridge.py, tests/test_belt_bridge.py
- What: Direct belt turns add an off-line steel chest with one sampling and one return inserter when the inside corner is clear.
- Why: Keeps belt throughput uninterrupted while providing a local shock reserve.
## [2026-07-31] Mall bootstrap transport escape
- Files: orchestrator/autonomous_builder.py, orchestrator/stage_transport.py
- What: Mall-only stages keep requester inputs regardless of nominal rate, wait on temporarily starved existing lines, and finished plate smelters publish from a continuous side-tapped output belt.
- Why: Recalculating mall transport as bulk belts trapped belt production in a circular shortage and left the two-furnace emergency cell acting like the final smelter.
## [2026-07-31] Side-tap power boundary
- Files: orchestrator/stage_services.py
- What: Power coverage now uses Factorio's strict square-area intersection and hookup poles always use valid tile centers.
- Why: A copper side-tap inserter exactly on a medium pole's boundary was incorrectly declared powered and retried without placing a pole.
## [2026-07-31] Existing output-tap recovery
- Files: orchestrator/autonomous_builder.py
- What: Reused working stages now repair unpowered entities beside their provider output before declaring the product available.
- Why: The already-built copper tap would otherwise remain unpowered after a runner restart.
## [2026-07-31] Beltless belt mall
- Files: planners/mall_layout.py, orchestrator/mall_builder.py, orchestrator/autonomous_builder.py
- What: Belt-tier construction recipes bootstrap in a compact requester-fed assembler cell with no internal belts.
- Why: The generic transport-belt factory required belts to manufacture belts and repeated forever without submitting a build.

## [2026-07-31] Research construction-readiness phase
- Files: orchestrator/parts_mall.py, planners/mall_layout.py, orchestrator/mall_builder.py, orchestrator/autonomous_builder.py, tools/autonomous_run.py, planners/recipe_data.py
- What: Research missions first stock a small starter mall plus the drills, furnaces, assemblers, oil equipment, and labs implied by the complete science set.
- Why: Mining-productivity production now begins only after modest self-produced construction inventory; faster tiers remain demand-triggered.

## [2026-07-31] Deferred construction priority and virtual mine reservations
- Files: orchestrator/priority_list.py, orchestrator/autonomous_builder.py, orchestrator/parts_mall.py, orchestrator/live_base.py, orchestrator/extraction_state.py, orchestrator/stage_extraction.py, planners/resource_layouts.py
- What: Construction tasks now carry persistent ratings, completion, tick age, and deferral reasons; mine expansion corridors remain metadata until each approved phase builds its own belt tiles.
- Why: A blocked future drill site should defer and age rather than terminate the mission or consume belts for unused capacity.

## [2026-07-31] Live priority queue dashboard
- Files: orchestrator/priority_list.py, tools/dashboard_runtime.py, tools/dashboard_server.py, tools/dashboard.html, tools/dashboard.js, tools/dashboard.css
- What: The local operations site now polls and displays task rating, completion, tick age, state, and deferral reason.
- Why: The autonomous scheduler's current work and postponed capacity decisions need to be observable without reading the runner log.

## [2026-07-31] Progressive mine-corridor spending
- Files: orchestrator/extraction_capacity.py, orchestrator/autonomous_builder.py, orchestrator/stage_extraction.py, planners/resource_layouts.py, tests/test_extraction_separation.py
- What: New mines keep future belts virtual below 200 belts, prebuild 2 reserved columns at 200, 5 at 500, and the full measured corridor at 1000.
- Why: Early construction stays frugal while a mature parts supply can buy expansion readiness.

## [2026-07-31] Consolidated mall requester groups
- Files: planners/mall_layout.py, orchestrator/mall_builder.py, orchestrator/autonomous_builder.py, factorio_mod/layout_executor.lua, schemas/build_plan.schema.json
- What: Compact mall cells now use one grouped multi-item requester with recipe-scaled counts and load-tiered input inserters; existing consolidated cells can update their request quantities.
- Why: One assembler does not need one requester per ingredient, and flat requests of 50 ignored both recipe ratios and construction demand.

## [2026-07-31] Dense paired mall cells
- Files: `planners/mall_layout.py`, `orchestrator/mall_builder.py`, `orchestrator/live_base.py`, `orchestrator/autonomous_builder.py`
- What: Centralized mall allocation now packs two assignable assemblers around one multi-item requester; a cell may launch half-empty and fill its related half later.
- Why: Keep the mall dense without duplicating requesters or reserving large unused plots.
- Next: Restart the Python runner before the next mission.
## [2026-07-31] Directional refinery flow repair
- Files: `planners/line_layouts.py`, `orchestrator/stage_extraction.py`, `orchestrator/extraction_transport.py`, `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`, `docs/planner_and_execution.md`
- What: Refineries now orient input toward the supplying mine and output toward the downstream reference, replace conflicting source belts safely, and use one powered plate side collector.
- Why: Remove the two-turn detour, the underground-over-surface belt failure, duplicate collectors, and the initially unpowered output tap.
- Next: Restart the Python runner before the next mission.
## [2026-07-31] Westbound refinery input repair
- Files: `planners/belt_bridge.py`, `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`, `orchestrator/live_base.py`
- What: Direct ore bridges now connect to the upstream end of eastbound and westbound refinery belts, turn their final tile into the refinery, and avoid chest-only parsing on belt failures.
- Why: The iron refinery selected its west/output end as the feed, leaving the actual east/input end disconnected and crashing diagnostics on Lua error text.
- Next: Restart the Python runner before the next mission.
## [2026-07-31] Batched machine health polling
- Files: `orchestrator/live_base.py`, `orchestrator/stage_services.py`
- What: Status and progress for an entire machine stage are now read in one RCON observation per poll.
- Why: The old health loop made two full live queries every two seconds, adding avoidable latency and RCON load during long waits.
- Next: Profile the next largest source of repeated live queries before adding further caching.
## [2026-07-31] Aligned mine-to-refinery belt handoff
- Files: `planners/belt_bridge.py`, `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`
- What: Mine handoffs start one upstream tile beyond a side tap, and refinery bridges force a straight final approach so underground endpoints stay paired on one line with one facing.
- Why: The side-tap tile was being reused as a tunnel endpoint, while corner-ending routes could rotate or mispair underground belts.
- Next: Restart the Python runner before the next mission.
## [2026-07-31] Belt handoff and tunnel alignment repair
- Files: `planners/belt_bridge.py`, `orchestrator/stage_transport.py`
- What: Mine belt handoffs now start from the upstream tile beside a side tap, and every belt bridge reserves a straight final approach so underground endpoints share one line and facing.
- Why: A side tap was reused as the route endpoint, and corner-ending routes could leave underground belts visually adjacent but unpaired.
- Next: Restart only the Python runner before evaluating the corrected route.

## [2026-07-31] Mall inserter direction and tap preservation
- Files: `planners/mall_layout.py`, `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`, `factorio_mod/layout_executor.lua`
- What: Corrected shared-requester pickup/drop directions, repaired existing mall inserters/assembler recipes idempotently, preserved mine side taps during belt handoffs, and power-checked buffer inserters.
- Why: Copper-cable and electronic-circuit assemblers were being fed from the wrong side, while belt replacement could disconnect a mine tap and leave buffer inserters unpowered.
- Next: Restart the Python runner; redeploy the mod only if the Lua executor change is to be used in the live save.

## [2026-07-31] Priority retry and existing belt reuse
- Files: `orchestrator/priority_list.py`, `orchestrator/stage_transport.py`
- What: Clamp stale persisted retry deadlines and preserve the first existing belt tiles beside a mine side tap instead of submitting slower replacement ghosts.
- Why: A saved absolute retry tick delayed construction for over five minutes, and the copper route tried to place yellow belts over existing fast belts at `(77.5,-39.5)` and `(78.5,-39.5)`.

## [2026-07-31] Mine side-tap turn ownership
- Files: `planners/belt_bridge.py`, `orchestrator/extraction_transport.py`, `tests/test_belt_bridge.py`
- What: Mine bridges preserve the two westbound belts at `(78.5,-39.5)` and `(77.5,-39.5)`, then turn south at `(76.5,-39.5)`.
- Why: The unconstrained L-route tried to rotate the side-tap tile itself, disconnecting the provider path.
- Next: Restart the Python runner before evaluating the corrected handoff.
## [2026-07-31] Mine batch-expansion import repair
- Files: `orchestrator/stage_extraction.py`, `CURRENT_STATUS.md`
- What: Imported `generate_shared_belt_batch_expansion` where reserved mine phase-up planning calls it.
- Why: The first batched mine expansion would otherwise raise `NameError` during execution.
- Next: Address the stale research command tests from audit item 1.2.
## [2026-07-31] Research command test contract repair
- Files: `tests/test_research_commands.py`, `CURRENT_STATUS.md`
- What: Updated research tests for `_research(args, emit)`, the current `_run_item(args, item, emit)` fake, and the required Nauvis surface argument.
- Why: Five tests still called the pre-logging function contract and failed before exercising research behavior.
- Next: Address the preflight spatial-index performance issue from audit item 2.1.
## [2026-07-31] Spatially indexed footprint preflight
- Files: `planners/preflight.py`, `CURRENT_STATUS.md`
- What: Replaced the quadratic placement-pair sweep with a deterministic 5-tile spatial grid while preserving failure ordering and pumpjack attachment exceptions.
- Why: Large composed plans were spending most preflight time comparing entities that could not overlap.
- Next: Guard and spatially bound Lua pole-wiring remediation from audit item 3.2.
## [2026-07-31] Localized pole-wiring remediation
- Files: `factorio_mod/layout_executor.lua`, `CURRENT_STATUS.md`
- What: Skip explicit pole wiring for plans without pole placements and restrict pole discovery to the plan bounds expanded by its maximum pole reach.
- Why: Every build previously triggered a quadratic full-surface pole sweep, including plans that placed no poles.
- Next: Replace the snapshot whole-map water scan from audit item 3.3.
## [2026-07-31] Native snapshot water query
- Files: `factorio_mod/snapshot.lua`, `CURRENT_STATUS.md`
- What: Replaced the whole-world per-tile water scan with one `find_tiles_filtered` query and deterministic y/x sorting.
- Why: A 500x500 snapshot previously made 250,000 Lua API calls in one game tick.
- Next: Resolve whether the disconnected first-generation RL/core pipeline should be revived or removed.
## [2026-08-01] Legacy autonomy quarantine
- Files: `experimental/legacy_autonomy/`, `experimental/__init__.py`, `tests/test_autonomy.py`, `README.md`, `docs/architecture.md`, `docs/10_checklist_todo.md`, `docs/22_rl_decision_layer.md`, `docs/30_codex_brief_realbase_autonomy.md`, `FULL_DOCUMENTATION.md`, `CURRENT_STATUS.md`
- What: Moved the disconnected RL advisor, sandbox daemons, and private policy/state modules behind an explicit experimental namespace while retaining explicit module entry points.
- Why: The superseded generation was presented beside the active Nauvis builder despite having no production caller.
- Next: Continue the audit with batched `find_clear_area` terrain/resource probing or report-retention policy.

## [2026-08-01] Report retention bounds script-output growth
- Files: `orchestrator/game_bridge.py`, `tests/test_report_retention.py`, `CURRENT_STATUS.md`
- What: `GameBridge` now trims each report subdirectory to the newest `REPORT_RETENTION` (200) JSON files after every collection, never removing the report just handed to the caller.
- Why: The mod writes one tick-stamped report per command and removed none, while each command globs and stats the whole subdirectory once up front and again per poll -- so every command grew slower than the last, permanently, across all runs sharing one script-output.
- Next: Batched `find_clear_area` terrain probing (audit item 3.4), which should drop the redundant `area_clear` round-trip rather than batch it.
## [2026-08-01] Continuous integration
- Files: `.github/workflows/ci.yml`, `CURRENT_STATUS.md`
- What: Added a CI workflow running `python -m pytest -q` on Python 3.13 and `luac5.4 -p` over every `factorio_mod/*.lua`.
- Why: The mod had no automated validation at all -- nothing outside a running game loads it -- so syntax errors and Python regressions were only discoverable by launching a live server, the most expensive feedback loop in the project.
- Next: Decide whether to add `pythonpath = .` config so bare `pytest` works alongside `python -m pytest`.

## [2026-08-01] Mall requester and turn-buffer correction
- Files: `factorio_mod/layout_executor.lua`, `planners/mall_layout.py`, `orchestrator/mall_builder.py`, `planners/belt_bridge.py`, `tests/test_belt_bridge.py`, `CURRENT_STATUS.md`
- What: Applied logistic groups before requester slots, restored the shared-requester/two-provider mall geometry, and suppressed reserve buffers unless both inserters touch real surface belts.
- Why: Group assignment erased mall requests, outer output chests violated the dense cell contract, and a turn buffer could unload onto a tunneled or absent belt tile.
- Next: Redeploy and rebuild from the starting save so the corrected Lua and entity geometry take effect.
## [2026-08-01] Linear fluid-mixing adjacency
- Files: `core/fluid_systems.py`, `planners/preflight.py`, `CURRENT_STATUS.md`
- What: Added `mixing_conflicts`, a position-indexed adjacency scan, and rewrote both `validate_network_purity` and preflight's `_fluid_mixing_failures` on top of it.
- Why: Both compared every tile against every other tile -- 235k comparisons on the current bundle, paid on EVERY preflight because proving purity requires exhausting the search, and again far more expensively when diagnosing a large broken plan.
- Next: The reported tile pair for a purity failure is now the first in position-index order rather than segment-pair order; only the "Fluid mixing" prefix is asserted anywhere.
## [2026-08-01] Region-scan site selection
- Files: `orchestrator/live_base.py`, `orchestrator/resource_patches.py`, `tests/test_extraction_separation.py`, `CURRENT_STATUS.md`
- What: `find_clear_area` now answers every terrain question from two region-wide scans (occupancy incl. clutter via the new `include_clutter`, plus `resource_tiles`), dropping the per-candidate `area_clear` round-trip and asking `box_has_reserved_patch` only of candidates that actually overlap ore.
- Why: Siting cost one blocking game round-trip per rejected position -- 42 on a forested map -- for answers the region scan already contained.
- Next: `live_base.py` is now 809 lines against the 500-line charter limit; the observation helpers and the siting/search helpers are the natural split.

## [2026-08-01] Demand-dampened mall providers
- Files: `schemas/build_plan.schema.json`, `planners/mall_layout.py`, `orchestrator/autonomous_builder.py`, `factorio_mod/layout_executor.lua`, `docs/planner_and_execution.md`, `CURRENT_STATUS.md`
- What: Limited mall provider inventory to at least 4 production stacks, 5 logistics stacks, or 10 intermediate-product stacks, while allowing larger stock targets to expand existing providers.
- Why: Unrestricted provider chests induced excessive early production and logistic-bot demand.
- Next: Redeploy before live verification because the inventory-bar executor change is Lua-side.
## [2026-08-01] Adaptive mall reserve headroom
- Files: `schemas/build_plan.schema.json`, `planners/mall_layout.py`, `orchestrator/live_base.py`, `orchestrator/autonomous_builder.py`, `factorio_mod/layout_executor.lua`, `docs/planner_and_execution.md`, `CURRENT_STATUS.md`
- What: Combined live requester demand with queued blueprint/stock demand and expanded mall provider bars in two-stack steps whenever demand exceeded half their usable capacity.
- Why: Fixed minimum buffers still risked exhaustion during simultaneous construction and logistic requests.
- Next: Redeploy before live verification because requester aggregation and inventory-bar realization cross the Python/Lua boundary.
## [2026-08-01] One labelled request group per mall machine
- Files: `factorio_mod/layout_executor.lua`, `planners/mall_layout.py`, `orchestrator/mall_builder.py`, `orchestrator/live_base.py`, `schemas/build_plan.schema.json`, `tests/test_mall_requests.py`, `CURRENT_STATUS.md`
- What: Added the `logistic_sections` action field; the executor upserts one named section per machine set (label applied before slots, multiplier scaling per-craft group contents), and a shared requester now carries one group per assembler instead of a single merged slot list.
- Why: Merging both halves into one slot list meant each build rewrote the other's requests and each retry accumulated onto live state; a group per machine lets Factorio sum them, labels which machine each set feeds, and makes a rebuild idempotent.
- Next: The deployed mod is still the Jul 26 build, which has no `logistic_requests`/`logistic_sections` handling at all -- redeploy before judging any mall request behaviour.

## [2026-08-01] Settings gate covers every configurable field
- Files: `factorio_mod/layout_executor.lua`, `tests/test_executor_settings_coverage.py`, `CURRENT_STATUS.md`
- What: Replaced the three hand-listed "does this action still need configuring" conditions with one `SETTING_FIELDS`/`needs_reconfiguration` gate that includes `logistic_sections` and `infinity_filter`, and added a test asserting every field `configure_created_entity` writes appears in that list.
- Why: The gate omitted `logistic_sections`, so the SECOND machine of a paired mall cell -- which always lands on an already-existing requester -- skipped configuration entirely, was counted already_present, and reported no failure.
- Next: The deployed mod is still the Jul 26 build; redeploy and restart before any mall request behaviour can be judged.

## [2026-08-01] Mall requests sized in seconds of machine runtime
- Files: `planners/mall_layout.py`, `planners/recipe_data.py`, `orchestrator/mall_builder.py`, `tests/test_mall_requests.py`, `CURRENT_STATUS.md`
- What: A requester section's multiplier is now the crafts its machine completes in `MALL_SUPPLY_SECONDS` (10s) -- `ceil(10 * speed / craft_time)` -- instead of the finished-goods stock target; added assembling-machine-1/-3 to `MACHINE_SPEEDS`.
- Why: The requester exists to keep a machine fed, which is a rate question, not a stock question; sizing the buffer in time means an upgraded (faster) machine re-derives a correct buffer with no separate retuning step.
- Next: `compact_input_inserter` still picks the input inserter from a stock-count proxy and is undersized for most mall recipes (electronic-circuit needs 6 items/s and gets a basic inserter), so the fuller chest cannot actually reach the machine.

## [2026-08-01] Line inserter tier follows the real rate
- Files: `planners/recipe_data.py`, `orchestrator/autonomous_builder.py`, `tests/test_inserter_sizing.py`, `CURRENT_STATUS.md`
- What: Added the plain inserter to `FEEDER_RATES`/`INSERTER_TIERS` and `inserter_for_demand`, and `build_conversion_stage` now derives its tier from the busiest flow one machine's inserters carry (ingredients in and product out) instead of defaulting to fast-inserter.
- Why: Electric-furnace smelter rows move 0.625 item/s, so the docs/21 fast-inserter baseline was ~5x oversized and cost a whole production chain on a real base; 8 of 18 line recipes drop to a plain inserter, busy ones still escalate to bulk.
- Next: docs/21 still records "fast-inserter baseline" as the standard and should be updated; the plain inserter's 1.4 item/s is derived from base-rate ratios, not measured live like the other FEEDER_RATES entries.

## [2026-08-01] Transport ghost settling grace
- Files: orchestrator/stage_services.py, orchestrator/stage_transport.py, CURRENT_STATUS.md
- What: Long belt bridges now honor their calculated grace window before a temporary construction plateau is treated as a stall; the final wait remains bounded for diagnosis.
- Why: The runner previously quit with unbuilt ghosts while construction robots were still progressing.
- Next: If a bridge still fails after the bounded wait, the existing blockage/power/material diagnostics remain the escalation path.

## [2026-08-01] Promote high-demand intermediates out of the mall
- Files: orchestrator/intermediate_scaling.py, orchestrator/autonomous_builder.py, CURRENT_STATUS.md
- What: Working downstream lines now contribute real item-per-second demand; high-demand intermediates get a shared belt-fed line with a fast side tap instead of another requester-fed mall cell.
- Why: Gear demand can exceed a mall machine's output by several times, starving assembly-machine, belt, and fast-belt production while overloading logistic bots.
- Next: Validate the promotion against the next live run before adding automatic line-extension phases.

## [2026-08-01] Intermediate line belt sizing
- Files: orchestrator/intermediate_scaling.py, orchestrator/autonomous_builder.py, CURRENT_STATUS.md
- What: Promoted lines choose the cheapest stocked belt tier that can carry their calculated input rate.
- Why: A demand-sized gear line can exceed the 30/s fast-belt ceiling; selecting the tier from rate prevents a larger line from being built on an undersized belt.

## [2026-08-01] docs/21 records rate-driven inserter tiers
- Files: `docs/21_external_game_knowledge.md`, `CURRENT_STATUS.md`
- What: Replaced the "fast-inserter baseline" standard with the rate-driven rule (`inserter_for_demand`, busiest flow per machine including the product out) and recorded that stack inserters are never auto-selected.
- Why: The doc still asserted a standard the code deliberately stopped following, which `AGENTS.md` section 6 requires calling out rather than leaving to drift.
- Next: `FEEDER_RATES["inserter"] = 1.4` is derived from base-rate ratios, not measured live like the other entries.

## [2026-08-01] Retire promoted mall half
- Files: `planners/mall_layout.py`, `orchestrator/autonomous_builder.py`, `factorio_mod/layout_executor.lua`, `schemas/build_plan.schema.json`, `CURRENT_STATUS.md`
- What: When a mall recipe is promoted to a shared line, the old paired assembler, inserters, and provider are removed and only that recipe's requester section is cleared; the shared requester/substation remain reusable.
- Why: Promotion must stop the obsolete mall cell from consuming bots and construction materials while preserving the dense cell for a future assignment.
## [2026-08-01] Reuse emptied paired mall cells
- Files: `orchestrator/mall_builder.py`, `CURRENT_STATUS.md`
- What: Mall allocation now recognizes a shared requester whose two machine halves are empty, so a retired promotion cell can be assigned again.
- Why: Retiring the old assembler must release the dense cell rather than leave its shared requester stranded.
## [2026-08-01] Break readiness mall stock cycle
- Files: `orchestrator/autonomous_builder.py`, `CURRENT_STATUS.md`
- What: Readiness mall tasks may consume sufficient stocked ingredients directly instead of recursively requiring an upstream line first.
- Why: Inserter bootstrap was looping through an iron-plate build even though plates, gears, and circuits were already stocked; that upstream construction needed the same inserters.

## [2026-08-01] Conversion stage picks an inserter tier it can build
- Files: `orchestrator/autonomous_builder.py`, `planners/recipe_data.py`, `tests/test_inserter_affordability.py`, `tests/test_extraction_separation.py`, `CURRENT_STATUS.md`
- What: Added `inserter_tiers_covering` and `_stage_inserter_type`; a conversion stage now substitutes UP to an adequate tier the base is already stocking when the cheapest adequate tier is short, and only falls back to the cheapest when nothing adequate is stocked.
- Why: Regression from the rate-driven tier change -- the iron-plate smelter demanded 14 plain inserters, inserters need iron plate, and iron plate needs that smelter, so the runner livelocked on `MALL DEMAND: conversion_iron-plate requested inserter=14` roughly every 12s indefinitely.
- Next: The mall loop still has no bound (`iteration` never increments on the MaterialShortage path) and deferral does not remember what failed, so any other circular material demand will spin the same way.

## [2026-08-01] Belt-vs-bots follows measured demand
- Files: `orchestrator/autonomous_builder.py`, `orchestrator/stage_transport.py`, `tests/test_transport_mode_selection.py`, `CURRENT_STATUS.md`
- What: `build_conversion_stage` now derives each ingredient's transport mode from `_transport_mode` instead of hardcoding "belt", and the belt-route log line states the real reason rather than always claiming the bot limit was exceeded.
- Why: A 2-machine science stage draws 0.30 copper-plate/s -- a tenth of the 3.0/s bot limit -- but was handed a 128-tile belt corridor it could not afford, ending the run with "no belt tier can be afforded"; the log compounded it by asserting "0.30/s exceeds the 3.0/s bot limit".
- Next: `expand_upstream` only scales MINEABLE inputs, so a stage bottlenecked on an intermediate (fast-transport-belt short of iron-gear-wheel) defers forever instead of adding gear capacity.
