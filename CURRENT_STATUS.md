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

## [2026-08-01] Expansion follows the bottleneck down the chain
- Files: `orchestrator/autonomous_builder.py`, `tests/test_expansion_target.py`, `CURRENT_STATUS.md`
- What: Added `expansion_target`, which walks the recipe DAG from a stalled item to the deepest extraction stage, following whichever input the base is shortest of per craft and guarding against recipe cycles; `expand_upstream` now uses it and names the traced bottleneck.
- Why: Expansion only checked DIRECT ingredients for a mineable one, so fast-transport-belt (transport-belt + iron-gear-wheel, neither mineable) deferred forever at 11/25 while its real constraint, iron ore, sat two levels down.
- Next: Only extraction is scaled. When a stage's own line is saturated rather than starved the runner still has no move, because `ensure_produced` refuses to duplicate an existing machine.

## [2026-08-01] Saturated intermediates promote on the mining phase ladder
- Files: `orchestrator/intermediate_scaling.py`, `orchestrator/autonomous_builder.py`, `tests/test_intermediate_promotion.py`, `CURRENT_STATUS.md`
- What: A promotable intermediate whose machines are all working now promotes to a shared line on that evidence alone, and line size snaps to `EXTRACTION_DRILL_PHASES` (6, 20, 50, 100) -- reused, not copied -- stepping past the current size each time it saturates again.
- Why: `live_intermediate_demand` only sums consumers that are WORKING, and the consumers a starved intermediate blocks are exactly the ones not working, so a flat-out gear cell measured 0.00/s demand, never cleared the 3.0/s promotion threshold, and starved every downstream line indefinitely.
- Next: Promotion is still gated by `PROMOTABLE_INTERMEDIATES`, a hardcoded allowlist; an intermediate outside it saturates with no move available.

## [2026-08-01] Drill siting clears the mining area of foreign ore
- Files: `planners/recipe_data.py`, `orchestrator/live_base.py`, `orchestrator/stage_extraction.py`, `tests/test_drill_siting.py`, `tests/test_autonomous_builder.py`, `CURRENT_STATUS.md`
- What: Added `DRILL_MINING_AREAS` (electric 5x5, big 13x13) and `drill_siting_conflicts`, which rejects a centre with no target ore under it OR any foreign ore inside its mining area; `drill_footprints_have_resource` now wraps it so every existing siting search inherits the rule.
- Why: The probe only checked the 3x3 footprint for the target, but an electric drill mines 5x5 -- one tile past its footprint on every side -- so a drill sited entirely on iron near a patch border also mined the adjacent copper and jammed its output with an item nothing downstream accepts.
- Next: `DRILL_MINING_AREAS` covers the two electric drills; anything else raises rather than guessing a reach.

## [2026-08-01] A promoted line resolves real sources, not stocked buffers
- Files: `orchestrator/autonomous_builder.py`, `tests/test_promoted_line_sources.py`, `CURRENT_STATUS.md`
- What: Added `may_consume_stocked_inputs`; the bootstrap-from-stock shortcut is now taken only for a plain mall cell, never when promoting an intermediate to a shared line.
- Why: The shortcut `continue`s without recording a source position, which is right for a requester-fed mall cell but not for a belt-fed line; promoting gears while 13 iron plates sat in a chest left `build_conversion_stage` with nothing to route from and ended the run on "iron-gear-wheel feeds on ['iron-plate'], which have no producing stage to supply them".
- Next: Promotion retires the mall cell before the shared line completes, and the next pass rebuilds that cell -- observed twice in one run at (35, 31); worth confirming it converges rather than churning.

## [2026-08-01] Production prep before goal-driven building
- Files: `orchestrator/baseline_production.py`, `orchestrator/autonomous_builder.py`, `tests/test_baseline_production.py`, `CURRENT_STATUS.md`
- What: Added a standing prep set (copper-cable x2, iron-gear-wheel x2, steel-plate x2, electronic-circuit x1) built in dependency order before the goal loop, with `baseline_plate_draw`/`baseline_drill_phase` sizing the extraction it implies (iron 8.75/s -> phase 20, copper 3.00/s -> phase 6); `ensure_produced` gained `minimum_machines` so an under-sized line falls through to the build path instead of the repair guard.
- Why: Starting a goal from nothing made every run rediscover the same missing basics and thrash between half-built stages; prepping the basics first lets demand-driven scaling grow from a running start.
- Next: Prep declares the iron drill phase but does not yet drive extraction to it -- the phase number is reported, and the existing expansion ladder still has to climb 6 -> 20 on demand.

## [2026-08-01] Prep finishes its mall cells before promotion may fire
- Files: `orchestrator/autonomous_builder.py`, `tests/test_prep_precedence.py`, `CURRENT_STATUS.md`
- What: `ensure_produced` gained `allow_promotion` (default True); the production-prep pass passes False, so a baseline recipe fills its mall cells to the requested count instead of being promoted.
- Why: Prep asked for copper-cable to 2 machines; on the second pass the single cell read as saturated, promotion outranked prep and built a 6-machine dedicated line, which then demanded 9.00/s of copper plate belted across the base and died on a 25-tile tunnel requirement.
- Next: That belt failure is still latent for genuinely promoted lines -- `no belt route is available ... beyond turbo-underground-belt's 11-tile reach` means a promoted line sited far from its plate source cannot be fed at all.

## [2026-08-01] Run loop bounds livelocks; promoted lines sit by their source
- Files: `orchestrator/autonomous_builder.py`, `tests/test_run_loop_bounds.py`, `CURRENT_STATUS.md`
- What: The run loop now aborts after `_MAX_UNCHANGED_PASSES` consecutive passes that chose the same task at the same completion with the same outstanding work, and a promoted line is sited beside the input it consumes fastest instead of beside the mall.
- Why: `max_iterations` never bounded anything because `iteration` only advances on goal work, so every mall/prep `continue` skipped it and a stuck run spun for hours; and a 6-machine copper-cable line placed at the mall needed 9.00/s of plate belted ~90 tiles from the mine, dying on a 25-tile tunnel requirement.
- Next: Belt routing still fails outright on an obstruction instead of routing around it.

