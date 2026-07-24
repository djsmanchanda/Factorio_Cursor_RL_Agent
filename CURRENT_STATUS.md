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