## [2026-08-01] Belt routes step around what they cannot tunnel under
- Files: `planners/belt_bridge.py`, `tests/test_belt_detour.py`, `tests/test_belt_bridge.py`, `CURRENT_STATUS.md`
- What: Added `search_clear_route`, a deterministic rectilinear A* over free tiles with a turn cost, and `_route_or_detour`; all three bridge builders now fall back to a searched route when the chosen one cannot be belted, and only report failure when no route exists at all.
- Why: `choose_clear_l_route` only scores a fixed set of L and Z shapes, so a cross-base run through built-up ground kept returning a route whose obstacles could not be tunnelled -- "blocked tiles sit on a corner", or a 25-tile span past turbo's 11-tile reach -- and the run died rather than stepping aside; this was the recurring run-killer across days of logs.
- Next: Tier reach still bounds a tunnel, so a genuine wall across the search band is still reported; the search margin is 48 tiles either side of the endpoints.

## [2026-08-02] Mall inserters sized from machine rate
- Files: `planners/mall_layout.py`, `tests/test_mall_inserter_sizing.py`, `CURRENT_STATUS.md`
- What: `compact_input_inserter` now takes the machine and recipe and sizes from intake rate via `inserter_for_demand`; added `compact_output_inserter` for the output face, which was a hardcoded fast-inserter; deleted the unreferenced `generate_compact_mall_layout`.
- Why: Sizing from the requested batch left 8 of 13 mall recipes undersized -- an electronic-circuit cell draws 6.0 items/s and got a 1.4/s inserter, running at a quarter speed however full its requester was -- and a machine throttled by its inserter still reports as working, so the cell read as saturated and promotion built six more equally throttled machines.
- Next: `FEEDER_RATES` remain estimates; the plain inserter's 1.4/s is derived from base-rate ratios rather than measured live.

## Prep raises extraction, and retirement waits for its replacement

**Files:** `orchestrator/autonomous_builder.py`, `tests/test_prep_extraction.py`

**What:** Production prep now grows the plate lines to the furnace count its own
draw implies (iron 14, copper 5) before it builds any intermediate cell, one
plate per pass so the next pass re-surveys. A blocked corridor logs
`PREP DEFERRED` and the run continues. Separately, the promoted-mall retirement
plan is now submitted AFTER `build_conversion_stage` rather than before it.

**Why:** An intermediate built over a starved plate line starves too, so prep
that only placed assemblers was declaring a readiness it did not have.
Retiring the mall cell first meant a pass that failed to finish the replacement
line left the recipe with no machine at all, and the next survey rebuilt the
very cell just removed -- observed twice in fifteen seconds at cell (35,31).

**Next:** Deferral has no memory: a blocked drill site is retried identically
on every later pass.

## A blocked mining corridor shortens the batch instead of failing forever

**Files:** `orchestrator/stage_extraction.py`, `tests/test_batch_prefix.py`

**What:** `buildable_batch_prefix` walks a reserved corridor outward from the
mine and returns the leading run of columns that are clear and free of foreign
ore. `plan_local_extraction` builds that prefix. An empty prefix falls through
to `_new_direct_mine`, siting a fresh row elsewhere.

**Why:** Reserved positions are a deterministic function of the active mine, so
a corridor that had grown into trees or a neighbouring patch raised the same
`Reserved ... drill site is blocked` on every pass, for the life of the save.
The prefix -- not the clean subset -- is the buildable part: the shared belt is
paved three tiles per column and columns sit three apart, so a skipped column
severs the belt and every drill past it feeds nothing.

**Next:** `PROMOTABLE_INTERMEDIATES` is still a hand-maintained allowlist.

## Promotion eligibility is derived, not listed

**Files:** `orchestrator/intermediate_scaling.py`, `tests/test_promotable.py`

**What:** `PROMOTABLE_INTERMEDIATES` is replaced by `is_promotable(item)`, which
asks whether a belt-fed line can actually supply the recipe: no fluid input, and
a machine not already owned by the mining or fluid stage.

**Why:** The hand-written list had to be remembered every time a recipe was
added, and it was already wrong in both directions. It named `processing-unit`,
which takes sulfuric acid and could never have run on a belt-fed line, and it
omitted `transport-belt` and `inserter` -- the two items the mall is most often
asked to mass-produce, and the exact symptom reported as "it should have started
regular transport belt production and it didn't".

**Next:** `FEEDER_RATES` are still derived estimates rather than measured.

## Feeder throughput is a ceiling, and is now treated as one

**Files:** `planners/recipe_data.py`, `planners/line_layouts.py`,
`planners/fluid_layouts.py`, `planners/local_layout_planner.py`,
`docs/21_external_game_knowledge.md`, `tests/test_feeder_rate_honesty.py`,
plus the sizing tests that asserted against the raw table.

**What:** `FEEDER_RATES` is documented as a best-case ceiling.
`UNMEASURED_FEEDER_RATES` names the three tiers never observed on this base
(inserter, bulk-inserter, stack-inserter) and `feeder_rate()` discounts them by
`UNMEASURED_RATE_DERATING` before anything is sized from them. Every production
call site goes through the accessor; a test fails the build if any reads the
table directly.

**Why:** Sizing off a ceiling understates how many feed points a line needs, and
an underfed machine still reports as working -- the exact path by which a
throttled cell read as saturated and had five more equally throttled machines
built beside it. The derating is a safety margin, NOT a measurement: it makes
the error direction cost materials instead of throughput.

**Next:** This cannot be closed by choosing a better constant. docs/21 now
carries the live measurement procedure; running it replaces the guess and the
tier comes out of the frozenset.

## The mod's requester bookkeeping now runs under test

**Files:** `factorio_mod/logistic_sections.lua` (new),
`factorio_mod/layout_executor.lua`, `tests/test_logistic_sections_lua.py`,
`tests/test_executor_settings_coverage.py`, `.github/workflows/ci.yml`

**What:** The pure logistic-section functions moved out of the executor into
their own module, and 25 tests drive them in a real Lua interpreter (lupa)
against a stubbed `LuaLogisticSections`. `layout_executor.lua` drops 650 -> 582
lines. CI installs lupa in the pytest job; the syntax job still parses the rest.

**Why:** 650 lines of Lua were covered only by `luac -p`, which parses without
executing and does not resolve globals. This is the code whose failure mode is
silent -- a chest holding no requests looks exactly like a chest nobody asked
for any -- and it is where the live requester bug lived. Verified by
reintroducing four separate bugs (dropped trailing-slot clear, unclaimed blank
section, missing SETTING_FIELDS entry, ghosts reconfigured for recipe); each
was caught.

**Note:** `lupa` is a verification-only dependency, deliberately kept out of
`requirements.txt` like `luaparser` -- the game ships its own interpreter. The
tests `importorskip` it so a bare checkout still runs green.

**Next:** LOC debt -- `autonomous_builder.py` is still ~1400 lines.

## Dead-code sweep: one orphan removed, three false positives corrected

**Files:** `schemas/build_plan.schema.json`,
`tests/test_executor_settings_coverage.py`

**What:** Removed the `blueprint` action field from the build-plan schema, and
added a guard that every field the schema declares is one the executor either
applies at creation or reapplies as a setting.

**Why:** Nothing emitted `blueprint` and nothing consumed it, so a plan could
declare one and have it silently dropped -- the same silent-skip failure
SETTING_FIELDS guards against, one level up in the contract. Verified by
reinstating the field and watching the new test fail.

**Correction to the record.** Three modules previously flagged as residual dead
code are not, and are deliberately kept:
- `rl_feedback_builder.py` -- a documented CLI entry point in README.md and
  FULL_DOCUMENTATION.md. Not imported because it is a script.
- `core/quality_modules.py` -- data tables and pure validators, encoded from
  docs/21 and tested; awaiting a consumer, not orphaned by accident.
- `planners/pipe_bridge.py` -- imported only by its own test. Note that the
  earlier entry at line 305 has the dependency backwards: pipe_bridge imports
  `fluid_routing`, not the reverse, so it is an orphan rather than a live
  dependency. Kept anyway as the fluid analogue of the belt bridge the
  extraction stages use.

**Next:** LOC debt is the remaining charter breach.

## Split the judgement calls and the diagnosis out of the builder

**Files:** `orchestrator/build_decisions.py` (new),
`orchestrator/build_diagnostics.py` (new),
`orchestrator/autonomous_builder.py`, `tests/test_inserter_affordability.py`

**What:** `autonomous_builder.py` drops 1444 -> 1185 lines.
`build_decisions.py` holds what a pass decides -- what limits a product
(`expansion_target`), where a line should sit (`_heaviest_source`), whether
stocked inputs may be consumed, and which inserter tier is both adequate and
affordable. `build_diagnostics.py` holds `_diagnose_blockage` and
`_side_sample_plate_output`, which read a stalled stage without changing it.

**Why:** Charter limit is 500 LOC; this was the largest breach. These two groups
also had no monkeypatch entanglement, so the move could not silently change
which function a test was exercising.

**Still over the limit and NOT split:** `autonomous_builder.py` (1185),
`live_base.py` (851), `stage_services.py` (716), `stage_extraction.py` (609),
`verify_factory_invariants.py` (600), `stage_transport.py` (556),
`core/metrics.py` (555), `belt_bridge.py` (521), `layout_executor.lua` (581).

The remaining split of `autonomous_builder.py` -- the four stage builders into
one module, `run()` into another -- is blocked on a real hazard, not on effort:
tests monkeypatch `autonomous_builder.bring_stage_up` and
`autonomous_builder.build_conversion_stage`, and callers of those live in the
same module. Move the callee and the patch silently stops applying, so the test
exercises the real function and still passes. Those patch sites must be
retargeted in the same change, and each retarget verified by confirming the test
fails when the patch is removed.

**Next:** the goal's closing audit and verification.

## Closing audit for this batch of work

**Verified:**
- 971 passed, 1 skipped. All 17 mod Lua files compile.
- Every module in orchestrator/planners/core/tools imports cleanly -- the two
  new modules introduce no cycle.
- Every .py and mod .lua file carries its `Path:`/`Purpose:` header
  (`scripts/combine_docs.py` was the last one missing them).
- The growth ladder coheres end to end: prep sizes extraction from its own draw
  (iron 8.75/s -> 14 furnaces -> 20 drills; copper 3.00/s -> 5 -> 6), prep tops
  out at 2 machines and promotion starts at 6 so the two cannot fight, and a
  saturated cell climbs 2 -> 6 -> 20 -> 50 -> 100 on the identical ladder the
  drills use. steel-plate is correctly excluded from promotion: it is a furnace
  recipe the mining stage already sizes.
- Four guards were verified by reintroducing the bug they catch: the batch
  prefix (5 failures), the Lua section bookkeeping (4 separate bugs, all
  caught), the orphan schema field, and the SETTING_FIELDS omission.

**Known open, in priority order:**
1. `FEEDER_RATES` derating is a policy margin, not a measurement. The live
   procedure is in docs/21; running it is the only thing that closes this.
2. LOC: nine files remain over the 500-line charter. The `autonomous_builder.py`
   split is blocked on retargeting monkeypatch sites, described above.
3. `planners/pipe_bridge.py` is a genuine orphan, kept deliberately.
4. Nothing in this batch has been exercised against a live game. Prep,
   corridor truncation, and the promotion set are all decision-layer changes
   verified by test only.

## Prep now queues a drill shortfall instead of ending the run

**Files:** `orchestrator/autonomous_builder.py`, `tests/test_prep_shortage.py`

**What:** The plate-prep block catches `MaterialShortage` and calls
`add_demands`, so the shortfall becomes a mall target.

**Why:** Live run 2026-08-02 17:02:22 ended on
`mining_iron-ore needs material the base does not have -- electric-mining-drill:
need 14, short 6`. Prep correctly decided iron needs drill phase 20, asked for
14 drills, and the base held 8. `MaterialShortage` is a `RuntimeError`, so the
`except (StuckError, ValueError)` deferral never saw it. Every other build call
in `run()` already fed shortages back to the mall; the block added last commit
was the only one that did not.

Prep is the first thing that ever asks for a whole drill phase in one batch, so
it is the first thing to discover the base cannot afford one. That is the
recursive bottleneck working as intended -- raising iron requires drills, and
drills come from the mall -- and it needs to push back a step, not die.

**Note:** a shortage must NOT take the `PREP DEFERRED` path, which marks a plate
finished for the whole run. The mall is about to fix it; prep should retry.

## No function in autonomous_builder.py exceeds the 80-line charter limit

**Files:** `orchestrator/autonomous_builder.py`, `tests/test_run_loop_bounds.py`,
`tests/test_prep_extraction.py`, `tests/test_prep_precedence.py`,
`tests/test_prep_shortage.py`

**What:** The five oversized functions are decomposed into 32 functions, largest
now 80 lines (was 281). `ensure_produced` 281 -> 57, `run` 232 -> 80,
`build_conversion_stage` 219 -> 80, `build_mining_stage` 145 -> 72,
`bring_stage_up` 115 -> 41. The file grew 1185 -> 1537 LOC, which is the point:
file length was never the metric.

**Why:** Per the revised AGENTS.md 11, function size is the hard limit because
it is what predicted the defects. Every helper stays in this module on purpose
-- tests patch `bring_stage_up` and `build_conversion_stage` by module
attribute, and a callee moved to another module would resolve the real function
instead of the patch, silently, with the test still passing.

**How it was verified.** Tests alone do not prove a move refactor: they pass
just as happily if a name silently resolves to the wrong scope. `pyflakes` was
the real check and it caught six genuine defects the suite did not --
`area`, `expand`, `ore_output`, `modes`, `max_belt_route_tiles`, and
`direct_belt_input` all became unbound when their block moved. Bodies were
taken verbatim from source rather than retyped, after an early attempt at
retyping introduced differences.

Three source-slicing tests broke because the text they searched for had moved.
Two of them (`test_run_loop_bounds.py`) are now behavioural instead: the stall
signature and the livelock bound are pure functions, so they are called rather
than grepped, and the file gained five tests in the process. That is the seam
benefit the charter asks for -- the split paid for itself in testability, not
in line count.

**Still over 80 LOC repo-wide:** 33 functions, largest
`planners/line_layouts.py:generate_line_layout` at 295.

## File-size budget set to the user's actual standard

**Files:** `AGENTS.md`

**What:** File length is a budget, not a gate: under 500 recommended, under 800
acceptable, past 800 justify it here. Added the note that splitting functions
to meet the 80-line rule will often GROW a file, and that this is fine.

**Why:** User direction, 2026-08-02: "no need for extensive loc correction /
under 800 is alright, under 500 recommended."

**Where the repo stands** (excluding tests and experimental/):
- Over 800: `autonomous_builder.py` (1537), `live_base.py` (851). Both waived --
  the first is long precisely because its functions are now all under 80, and
  the second is 31 small RCON helpers.
- 500-800, acceptable, no action: `stage_services.py` 716,
  `stage_extraction.py` 609, `verify_factory_invariants.py` 600,
  `stage_transport.py` 556, `core/metrics.py` 555, `belt_bridge.py` 521,
  `electronics_execution.py` 507.

No further LOC work is planned.

## A belt route is never emitted severed

**Files:** `planners/belt_bridge.py`, `tests/test_belt_continuity.py`

**What:** `_tunnelled_points` marks which route tiles run underground: the
blocked ones, plus any free tile trapped between two blocked runs. `_belt_run`
works from that instead of from the raw blocked set.

**Why:** A single free tile between two obstacles was used as BOTH the first
tunnel's exit and the second tunnel's entrance. `actions.pop()` removed the
exit and replaced it with a second entrance, so the emitted plan had two
underground inputs and one output -- items went underground and never came back
up. Reported live on the iron-plate haul from the mine at (12.5,-3.5), which
arrived as three disconnected belts with the break at (-9.5,-25.5).

The trapped tile is now swallowed and the two runs merge into one tunnel, whose
longer span is checked against the tier's reach like any other. When the merged
span does not fit, the route is REFUSED rather than severed, which lets the
caller detour or buy a longer tier -- verified: the same obstacle that a plain
transport-belt cannot span is routed cleanly by a fast-transport-belt.

**Verified** by an exhaustive check: all 1024 obstacle layouts on a 12-tile run,
for all four belt tiers, are either continuous or refused -- never silently
broken. Reintroducing the bug fails 9 of the 11 tests.

## Prep runs before the mall, and an unbacked stock draw is now visible

**Files:** `orchestrator/autonomous_builder.py`, `tests/test_precursor_check.py`,
`tests/test_prep_extraction.py`

**What:** Two changes.

1. The run loop now does standing-cell prep FIRST, then plate extraction, then
   mall construction. Both prep paths hand the pass back to the mall when they
   cannot afford a machine, instead of keeping it.
2. `_has_producer` checks whether anything is actually making an ingredient
   before a mall cell draws it from stock. An unbacked draw is logged as
   `NOTHING IS PRODUCING IT`, recorded in `UNBACKED_DRAWS`, and named in the
   stall report.

**Why:** Reported live -- the run never built a copper-cable cell, so no
electronic-circuit, so no electric-mining-drill and no assembling-machine-1. It
consumed the player's manually supplied starter stock and could not continue.

`mall_targets` starts with ten entries and only empties once every one is
satisfied, and prep sat *after* the `if mall_targets:` gate -- so prep never ran
at all. Every pass went to mall construction, which drew the starter
intermediates through `MALL BOOTSTRAP` while the lines that would refill them
were never built.

Ordering intermediates before plate extraction reverses an earlier decision, on
purpose: plates-first was defensible, but extraction prep asks for 14 drills,
and drills need circuits, which need the copper-cable cell that was queued
behind it. The cheap half (four assemblers) goes before the expensive half.

**Why the check WARNS rather than refuses:** refusing to draw unbacked stock
deadlocks. Prep needs an assembling machine, which comes from the mall, which
needs circuits and gears -- so the mall must be able to spend stock on the very
assemblers the producing lines are made of. Ordering fixes the failure; the
check makes the remaining risk visible instead of silent.

## A pole blocking a belt route steps aside instead of forcing a detour

**Files:** `orchestrator/pole_relocation.py` (new), `orchestrator/live_base.py`,
`orchestrator/stage_transport.py`, `tests/test_pole_relocation.py`

**What:** When a belt route fails outright, `relocate_blocking_poles` looks at
the poles standing on the corridor and nudges the movable ones aside, then the
route is planned again. `live_base.pole_context` fetches what a pole supplies
and what it is wired to in one round trip; `live_base.poles_in_area` lists
candidates.

**Why:** User direction -- a pole is the one obstacle worth moving rather than
routing around. It is a one-tile entity whose job is to stand SOMEWHERE in a
supply area, not on one exact tile. A machine, chest, or drill is where it is
for a reason; a pole usually is not.

**What stops it doing damage:**
- A pole moves only if the new position still covers every consumer it powered
  AND still reaches every pole it was wired to. Checking only the nearest
  neighbour is how a nudge silently cuts a network in two.
- Supply area is compared per-axis, because Factorio's is a SQUARE. A radius
  check rejects legal corner positions and accepts illegal edge ones.
- Substations and big poles (2x2) are never moved for a belt -- that is a
  network decision, not a routing one.
- The replacement is placed BEFORE the original is removed, so nothing loses
  power in between.
- A pole may not step onto the corridor it is clearing, or onto an occupied
  tile, and moves are capped at 3 tiles with a 1-tile wire margin.
- Relocation runs only AFTER a route has failed. If no pole can move, the
  original failure is re-raised and the router detours as before.

**Verified** by subverting three guards (nearest-neighbour-only wire check,
circular supply area, allowing 2x2 poles); each was caught.

## The agent can build the machine it builds everything with

**Files:** `planners/recipe_data.py`, `planners/assembler_tiers.py` (new),
`factorio_mod/recipe_catalog.lua`, `schemas/recipe_catalog.schema.json`,
`orchestrator/intermediate_scaling.py`, `orchestrator/autonomous_builder.py`,
`tools/starter_kit_audit.py` (new), plus tests.

**What:**
1. `MALL_ONLY_RECIPES` now admits `assembling-machine-2`, `bulk-inserter`, and
   `flying-robot-frame`. The catalog filter rejected them for having four
   ingredients, so the agent could never build them at all.
2. `is_promotable` also rejects recipes wider than `LINE_MAX_INGREDIENTS` --
   a belt-fed line carries two main lanes plus one auxiliary and refuses a
   fourth, so promoting one would have crashed the layout.
3. The recipe catalog (v1.1.0) exports crafting machines with their live
   ingredient slot counts, speeds, and categories.
4. `planners/assembler_tiers.py` chooses the tier for a line or a mall cell.
5. `tools/starter_kit_audit.py` reports which hand-placed kit items the base
   can now make for itself.

**Why:** Every production line in the system runs on an assembling-machine-2,
and the agent had no way to make one -- so the whole base rested on the player
having stocked them by hand. Same for bulk-inserter, which the rate-driven
selector reaches for on busy lines.

**The tier policy** (user standard, 2026-08-02):
- Bootstrap: lines take tier 1, tier 2 is reserved for mall cells that need
  four ingredient slots.
- Once the base PRODUCES tier 2, everything uses it, and spare tier-2 stock
  above `UPGRADE_RESERVE` rebuilds tier-1 lines in place.
- Tier 3 is demand-based -- only for recipes at or above
  `TIER3_MIN_CRAFT_SECONDS`, only once the base makes them, and the mall gets
  first claim.

**Slot counts are read, never assumed.** They are the reason the tiers are not
interchangeable, and putting a three-ingredient recipe on a two-slot machine
leaves a line that builds and then cannot set its own recipe. With no live
export the recipe's declared machine is kept, so an un-redeployed base behaves
exactly as before.

**Remaining starter-kit dependencies**, from the audit against the live
catalog -- 6 items the agent still cannot build:
- `stone-brick` -- category `smelting`, which the catalog filter does not learn
  (it only takes assembler categories). Blocks `electric-furnace` and
  `oil-refinery`. Needs a stone mining stage plus a furnace recipe, both of
  which follow the existing iron/copper pattern.
- `battery` -- chemistry category, needs sulfuric acid.
- `electric-engine-unit` -- crafting-with-fluid, needs lubricant.
- `flying-robot-frame` -- now learnable, but blocked on the two above.
- `construction-robot`, `logistic-robot` -- blocked on the frame.

## Construction stock fills the chest once the base makes its own

**Files:** `orchestrator/construction_stock.py` (new),
`orchestrator/autonomous_builder.py`, `planners/recipe_data.py`,
`factorio_mod/recipe_catalog.lua`, `schemas/recipe_catalog.schema.json`,
`tests/test_construction_stock.py`

**What:** A bulk construction item keeps its small opening figure only while
nothing on the base produces it. Once a line does, the cap comes off and the
target becomes a full provider chest (48 slots x the live stack size -- 4800
belts). Machines are exempt. The catalog (v1.2.0) now exports item stack sizes
so that figure is the game's rather than a guess.

**Why:** User standard, 2026-08-02: "< 50 is restrictive in the long term,
there will be blueprints where more than 1000 of belt will be required as well
as hundred splitters and underground belts", and "after the game is out of the
starter phase, just let it build and fill up the chest".

**Why self-sufficiency is the phase boundary** rather than a schedule or a
refill count: while an item is scarce, every one comes out of the player's
starter kit and an opening figure protects it. Once the base makes them, the
cap is pointless and self-limiting in the right way -- a base cannot
overproduce what it cannot produce.

**Superseded:** a first attempt used a 5-rung ladder (50/200/500/1000/2000)
climbing on a persisted refill count in the priority list. Dropped as
over-built once the rule was stated plainly: it is a phase change, not a curve.

## The opening belt stock now covers one real connection

**Files:** `orchestrator/parts_mall.py`, `tests/test_starter_stock.py`

**What:** `STARTER_MALL_TARGETS` raises `transport-belt` from 50 to 200 and adds
`underground-belt` at 20.

**Why:** User report, 2026-08-02: "even in the previous runs, just to connect
two different spots sometimes 200+ belts are required". The figure is sized off
a link actually observed -- the mine at (12.5,-3.5) feeding the refinery placed
at (-34,-26) is about seventy tiles by itself, and a conversion stage bridges
one route per ingredient on top of its own line. At 50 the first bridge could
never be paid for: it failed, raised its own target through `MaterialShortage`,
and spent a pass doing so every time.

Undergrounds are stocked because a tunnel is a PAIR of entities and a long
cross-base run meets several obstacles.

This is the same defect as the stock cap, at the other end -- the opening
figure was as wrong as the ceiling.

## A mall cell stops itself once the network holds enough

**Files:** `planners/stock_gating.py` (new), `planners/mall_layout.py`,
`factorio_mod/layout_executor.lua`, `factorio_mod/logistic_sections.lua`,
`schemas/build_plan.schema.json`, `tests/test_stock_gating.py`

**What:** Every paired mall cell carries a `logistic_condition` on its machine:
craft only while the logistic network holds fewer than the cell's stock target.

**Why:** Before this the only brake was the planner noticing on a LATER pass
and dropping the target. That cost a pass, and did nothing in between -- the
mall spent the interval consuming stock to make more of what it already had,
which is how a run ate its starter intermediates.

**Design change from the plan.** The plan was roboport -> red/green wire ->
decider -> machine. Verified against the live API instead of assumed, and
`LuaAssemblingMachineControlBehavior` inherits `connect_to_logistic_network`
and `logistic_condition` from `LuaGenericOnOffControlBehavior`: a machine reads
the logistic network DIRECTLY. No wire, no roboport connection, no combinator,
no constant combinator holding targets. It only has to stand inside roboport
coverage, which a requester-fed mall cell does by construction.

That also settles the inspectability question raised when this was proposed:
the gate is a per-entity setting the planner writes into the build plan,
exactly like `logistic_sections`. Nothing moves out of the plan and into
in-world wiring.

**API verified, not guessed** (lua-api.factorio.com): `circuit_enable_disable`
and `circuit_condition` are the CIRCUIT pair; `connect_to_logistic_network` and
`logistic_condition` are the LOGISTIC pair, which is the one that needs no
wire. `CircuitConditionDefinition` is `{first_signal, comparator, constant}`,
and the game returns unicode comparators when read back even where ASCII was
written.

**Guards:** the gate is in `SETTING_FIELDS`, so an already-built cell gets it
reapplied when the target changes -- omitting it is the silent skip where the
entity reports already_present, nothing fails, and the setting never lands. The
executor also re-reads `connect_to_logistic_network` after writing, because
pcall succeeding is not proof the property took. Verified by dropping the
SETTING_FIELDS entry (3 tests fail) and by loosening the comparator to `<=`
(2 tests fail).

**Not built:** the red/green wire foundation, the roboport read-mode, and the
constant-combinator target vector are all unnecessary for this. They stay
unbuilt until something actually needs a signal the logistic network cannot
supply.

## Three faults from the 23:30 run

**Files:** `planners/mall_layout.py`, `orchestrator/mall_builder.py`,
`orchestrator/autonomous_builder.py`, `orchestrator/baseline_production.py`,
`tests/test_stock_gating.py`, `tests/test_baseline_production.py`

### 1. Cells gated at "< 2"
`_prep_intermediate` passes `stock_target=wanted`, where `wanted` is a MACHINE
COUNT from `BASELINE_MACHINES`, not a quantity of product. The gate added last
commit turned that long-standing conflation into a hard production stop: the
copper-cable cell refused to craft above two cables.

Gating is now opt-in (`gate_on_stock`), and only the mall-task path asks for it,
because only that path has a real count of finished goods. An intermediate that
feeds other machines is throttled by its own provider chest filling up; gating
one on a network count stops every line behind it.

### 2. Twelve steel furnaces across six mall cells
`find_line` counts machines by the recipe they have SET. `steel-plate` carries
`set_recipe: False` because a furnace takes its recipe from whatever is
inserted -- so an idle one reports none and can never be counted. Prep read
`have=0` however many it had built, and placed another cell every pass until the
livelock guard stopped it (it did stop it, after twelve furnaces).

`build_compact_mall_stage` now refuses any recipe with `set_recipe: False`,
because such a cell can never be re-counted and would be rebuilt forever. A
silent infinite loop becomes a stated error.

### 3. steel-plate does not belong in the prep set
Removed. It is smelted, not assembled, and it wants an iron-plate BELT that does
not exist that early. It is built on demand by whatever needs it, through a
smelting stage. Iron prep draw falls 8.75 -> 7.50/s, 14 -> 12 furnaces; the
drill phase stays 20.

**Verified** by reintroducing all three together: 5 tests fail.

## Two faults from the 23:52 run

**Files:** `factorio_mod/layout_executor.lua`, `orchestrator/autonomous_builder.py`,
`tests/test_plate_prep_fairness.py`, `tests/test_stock_gating.py`

### 1. `inventory_limit_set_failed` ended the run
`LuaInventory` has no `clear_bar()`. The limit is cleared by calling `set_bar()`
with no argument. That branch only runs when a target needs the WHOLE chest, so
it had never executed -- until `STOCK CAP LIFTED: transport-belt 200 -> 4800`
produced the first plan that reached it, and it died on a nil method.

Verified against the API rather than reasoned about: `set_bar(bar?)`, "omitting
this parameter or passing nil will clear the limit", and `supports_bar()` should
gate both. Clearing the bar is the correct behaviour for a full-chest target:
no limit means fill it.

### 2. A whole run produced no copper
`_prep_plate_extraction` was offered only the FIRST unprepped plate. iron-plate
yields the pass every time it is short of drills and never enters `prepped`, so
copper-plate never got a turn at all. Every unprepped plate is now offered the
pass, and the first one that does work spends it.

**Verified** by reintroducing both: 3 tests fail.

**Still open -- smelter siting.** The run put the iron smelter at (6,51) with
the mine output at (12.5,-3.5): a 61-tile ore haul, and 55 tiles again from
there back to the reference point at (3,-1). Reported as "the design of the
transport belt to the iron smelting was very inefficient", and the screenshot
shows the belt looping around. Not yet fixed -- see the analysis below before
changing a spatial heuristic that cannot be validated offline.

## Two faults from the 01:01 run

**Files:** `orchestrator/stage_services.py`, `orchestrator/autonomous_builder.py`,
`tests/test_belt_survey_and_tiers.py`

### 1. Belt laid over the mine's own belt, so no ore reached the furnaces
`_BRIDGE_SURVEY_MARGIN` was 24 while `_ROUTE_SEARCH_MARGIN` is 48: the detour
router could search TWICE as far as the collision survey covered, and emit belt
onto tiles never checked for occupancy. The iron-ore bridge did exactly that,
laying transport-belt over the mine's existing fast-transport-belt at
x=15.5..17.5, y=-1.5, and the ore never arrived.

The survey margin is now derived from the router's own constant, so raising the
search radius cannot silently outrun the survey again.

### 2. Iron dropped to a bot-fed smelter because ONE belt tier was short
`_DEFAULT_BELT` is `fast-transport-belt`. A `MaterialShortage` naming it went
straight to `build_logistic_smelter` -- a beltless, requester-fed line -- while
plain `transport-belt` sat on a 200-unit mall target. Reported as "very slow and
inefficient, just unacceptable for basic resource like iron and copper".

Every stocked tier is now tried, preferred first, before belts are abandoned.
The beltless smelter remains only for a genuine cold start where no tier can be
afforded at all, and its log line now says it is to be replaced.

**Verified** by reintroducing both: 8 of 9 tests fail.

**Note on the repeated `inventory_limit_set_failed`:** that run still used the
mod from before the `set_bar()` fix. The failing branch is inside the mod, so it
needs a redeploy to take effect.

**Still open -- smelter siting.** Unchanged from the previous entry: iron went to
(6,51) again against a mine output at (12.5,-3.5). The anchor ordering, the
60-tile drift in `find_clear_area`, and the equal weighting of ore haul against
plate haul are all suspects, and none can be judged offline.

## The smelter is sited beside the ore, not beside the base

**Files:** `orchestrator/stage_extraction.py`, `tests/test_smelter_siting.py`

**What:** Three changes to the siting DECISION, not to any placement:

1. `smelter_search_anchors` takes the mine's `ore_output` and adds two anchors
   derived from it -- the footprint immediately beyond the ore reservation,
   level with the output, on whichever side it sits. The four existing anchors
   are edges of the whole PATCH, so on a long patch none of them is near the
   belt the smelter has to meet.
2. Anchors are ranked by MANHATTAN distance, which is what a belt costs and
   what the candidate scoring downstream already measured. Ranking by
   straight-line distance disagreed with the quantity being minimised and put a
   16.5-tile site behind an 18.0-tile one.
3. Candidate scoring puts the ore haul BEFORE the sum of both hauls. Summing
   them let a site far from the mine win because it happened to sit near the
   base -- which is how iron landed at (6,51) against a mine output at
   (12.5,-3.5), a 61-tile haul.

**Why the ore haul outranks the plate haul:** the ore belt carries the drill
row's whole output and is re-laid every time the row grows 6 -> 20 -> 50 -> 100,
so its length is paid over and over. The plate belt leaving the smelter is built
once, and its far end can be served by bots if it comes to that.

This is the same rule a promoted line already follows through `_heaviest_source`
-- sit beside the input you consume most -- now applied to the smelter, which
had its own unrelated anchor and scoring logic.

**Verified** by reverting all three decisions: 3 of 8 tests fail. Anchors are
still proven never to overlap the ore reservation, and callers that do not know
where the ore leaves keep the old ordering.

**Not changed, and still a suspect:** `find_clear_area(max_radius=60)` lets a
site drift up to 60 tiles from its anchor, and `LOCAL_MODE_MAX_LINK_TILES` still
admits a 300-tile ore haul. If a run still sites badly with good anchors, the
cause is the base's own sprawl having filled the ground next to the mine, which
is a zoning problem rather than a scoring one.

## The build now lays the bridge the preflight approved

**Files:** `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`,
`tests/test_belt_to_belt_feed.py`

### 1. An inserter spliced into a belt-to-belt join
`destination_is_belt` existed and was threaded to the PREFLIGHT only. The
preflight therefore planned `bridge_belt_to_belt` -- one continuous belt, no
inserter -- while the build path, which had no such parameter, laid a
chest-shaped bridge with an inserter in the middle of it. The two planned
different things, so the bill of materials that was checked was not the bill
that got built.

Ore mine to furnace row, by bridge shape: chest->chest 2 inserters,
belt->chest 1, belt->belt 0. The flag now reaches the build.

That is also the answer to why copper looked right and iron did not: whether
`_through_belt_source` finds the mine's through belt decides between the
one-inserter and two-inserter shapes, and neither was the zero-inserter one.

### 2. Running short of belt ended the run
`_plan_belt_transport` raised `StuckError` when no tier was affordable. Every
other build path turns a shortage into a mall target and retries; this one
killed the run -- "short transport-belt by 96" while the mall held an unfilled
4800-belt target. It now raises `MaterialShortage` for the CHEAPEST tier's bill,
which is the tier the mall can actually produce. An unroutable bridge is still a
hard failure, because more belt cannot fix geometry.

**Verified** by reverting both: 4 of 10 tests fail.

## Smelting is a block decision, not one long line

**Files:** `planners/smelter_block.py` (new), `tests/test_smelter_block.py`

**What:** `block_shape(furnaces)` folds a furnace count into rows -- filling a
row to its cap before starting the next -- and `block_is_full` says when the
block has outgrown its phase and a SECOND block at another patch is the answer.

Sizes are the user's, 2026-08-03: 15 furnaces per row in the opening phase and
50 late, 4 rows opening and 30 late.

**Why:** the existing rule puts every furnace in ONE row. At the top drill phase
that is 104 furnaces in a 312-tile line, and a belt long enough to serve it has
to cross whatever the base has already built -- which is the "very inefficient"
belt design reported, and a large part of why siting drifted so far from the
mine. Measured, feed belt per drill phase:

| drills | furnaces | one row | block | block belt |
|---|---|---|---|---|
| 6 | 7 | 21 t | 7 x 1 | 21 t |
| 20 | 21 | 63 t | 15 x 2 | 61 t |
| 50 | 52 | 156 t | 15 x 4 | 77 t |
| 100 | 104 | 312 t | 15 x 7 | 101 t |

A test found a real modelling error while writing this: `feed_belt_length` was
adding the cross-row run even for a single row, which has none -- it IS the
line.

**NOT WIRED YET.** This is the decision only. `_smelter_layout_geometry` and
`generate_line_layout` still emit a single row, so nothing on the ground changes
until the layout generator can lay a block. That is the next piece and it is a
real geometry change, not a constant.

## A plan is now checked against itself

**Files:** `planners/plan_validation.py`, `planners/line_layouts.py`,
`tests/test_plan_self_collision.py`

### The reported fault
A substation shared ground with a fast inserter and a steel chest. Reproduced
exactly: the line layout at (53,45) placed a substation at (49,47), whose 2x2
footprint covers the feed chest at (49.5,47.5) and its inserter at (49.5,46.5).

### Why nothing caught it
`validate_no_collisions` existed but was only ever called with SEPARATE plans,
so a generator colliding with its own output was never checked. The live
executor could not catch it either: `exact_position_occupants` matches entities
whose CENTRE is identical, which a 2x2 over a 1x1 never is. `can_place_entity`
-- the game's own footprint-aware check -- is not used anywhere in the mod.

`validate_build_plan` now runs the collision check on the plan against itself.
Every plan goes through it, so this covers every generator.

### The deeper fault it exposed
Scaffolding was pinned at x=-4.0 and -7.5 while the feed columns GROW westward
with machine count: x=-2.5 at three machines, -10.5 at twenty-one, -24.5 at
fifty. Past about nine machines a line grew into its own power.

Scaffolding now sits SOUTH of the feed columns -- west of the machines only
y=-1.5..2.5 is occupied -- tracking the line's west edge but clamped at
`_SCAFFOLD_WEST_LIMIT`. The clamp is not cosmetic: the pole rows never run west
of x=0.5, so a substation tracking a fifty-machine line lands 26 tiles out, past
its 18-tile WIRE reach, and an unwired substation reads as no_power on every
machine it was meant to supply. A test caught that while the fix was being
written.

An offshore pump and the pipe on its own connector are excused, like the
pumpjack -- that is an attachment, not a fault.

**Verified** by restoring the old substation position and disabling the
self-check: 9 tests fail.

**Left alone:** the mod still does not call `can_place_entity`. Plan-time
checking now covers self-collisions, but a plan colliding with something ALREADY
on the ground is still only caught by the centre-exact check.

## A buffer is not something the run waits for

**Files:** `orchestrator/autonomous_builder.py`, `tests/test_construction_stock.py`

**What:** `_lift_targets_the_base_can_supply` is replaced by `stock_buffer_for`.
The mall TARGET stays at what the mission asked for; the chest-full figure is
passed to the cell as its gate and provider limit only.

**Why:** lifting the target to 4800 turned a satisfied 200-belt requirement into
a 4800-belt gate. The loop sat in `wait_for_stock` polling every five seconds
and expanding iron every sixty -- "MALL WAIT: transport-belt stock is 597/4800;
production continues", over and over -- while the research the run was launched
for never started. Filling a chest is worth doing in the background; it is not
worth standing still for.

The run now waits for 200 and moves on, while the cell keeps making belts up to
a chest because its logistic gate says 4800.

**Still open -- the promoted line is fed through CHESTS.** Reported as "3 steel
chests on each side ... then did a hop with the gear". `generate_line_layout`
uses `feed_style="chest"`, so each ingredient gets one feed chest per feeder
column (three at 9.00/s through a fast inserter) and the bridge fills those
chests rather than running belt into the line. `feed_style="sideload"` exists
and feeds from belt columns instead. Switching the conversion stage to it is a
real layout change and is NOT done.

## A feed endpoint nothing could fill

**Files:** `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`,
`tests/test_feed_endpoints.py`

**The fault:** a transport-belt line was built with its gear belt connected and
its iron-plate belt not, and stayed that way -- seven machines, zero working.

**Two causes, compounding.**

1. `_swap_infinity_chests` gave a BELT-mode feed endpoint a plain `steel-chest`.
   Only a belt can fill one. When the iron-plate bridge failed, nothing could
   ever fill it, and the repair pass kept reporting the line as merely
   "supply-starved" -- which reads as "upstream is slow" rather than "this
   ingredient has no supply route at all".

   Every feed endpoint is now a requester chest with a request, per user
   direction: "no need for the steel chest, just directly connect the line or
   put a requester". A requester takes belt input through its inserter exactly
   as a steel chest does, and bots keep the line alive meanwhile. Once the belt
   runs the chest stays full and the request goes quiet on its own, so it costs
   nothing where the belt does exist.

2. The `MaterialShortage` that aborted the bridge was caught and swallowed
   without a word. A stage that failed half-built looked identical in the log to
   one nobody had started. It now names what it is short of and says the stage
   resumes once the mall has it.

**Verified** by reverting both: 4 of 6 tests fail.

## The 4800-belt errand, and why it outlived its fix

**Files:** `orchestrator/priority_list.py`, `orchestrator/construction_stock.py`,
`tests/test_construction_stock.py`

### A persisted target could only ever rise
`PriorityList.sync` did `task.target = max(task.target, target)`, and the list is
saved to disk. So the 4800 written by one run became the target of EVERY later
run -- which is why a log printed "stock target 4800" with no `STOCK CAP LIFTED`
line anywhere above it, and why separating buffer from requirement did not take
effect. The caller's figure is now authoritative; `add_demands` still raises it
within a run by raising the mapping `sync` is given.

### The buffer itself was a milestone, not a buffer
`PROVIDER_CHEST_SLOTS` drops 48 -> 10. Ten stacks is a thousand belts.

User standard, 2026-08-03: "I set the 10 stack limit so that it doesn't waste
the limited resources in building transport belt, and the resources can be used
to build more important things faster", and "the goal is to do science not hit
milestone in transport belt stock".

A belt is iron that did not become a drill, an assembler, or a science pack. At
4800 the run spent forty minutes climbing toward the number, expanding iron
every sixty seconds, at 2496/4800 and still counting.

**No manual cleanup needed:** the stale 4800 in `autonomous_priorities.json` is
overwritten on the next sync rather than inherited.

## A buffer the base has to earn

**Files:** `orchestrator/construction_stock.py`, `orchestrator/autonomous_builder.py`,
`tests/test_construction_stock.py`

**What:** the standing buffer for a bulk construction item is now
`BUFFER_SECONDS` of that item's OWN live output, capped at `MAX_BUFFER_STACKS`,
and floored at whatever the mission asked for. `_live_output_rate` measures it
from the line rather than from stock, which a starter kit inflates.

| the base makes | buffer |
|---|---|
| nothing | 200 (the mission figure) |
| 3/s -- one machine | 200 |
| 6/s | 360 |
| 18/s -- a six-machine line | 1000 |
| 60/s | 1000 (ceiling) |

**Why a rule rather than a number.** 4800 was replaced with 10 stacks and the
same class of fault remained available, because the fault was never the figure.
The rule is: SPEND ON CAPACITY WHILE CAPACITY IS SCARCE, AND STOCKPILE ONLY OUT
OF SURPLUS. A base that cannot make something quickly does not hold much of it,
so the plates go to whatever would make more; a base that already makes them
fast can afford a bigger buffer by construction.

User standard, 2026-08-03: "I set the 10 stack limit so that it doesn't waste
the limited resources in building transport belt, and the resources can be used
to build more important things faster", and "conserve resource usage, and make
more resources, so that it can then use the resources a little more freely".

The ten-stack ceiling survives as a CEILING -- past it, stock is material
sitting in a chest instead of doing something -- but a six-machine line arriving
at roughly a thousand belts is now a consequence of the rule, not the rule.

**Terminology, corrected.** "Sideload" here means the game mechanic: a feeder
belt T-junctions into the side of an input belt so its item occupies one lane
and another item can use the other. `feed_style="sideload"` in
`line_layouts.py` already means exactly that, and docs/21 records it. Earlier
notes in this file describing it loosely as "belt-fed columns" understate it.
