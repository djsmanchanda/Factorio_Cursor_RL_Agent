# Path: Factorio_Cursor_RL_Agent/CURRENT_STATUS.md
# Purpose: Append-only project status log (AGENTS.md §5). First thing any agent reads when resuming work.

# CURRENT_STATUS

Append-only. Newest entries at the bottom. Entries before 2026-07-18 are
backfilled from git history because this file did not exist yet.

## [2026-08-21] Linux dashboard lifecycle timeout fix
- Files: scripts/manage_linux_deterministic_server.sh, scripts/manage_linux_training_worker.sh, tools/dashboard_runtime.py, focused Linux/dashboard tests
- What: Redirected the FIFO keeper's inherited stderr so captured dashboard subprocesses can exit after Factorio reaches RCON readiness; native server lifecycle commands now allow up to five minutes for slow save migration.
- Evidence: Focused suite passes (17 tests); deterministic server remains running on 127.0.0.1:34199 with RCON tick probe returning 165864.

## [2026-08-21] Linux dashboard deploy lifecycle fix
- Files: tools/dashboard_runtime.py, tests/test_dashboard_runtime.py
- What: Native Linux `deploy_mod` now stops the runner/server before deployment, starts Factorio after synchronizing both mod copies, and restores the runner if it was active. Full refresh avoids duplicating that lifecycle.

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

## Promotion now needs a backlog, not just a busy machine

**Files:** `orchestrator/intermediate_scaling.py`,
`orchestrator/autonomous_builder.py`, `tests/test_promotion_patience.py`,
`tests/test_extraction_separation.py`

**The fault:** transport-belt was promoted to a six-machine line -- eighteen a
second, with six feed requesters to supply -- for a stock target of 200 that one
cell covers in about a minute. Capacity built for work already nearly done, paid
for in the plates everything else was waiting on.

**Why saturation alone was the wrong trigger.** A single mall cell runs flat out
whenever it has ANY work, so `saturated` was true the entire time it filled a
one-off chest. Measured demand was 0.00/s and the promotion happened anyway,
because saturation bypassed the rate limit entirely.

`backlog_seconds` now asks how long the cells already built need to finish what
is OUTSTANDING, and saturation only counts when that exceeds
`PROMOTION_PATIENCE_SECONDS`. Measured with the reported numbers:

| case | backlog | promoted to |
|---|---|---|
| 200 target, 42 stocked, 1 cell | 53 s | not promoted |
| a line that never clears its backlog | 33333 s | 6 machines |
| real demand above the rate limit | n/a | 6 machines |

Infinite backlog when nothing is built, so the first cell is never blocked.

The promotion log line now carries the outstanding count and the backlog, since
"all 1 machine(s) running flat out" gave no way to see the decision was wrong.

**Verified** by removing the patience check: 2 tests fail.

**Still open -- a bridge collided with the stage it was feeding.**
`bridge_copper-plate_to_copper-cable` tried to place belt at (117.5,-18.5), held
by a fast-inserter belonging to the copper-cable line built 25 seconds earlier.
`occupied_tiles` DOES count ghosts (a ghost's `e.name` is `entity-ghost`, never
in the ignore list), so the survey should have seen it. Not diagnosed further,
and NOT fixed.

## A bridge that cannot reach its endpoint now says so

**Files:** `orchestrator/stage_transport.py`,
`orchestrator/extraction_transport.py`, `tests/test_blocked_feed_endpoint.py`

**The fault, found:** `_clear_side` ended with `return preferred`. Its docstring
said so deliberately -- "falls back to the preferred side when nothing is clear,
so the caller still gets a plan and a real placement error rather than a silent
no-op".

So when EVERY side of a feed chest is occupied, the bridge attached to a blocked
one anyway and emitted belt onto whatever stood there. That is what put a
transport-belt ghost at (117.5,-18.5) on top of a fast-inserter the same system
had placed twenty-five seconds earlier: the copper-cable line's feed chest is
flanked by the line's OWN feed inserters, so no side was free.

The plan was invalid before it was ever submitted. The executor's
`exact_position_occupied_by_different_entity` was not a detection -- it was the
planned outcome arriving.

**What changed.** `_clear_side` returns None when no side is usable, and every
caller states which endpoint cannot be reached. A plan known to collide is not a
better diagnostic than a stated failure; it is a build that damages the base and
then reports the damage.

**A second, unguarded call** was found while testing: `extraction_transport`
passed `_clear_side(...)` straight into `bridge_chest_to_chest` as
`exit_direction`, so None would have leaked in as a direction. Guarded too.

**Ruled out along the way,** so it is not re-investigated: `occupied_tiles` DOES
count ghosts -- a ghost's `e.name` is `entity-ghost`, never in the ignore list --
and the survey box did cover the tile. The blocked set was correct; the decision
that consumed it was not.

**Verified** by restoring `return preferred`: the surrounded-chest test fails.

## [2026-08-04] Mall topology and real stock-stall detection
- Files: `orchestrator/autonomous_builder.py`, `orchestrator/parts_mall.py`, `tests/test_live_mall_recovery.py`, `tests/test_parts_mall_progress.py`
- What: Paired mall cells are recognized during science calls, growing stock postpones capacity expansion, and real expansion shortages are logged.
- Why: A deferred gear promotion fell back into belt-line recovery for two valid mall assemblers, while a fixed timer repeatedly planned the same unaffordable iron expansion even as construction stock rose.
- Next: Restart the Python runner from the safe save and rerun `mining-productivity-4`; no Lua mod redeploy is needed.

## [2026-08-04] Mall storage limits no longer drive production
- Files: `orchestrator/autonomous_builder.py`, `planners/mall_layout.py`, `tests/test_construction_stock.py`, `tests/test_stock_gating.py`
- What: Construction mall cells gate production at the current requirement while provider bars remain capacity ceilings; existing cells are reconfigured and intermediate prep cells remain ungated.
- Why: The real build path dropped the machine gate and reused chest capacity for ingredient sizing, so a stack limit behaved like an instruction to consume scarce inputs.
- Validation: `1217 passed, 1 skipped`.
- Next: Restart the Python runner from the safe save and rerun `mining-productivity-4`; no Lua mod redeploy is needed.

## [2026-08-04] Mall reserves prebuild to exact stack limits
- Files: orchestrator/construction_stock.py, orchestrator/autonomous_builder.py, orchestrator/mall_builder.py, planners/mall_layout.py, factorio_mod/layout_executor.lua, factorio_mod/logistic_sections.lua, schemas/build_plan.schema.json, docs/21_external_game_knowledge.md, docs/reference/inserter_throughput_factorio_2_0_26.txt, and focused tests
- What: Bootstrap mall cells now maintain deterministic reserves (four live stacks for bulk construction parts, one for machines), grow when one job exceeds half the reserve, and remove their bar/gate after a live assembling-machine-3 producer proves the base mature; the runner still advances at the immediate job quantity.
- Why: The chest stack setting is a production reserve for future expansion, not merely storage permission and not the mission readiness target. This entry supersedes the preceding storage-limit interpretation.
- Reference: Preserved the complete user-supplied Factorio 2.0.26 inserter table as diagnostic evidence; active row selection remains deferred until live capacity bonus, quality, belt, and geometry context is exported.
- Validation: 1221 passed, 1 skipped; schema valid; Python compilation passed; git diff --check clean after cleanup.
- Next: Redeploy the Lua mod and restart the Factorio server/runtime, then restart the Python runner from the safe save. The next code milestone is active stall diagnosis: recursively expand deficient inputs, repair delivery, upgrade inserters, then upgrade/add assemblers.

## [2026-08-04] Background reserves and cohesive smelter growth
- Files: `orchestrator/autonomous_builder.py`, `tests/test_cohesive_smelter_expansion.py`, and focused stock, prep, feed, and belt-tier regressions
- What: Opening mall reserves now start producers without waiting for the reserve quantity; exact construction shortages remain blocking. Plate extraction expansion now validates one managed smelter, rate-sizes total furnace capacity, and extends that row using addition-only direct-belt geometry instead of choosing another refinery site.
- Why: The run waited for a stale transport-belt=200 readiness target before serving the real fast-belt/drill shortage, while each mining phase independently searched for a new smelter and created the iron/copper belt tangle.
- Safety: Existing side-tap providers and both belt trunks remain in place; westbound rows grow away from their fixed ore handoff. Ambiguous legacy multi-site rows fail before another drill plan is submitted.
- Validation: `1228 passed, 1 skipped`; Python compilation, function-size audit, and `git diff --check` passed.
- Next: Python-only round -- restart the runner from the safe save; no new Lua redeploy is required for this change. The next transport milestone is the reserved multi-row mine/smelter blueprint with phase-1 splitters and fixed future corridors.

## [2026-08-05] Direct mine-to-smelter belts and stone-brick production
- Files: `planners/recipe_data.py`, `planners/belt_bridge.py`, `orchestrator/live_base.py`, `orchestrator/stage_transport.py`, `orchestrator/extraction_transport.py`, and belt/recipe regressions
- What: Added the live `2 stone -> 1 stone-brick` electric-furnace recipe; pending mine ghosts now count as the planned through-belt; ore bridges turn immediately into the downstream furnace trunk with no automatic chest/inserter buffer.
- Why: The runner saw an unfinished mine belt as absent and downgraded its side-tap provider into a terminal chest source, creating two inserter-limited belt hops. The belt endpoint constraint also requested the opposite final facing.
- Live evidence: Read-only survey on `nauvis/player` confirmed the chest drain at `(12.5,-4.5)`, separate vertical belt, and furnace-feed inserter at `(13.5,-30.5)`.
- Validation: `1231 passed, 1 skipped`; exact iron coordinates produce 31 continuous belt actions and zero chests/inserters; Python compilation and `git diff --check` passed.
- Next: Python-only round -- restart the runner from the safe save; no Lua mod redeploy is required.
## [2026-08-05] Move raw-mine side taps off the direct turn column
- Files: `planners/resource_layouts.py`, `orchestrator/stage_extraction.py`, `orchestrator/stage_transport.py`, and extraction/belt regressions
- What: Iron and copper mine output belts now turn on the clear tile west of the shifted side tap; the provider chest is two tiles east and the through-belt detector returns the correct upstream handoff.
- Why: The previous direct bridge still placed its vertical turn through the side-tap inserter/chest, so the tap blocked the ore line and the bridge could continue straight past the intended turn.
- Validation: `1232 passed, 1 skipped`; focused raw-mine/belt tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.

## [2026-08-05] Direct raw belts and stone mine classification
- Files: `core/science_recipe_graph.py`, `planners/resource_layouts.py`, `orchestrator/stage_extraction.py`, `orchestrator/stage_transport.py`, `orchestrator/extraction_state.py`, and focused regressions
- What: Stone is now an authoritative Nauvis mineable input; dedicated iron/copper refinery mines emit belt-only output, transport detects the direct belt, and restart surveys classify belt-only rows without inventing a chest source.
- Why: Stone-brick was rejected as non-mineable, while raw ore was routed through a side-tap chest/inserter that blocked the direct furnace belt and caused the observed two-hop transport mess.
- Validation: `103 passed, 1 skipped` focused extraction/transport/recipe tests; Python compilation passed. A full-suite retry reached 163 tests but was blocked by the host pytest temp-directory ACL, not a test failure.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required. Capture the next live log for any remaining power-network issue.
## [2026-08-05] Remove side-fed belt joins from refinery transport
- Files: `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`, and belt-feed regressions
- What: Runtime belt-to-belt bridges now receive the furnace belt direction and join the downstream belt tile, keeping raw refinery transport continuous; the runtime path no longer falls back to a side-loading inserter bridge.
- Why: The preflight already used the destination direction, but the actual build path dropped it, so the submitted geometry could meet a furnace belt at a T-shaped upstream tile. That is the side-fed ore path visible in the latest screenshot.
- Validation: `60 passed`; Python compilation passed.
- Next: Python-only round -- reset the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Fail closed on missing refinery source belts
- Files: `orchestrator/stage_transport.py`, `orchestrator/extraction_transport.py`, and belt-feed regressions
- What: If a refinery destination is belt-fed but its source belt cannot be detected, preflight and build now stop instead of generating a chest/inserter T-feed.
- Why: A missing source survey must never degrade into side-loading all ore onto one belt lane.
- Validation: `61 passed`; Python compilation passed.
- Next: Python-only round -- reset the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Stone-brick shortage must remain recoverable
- Files: `orchestrator/autonomous_builder.py`, `tests/test_belt_survey_and_tiers.py`
- What: Beltless logistic smelting fallback is now limited to iron/copper plates; stone-brick and other unsupported furnace recipes return the belt `MaterialShortage` so the mall can produce more belts and retry.
- Why: The latest run mined stone successfully, then exhausted belt tiers and called a logistic smelter that explicitly rejects `stone-brick`, ending with `ValueError`.
- Validation: `71 passed`; Python compilation passed.
- Next: Python-only round -- reset the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Enforce inline refinery-bus joins and early belt shortages
- Files: `orchestrator/extraction_transport.py`, `orchestrator/stage_transport.py`, `orchestrator/autonomous_builder.py`, and focused belt tests
- What: Raw ore bridges now may enter a furnace input belt only from its upstream end, aligned with the bus flow; non-plate refinery belt shortages stop at the first requested early belt rather than escalating through express/turbo.
- Why: The endpoint selector accepted a clear north/south approach, producing the observed T merge and lane compression; the bootstrap tier loop raised its final turbo shortfall for stone-brick even though faster belts were unnecessary.
- Validation: `28 passed` focused belt/tier tests and Python compilation; generated blocked-bus geometry ends with two westbound tiles into the westbound bus.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.## [2026-08-05] Preserve refinery belt direction through detours
- Files: planners/belt_bridge.py, 	ests/test_belt_detour.py`r
- What: Detour search now honors the declared source exit direction, rejects immediate U-turns, and regression coverage checks the first belt orientation.
- Why: Live iron routing reversed the first belt and copper routing generated duplicate/side-feed geometry; the failed belt stage prevented refinery power bridging.
- Validation: 71 focused belt/extraction tests passed; Python compilation passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Propagate source direction into refinery build execution
- Files: orchestrator/stage_transport.py`r
- What: The live build path now passes the surveyed source exit direction to belt-to-belt refinery bridges, matching preflight.
- Why: The prior rerun loaded the detour fix but the executor discarded exit_direction, regenerating the same malformed iron/copper routes.
- Validation: 82 focused belt/extraction/tier tests passed; Python compilation passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Remove refinery endpoint backtracking and obsolete mine side-tap migration
- Files: planners/belt_bridge.py, orchestrator/autonomous_builder.py`r
- What: Belt-to-belt bridges now route to a direction-compatible approach before joining the existing endpoint, and legacy tap migration skips outputs that are already direct belts.
- Why: The prior route doubled back through (77.5, -18.5) and emitted duplicate endpoint ghosts; the legacy migration then tried to place a side tap over the direct iron belt. Both failures occurred before refinery power repair.
- Validation: 82 focused belt/extraction/tier tests passed; Python compilation passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Enforce direct ore refinery feeds and full construction coverage
- Files: orchestrator/autonomous_builder.py, planners/belt_bridge.py`r
- What: Single-input iron/copper refineries are forced onto direct belt mode; endpoint routing avoids backtracking; conversion plans extend construction-roboport coverage to their full action bounds before ghost submission.
- Why: Side-feed fallback remained possible, and long bridge endpoints such as (103, -12.5) could lie outside construction coverage even when the stage origin was covered.
- Validation: 82 focused belt/extraction/tier tests passed; Python compilation passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Recover partial plate refineries after belt failures
- Files: `planners/belt_bridge.py`, `orchestrator/autonomous_builder.py`, `orchestrator/live_base.py`, `orchestrator/stage_transport.py`, and belt/smelter regressions
- What: Inline refinery bridges now stop at the existing downstream bus tile; idle or ghosted plate-furnace rows are structurally recovered, and partial conversion submits attempt to reconnect their placed substation before re-raising.
- Why: The copper bridge submitted the bus endpoint a second time, so the non-transactional executor left a no-power refinery island before the normal power phase. Recipe-less idle furnaces were then invisible to `find_line`, allowing duplicate sites.
- Validation: 91 focused tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Keep guarded extraction preflight resolvable
- Files: `orchestrator/extraction_transport.py`, `orchestrator/stage_transport.py`
- What: Extraction preflight now imports the fail-closed side selector, and the build path keeps an explicit guarded endpoint assignment before direct-belt override.
- Why: Full test collection exposed an undefined `_clear_side` name that would only fail when a chest-to-chest extraction path ran; the explicit assignment also preserves the blocked-endpoint contract.
- Validation: 155 focused tests passed; Python compilation passed. The broader suite reached 1232 passed/1 skipped before only host temp ACL failures remained.
- Next: Python-only round -- reset the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-05] Derive recovery power from the canonical plate layout
- Files: `orchestrator/autonomous_builder.py`, `tests/test_cohesive_smelter_expansion.py`
- What: Existing-row recovery now recomputes the actual substation position from the surveyed row's belt, inserter, direction, and size, covering long and westbound rows.
- Why: A fixed short-row offset would reconnect the wrong tile once a refinery grew or flowed west.
- Validation: 219 focused transport/extraction/smelter/integrity tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.
## [2026-08-06] Cover complete mining blueprints before submission
- Files: `orchestrator/autonomous_builder.py`, `tests/test_mining_coverage.py`
- What: New mine plans now extend construction-roboport coverage to all action-footprint corners before submitting drill, belt, and pole ghosts; non-RCON test doubles safely skip the live coverage probe.
- Why: The stone run covered only the mine origin, leaving a pole/drill ghost outside construction range. The stage then misdiagnosed the missing build as an unpowered machine and retried an unhelpful substation bridge until it stopped.
- Validation: 220 focused transport/extraction/coverage/integrity tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.

## [2026-08-06] Ignore direct belt endpoints in logistic coverage checks
- Files: orchestrator/autonomous_builder.py, tests/test_extraction_separation.py
- What: Existing mine servicing no longer treats a direct belt endpoint as a logistic chest; added regression coverage.
- Why: The 2026-08-06T01:15 run falsely stalled on `logistic_network=None` for the belt at (49.5, -65.5), preventing the stone-brick refinery from being reached.
- Next: Restart the Python runner before the next live run; no Lua redeploy is needed.
## [2026-08-06] Recipe-aware capacity, persistent precursors, and denser mall cells
- Files: `orchestrator/stage_extraction.py`, `orchestrator/stage_chemical.py`, `orchestrator/autonomous_builder.py`, `orchestrator/mall_builder.py`, and focused regressions
- What: Furnace counts now use input draw (stone-brick is no longer overbuilt), direct coal belts are not probed as logistic chests, legacy steel output rows remain expandable, stocked iron-stick/steel-plate bootstrap persistent producers, and mall pitch is reduced to `(11, 6)` with collision coverage.
- Why: The latest run starved iron, ran stone furnaces below half capacity, consumed starter steel/sticks without a refill line, and left avoidable mall dead space.
- Validation: Focused precursor, extraction, smelter, and mall suites passed; Python compilation passed. These are Python/test changes only.
- Next: Reset to the safe save and restart the autonomous runner; no Lua mod redeploy is required.
## [2026-08-06] Early belt tiering and mall-demand plate sizing
- Files: orchestrator/stage_transport.py, orchestrator/extraction_transport.py, orchestrator/autonomous_builder.py, orchestrator/baseline_production.py, orchestrator/intermediate_scaling.py, planners/resource_layouts.py, tests
- What: Raw refinery routes are capped at regular/fast belts, coal defaults to regular belts, plate rows merge recipe-less furnaces from stable geometry, and finite mall targets add a bounded five-minute plate draw.
- Why: Starter express stock was being mistaken for required capacity, starved furnaces made iron expansion look like a new/short row, and mall construction demand was absent from extraction sizing.
- Next: Restart the Python runner on the safe save; no Lua redeploy is required.
## [2026-08-06] Keep plate refineries direct and diagnose stalled ghosts
- Files: `orchestrator/autonomous_builder.py`, `orchestrator/build_diagnostics.py`, `orchestrator/live_base.py`, `tests/test_belt_survey_and_tiers.py`, `tests/test_ghost_diagnostics.py`
- What: Removed the copper/iron requester fallback; belt shortages now become mall demand and retry the direct mine-to-furnace route. Read-only ghost probes now report coverage, robot, and material blockers instead of a generic no-op stall.
- Why: The latest run had no copper refinery because both belt tiers were unaffordable, then fed a requester from a belt endpoint that could never enter logistics; stone ghosts later stopped with no recorded blockage.
- Validation: 75 targeted tests passed; 41 critical prep/extraction tests passed; Python compilation passed. The test suite reached 1252 passed/1 skipped, with only existing host temporary-directory ACL failures (2 failed/13 errors).
- Next: Python-only round -- reset the safe save and restart the autonomous runner; no Lua mod redeploy is required.
## [2026-08-06] Bound narrow water crossings in fluid routing
- Files: `planners/fluid_routing.py`, `orchestrator/live_base.py`, `orchestrator/stage_chemical.py`, `tests/test_fluid_routing.py`
- What: Chemical routes now survey water terrain, use paired pipe-to-ground endpoints for water runs under nine tiles, and reject wider crossings for a route-around retry.
- Why: The supplied run ended at a bounded petroleum-gas route refusal; the fluid planner could only route around water and could not emit a legal narrow-water underground crossing.
- Validation: 175 focused fluid/electronics/extraction/belt tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the runner; no Lua mod redeploy is required.

## [2026-08-06] Reopen iron prep for later construction demand
- Files: `orchestrator/autonomous_builder.py`, `tests/test_prep_extraction.py`, `tests/test_cohesive_smelter_expansion.py`
- What: Plate prep now re-evaluates completed baseline capacity against later mall shortages, and refinery expansion recovers the deployed row before merging recipe-less furnaces.
- Why: A 685-pipe chemical-cell bill waited 13 minutes while iron remained at its opening capacity: completed prep ignored the new bounded five-minute plate draw, then expansion compared the live row with a newly searched refinery site.
- Validation: 111 focused prep/smelter/extraction/mall-progress/run-bound tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the autonomous runner; no Lua mod redeploy is required.

## [2026-08-06] Stage landfill before wide-water oil pipes
- Files: `schemas/build_plan.schema.json`, `factorio_mod/layout_executor.lua`, `planners/fluid_routing.py`, `orchestrator/stage_chemical.py`, `orchestrator/live_base.py`, `orchestrator/stage_services.py`, focused regressions, and `docs/23_fluid_systems.md`
- What: Added authorized landfill tile ghosts, then split oil-cell construction into landfill foundation and pipe-route submissions; long straight water runs now use legal ten-tile underground spans with a landfill pair between spans. Tunnel endpoint facings now connect successive spans, while one-land-tile gaps fail closed.
- Why: Chemical routing both rejected wide water and accidentally removed all water from its tunnelable survey because general occupancy includes water; pipe ghosts must never be submitted before their water tiles become solid.
- Validation: 121 focused fluid/schema/executor/preflight/layout tests passed; Python compilation, JSON validation, and `git diff --check` passed. Lua is static-contract tested; no live mod deployment was performed.
- Next: Deploy the Lua mod copy and restart the Factorio server, then reset to the safe save and restart the Python runner for a real Nauvis/player validation.

## [2026-08-06] Keep mine and furnace expansion in one material gate
- Files: `orchestrator/autonomous_builder.py`, `tests/test_cohesive_smelter_expansion.py`, `tests/test_prep_extraction.py`, `tests/test_plate_prep_fairness.py`
- What: Plate expansion now checks the combined mine and smelter ghost bill before it submits the mine; a temporary material shortage records its exact bill and waits for stock rather than launching another mining phase or marking prep deferred. An expansion without a recoverable refinery, including stone-brick, fails before drill placement.
- Why: The 13:25 run grew iron mining, then the 7-to-19 furnace extension lacked 74 fast belts; a later siting deferral prevented the completed belt stock from ever retrying that furnace work.
- Validation: 112 focused prep/extraction/smelter/promotion tests passed; Python compilation and `git diff --check` passed.
- Next: Python-only round -- reset to the safe save and restart the autonomous runner; no Lua mod redeploy is required.

## [2026-08-06] Preflight plate-refinery expansion ground and direct ore handoff
- Files: `orchestrator/autonomous_builder.py`, `tests/test_cohesive_smelter_expansion.py`
- What: Westbound direct ore rows now keep their existing mine-side belt endpoint; refinery expansion surveys its full added footprint before drill placement, rejects occupied infrastructure, and stages/waits for landfill on water-only tiles.
- Why: The 14:59 iron expansion regenerated a chest-feed extension, attempted to replace the live ore trunk at `(24.5, 31.5)`/`(25.5, 31.5)`, and only failed after 12 new drills had been placed. Water must be made solid before any furnace ghost is submitted.
- Validation: 66 focused expansion/prep/extraction tests passed; reviewer approval confirmed west/east direct-feed and foundation ordering.
- Next: Python-only round if the prior landfill-tile-ghost Lua deployment is already active; otherwise deploy that existing Lua change and restart the server before testing on the safe save.

## [2026-08-06] Pin modular refinery templates and safe tail migration
- Files: `planners/refinery_blueprints.py`, `planners/smelter_block.py`, `schemas/build_plan.schema.json`, `factorio_mod/{layout_executor,logistic_sections}.lua`, focused tests
- What: Imported and hash-pinned the approved Start/Middle/End blueprints, preserved splitter priorities through BuildPlan 1.4 and Lua, widened through five columns, and made depth growth retire only the planner-owned End delta before adding Middle rows and a new End.
- Why: Refinery geometry must be planned as one repeatable belt/furnace system; expansion may migrate its terminal cap but must never infer permission to remove Start, Middle, or unrelated live infrastructure.
- Validation: 73 focused blueprint/schema/executor/layout tests passed; Python compilation, schema validation, function-size checks, and `git diff --check` passed; reviewer confirmed the corrected 15-tile width and End-only removal boundary.
- Next: Wire this validated primitive into live refinery siting, recovery, direct ore input, plate output, landfill, and power before deploying it.

## [2026-08-06] Integrate modular refineries into live extraction
- Files: `orchestrator/{autonomous_builder,extraction_transport,live_base,refinery_state,stage_extraction,stage_transport}.py`, `planners/smelter_block.py`, focused tests
- What: The Nauvis/player extraction path now builds Start+End refineries, widens to five columns, then expands by retiring only the verified End/output cap, inserting Middle rows, and placing a new End; ore stays on one affordable head-on belt with no chest or inserter hop.
- Why: Incremental furnace rows and independently planned bridges created disconnected power, T-side-feeds, wrong terminal directions, scattered refinery sites, and unsafe expansion over real infrastructure or water.
- Validation: 1303-test full suite passed with 1 skipped; final modular/refinery regression suite passed 107 tests after the handoff-geometry fix, plus Python compilation and diff checks.
- Next: Deploy the already-committed BuildPlan 1.4 Lua mod changes and restart the Factorio server, then restart the Python runner from the safe save.

## [2026-08-07] Add regular-belt refinery bootstrap
- Files: `planners/refinery_basic_blueprints.py`, `planners/refinery_blueprints.py`, `planners/smelter_block.py`, `orchestrator/{autonomous_builder,refinery_state}.py`, focused refinery tests
- What: Added the supplied regular-belt Start/Middle/End templates; the first refinery now uses one six-furnace basic Start+End module, while later growth migrates to the standard fast-belt templates.
- Why: The previous first refinery required 131 fast belts before iron plates could be produced, creating a circular bootstrap deadlock.
- Validation: 77 focused blueprint/smelter/state/extraction tests passed; Python compilation passed.
- Next: Reset to the safe save and restart the Python runner; no Lua mod redeploy or Factorio server restart is needed.

## [2026-08-07] Break initial plate and transport-belt bootstrap deadlock
- Files: `orchestrator/autonomous_builder.py`, `tests/test_cohesive_smelter_expansion.py`, `tests/test_persistent_intermediates.py`, `tests/test_prep_extraction.py`
- What: Compact mall producers now seed from one craft instead of the full stock target; a blocked plate preflight stops later baseline plate mining from spending the starter belt reserve; the initial refinery transaction permits only its authorized mine-output interface tiles during the second preflight.
- Why: The run queued 116 belts for iron before any iron line existed, then demanded 58 plates before placing the belt assembler and spent the same pass on copper mining; copper's subsequent refinery preflight misclassified its own output belt at `(77,-40)` as foreign infrastructure.
- Validation: 88 focused tests passed; Python compilation and `git diff --check` passed. Python-only change -- restart the runner; no Lua mod redeploy or Factorio server restart is required.
## [2026-08-07] Use fast-belt shortfall fallback and repair refinery startup
- Files: `orchestrator/{stage_transport,extraction_transport,autonomous_builder,mine_retirement}.py`, `planners/refinery_blueprints.py`, focused tests
- What: Reserved regular belts are protected for the initial refinery while stocked fast belts can fill only the remaining direct route; malformed mine surveys now fail closed, and refinery furnace ghosts no longer send unsupported recipes to Factorio.
- Why: The live run stalled at 108/116 belts, then crashed on a malformed retirement survey; when plates were added manually, every furnace ghost failed with `recipe_set_failed`.
- Validation: 47 focused belt/refinery/mine tests passed; Python compilation and `git diff --check` passed. Python-only change: restart the runner; no Lua deployment is needed.
## [2026-08-07] Repair partially powered mining stages
- Files: `orchestrator/autonomous_builder.py`, `tests/test_ghost_diagnostics.py`
- What: Stage power recovery now bridges stranded no-power machines when the stage substation is already on the generating network.
- Why: Stone mining had one drill on an unpowered local network while its substation was already on network 1, so the old remedy found no second powered network and stopped.
- Validation: Focused ghost-diagnostics tests passed; Python-only change, so restart the runner; no Lua deployment or Factorio server restart is needed.

## [2026-08-07] Size basic refinery output collectors
- Files: `planners/smelter_block.py`, `tests/test_smelter_block.py`
- What: The six-furnace basic refinery output tap now sizes its chest collector from total furnace output and selects a fast inserter.
- Why: The terminal collector at `(13.5, 42.5)` was hard-coded to a plain inserter, throttling the 3.75/s output of the bootstrap refinery.
- Validation: 40 focused smelter/state/refinery tests passed; Python-only change, so restart the runner; no Lua deployment or Factorio server restart is needed.

## [2026-08-06] Bound dashboard runner history and separate completed priorities
- Files: `tools/{autonomous_run,dashboard_runtime,runner_log_retention,runner_process}.py`, `tools/dashboard.{html,js,css}`, focused dashboard tests
- What: The live runner log now retains three sessions and archives older sessions/files; dashboard polling uses a validated PID record instead of repeated WMI scans, and construction priorities have separate Active and Completed tabs.
- Why: The 2.29 MB live log held 99 sessions since July 29, while every status poll launched an access-denied CIM query and 15 completed tasks obscured the three active tasks.
- Validation: 27 focused tests passed; Python compilation, JavaScript syntax, and `git diff --check` passed. Live migration retained 3 sessions (183366 bytes) and archived 96 sessions plus 15 legacy log files.

## [2026-08-06] Launch dashboard without a persistent shell
- Files: `scripts/launch_dashboard.ps1`
- What: The launcher now starts the server through hidden `pythonw.exe`, reuses an already-online dashboard, waits for readiness, and opens the browser without a `powershell.exe -NoExit` host.
- Why: The previous launcher left a visible background PowerShell window for the dashboard lifetime.
- Validation: PowerShell parsing passed; live launch returned HTTP 200 on port 9137 under hidden PID 17432 with RCON online and the runner stopped.
## [2026-08-07] Reserve refinery-plan footprints before roboport chaining
- Files: `orchestrator/{autonomous_builder,roboport_placement,stage_services}.py`, `tests/test_{logistic,mining}_coverage.py`
- What: Construction-coverage roboport candidates now avoid the complete footprint of the plan that is about to be submitted.
- Why: The run left a medium-pole ghost at `(51.5,-59.5)` overlapping a `(53,-58)` roboport; the drills were powered, but the invalid ghost made the stage spend six rounds and report the stale earlier power issue.
- Validation: 50 focused tests passed; Python-only change, so restart the runner; no Lua mod redeploy or Factorio server restart is needed.
## [2026-08-07] Fix managed-mine survey position encoding
- Files: `orchestrator/mine_retirement.py`, `tests/test_mine_retirement.py`
- What: Managed-mine RCON surveys now emit `{x,y}` positions instead of nested `{{x,y}}` tables.
- Why: The malformed Lua caused every iron expansion retry to defer with `real number expected got table`, leaving the mine/refinery at its starter capacity.
- Validation: 40 focused extraction/coverage/diagnostic tests passed; Python-only change, so restart the runner; no Lua redeploy is required.
## [2026-08-07] Preserve basic refinery geometry during growth
- Files: `orchestrator/autonomous_builder.py`, `tests/test_cohesive_smelter_expansion.py`
- What: Refinery expansion keeps a basic Start/Repeat/End variant instead of implicitly migrating to the standard two-row interface.
- Why: The automatic migration collided with existing starter infrastructure before adding capacity, after mine retirement had already removed the depleted mine.
- Validation: 41 focused refinery/extraction tests passed; Python-only change, so restart the runner; no Lua redeploy is required.

## [2026-08-07] Defer depleted-mine retirement until expansion preflight
- Files: orchestrator/autonomous_builder.py, 	ests/test_cohesive_smelter_expansion.py`r
- What: Expansion keeps the old mine operating until replacement mine/refinery collision and affordability checks pass.
- Why: The prior ordering deconstructed the only iron mine before a refinery footprint conflict was discovered, leaving the base without iron production.
- Validation: 55 focused tests passed; Python-only change, so restart the runner; no Lua redeploy is required.

## [2026-08-07] Refinery capacity schedules
- Files: planners/smelter_block.py, orchestrator/autonomous_builder.py, tests/test_smelter_block.py, tests/test_cohesive_smelter_expansion.py
- What: Added explicit staged refinery capacities and exact scheduled lattice shapes; generation-1 growth now stops at 48 instead of rounding to an oversized block.
- Why: Keep each refinery within its planned generation envelope and prevent mine expansion from silently overbuilding the starter footprint.
- Next: Add generation-aware new-site placement before enabling generation-2+ schedules in the live builder.

## [2026-08-07] Record plate providers for persistent intermediates
- Files: orchestrator/autonomous_builder.py, tests/test_persistent_intermediates.py, tests/test_prep_extraction.py
- What: Plate prep now records the refinery provider returned by mining stages; steel refuses arbitrary starter/storage chests and reuses the real iron provider.
- Why: The steel line previously requested iron from the nearest chest at (0.5, -1.5), which was not a reliable producer feed and left the furnace empty.
- Next: Restart the Python runner before the next live validation.

## [2026-08-08] Repair stalled mall intermediates for live consumers
- Files: orchestrator/autonomous_builder.py, tests/test_live_mall_recovery.py
- What: Upgrade/goal consumers now repair supply-starved mall cells; bootstrap-only prep still waits for its reserve.
- Why: Automation science accepted an empty iron-gear provider instead of reconnecting its stalled two-machine cell.
- Next: Restart the Python runner and rerun from the safe save.
## [2026-08-08] Paired mall recovery and power-generation diagnosis
- Files: orchestrator/autonomous_builder.py, tests/test_live_mall_recovery.py
- What: supply-starved paired mall cells now wait on their requester transport instead of being misclassified as deterministic belt lines; the run log also confirmed no power-generation stage exists in the active builder.
- Why: the 02:16:51 run stopped on valid six-tile mall geometry before any electricity policy could run; boilers/steam engines are additionally rejected by the repository's electric-only invariant.
- Next: implement generation only after the electric-only invariant is explicitly revised; this Python fix needs a runner restart, not a mod redeploy.
## [2026-08-08] Dashboard restart elevation reliability
- Files: tools/dashboard_runtime.py, tests/test_dashboard_runtime.py
- What: restart_server now uses the visible user-approvable elevation path and closes the temporary PowerShell window after launching.
- Why: hidden elevation returned Windows STATUS_CONTROL_C_EXIT (3221225786 / 0xC000013A) during restart, while restore's visible launch path succeeded.
- Next: restart the dashboard process before using the Restart server button; no mod redeploy is required.

## [2026-08-08] Target stranded construction ghosts for coverage repair
- Files: orchestrator/autonomous_builder.py, tests/test_ghost_diagnostics.py
- What: Coverage remediation now extends roboports to the exact out-of-range ghost position instead of retrying from only the stage origin.
- Why: Coal mining stalled six rounds on a belt at (25.5, -49.5) while the stage anchor was already covered, so every retry built nothing.
- Next: Restart the Python runner and rerun from the safe save; no Lua mod redeploy is required.
## [2026-08-08] Gate fast belts behind iron bootstrap capacity
- Files: orchestrator/autonomous_builder.py, tests/test_prep_extraction.py, tests/test_cohesive_smelter_expansion.py
- What: Fast-belt production now waits for 24 iron furnaces and 24 iron drills; regular belts or existing fast-belt stock remain usable, and unmanaged/standard starter rows open cheaper basic replacements instead of deferring iron growth.
- Why: Iron prep required 14 furnaces but stayed at six after recovery rejected the starter geometry, while fast-belt demand could create a circular shortage.
- Next: Restart the Python runner and rerun from the safe save; no Lua mod redeploy is required.
## [2026-08-08] Defer mine retirement until replacement is healthy
- Files: orchestrator/autonomous_builder.py, orchestrator/mine_retirement.py, tests/test_cohesive_smelter_expansion.py, tests/test_mine_retirement.py
- What: Depleted mines are retired only after the replacement mine/refinery is built and healthy; retirement now preserves shared belts and power poles.
- Why: The 04:35:37 run removed the only iron source before its 18-furnace replacement bill was available, and the broad teardown included infrastructure shared with the copper line.
- Next: Restart the Python runner and rerun from the safe save; no Lua mod redeploy is required.

## [2026-08-08] Keep opening refinery proportional to mine capacity
- Files: orchestrator/stage_extraction.py, tests/test_extraction_separation.py
- What: Planned furnace counts are capped at available drill capacity before blueprint module rounding; six drills now produce a six-furnace opening line instead of twelve.
- Why: A +30% productivity rate required seven furnaces mathematically, and the six-furnace blueprint lattice rounded seven up to two modules.
- Next: Restart the Python runner and rerun from the safe save; no Lua mod redeploy is required.

## [2026-08-08] Guarded replacement coverage before force-style migrations
- Files: orchestrator/autonomous_builder.py, tests/test_cohesive_smelter_expansion.py
- What: Refinery replacement deltas now stage the complete future construction footprint and power targets before owned removals; roboport removal is rejected without an alternate coverage chain.
- Why: Super-force-style replacement is safe only when it cannot strand belts, power, or construction/logistic coverage; the Lua executor has no safe UI super-force primitive.
- Next: Restart the Python runner after this Python-only change; no mod redeploy is required.
## [2026-08-08] Gate electric-furnace bootstrap demand
- Files: orchestrator/parts_mall.py, orchestrator/baseline_production.py, orchestrator/autonomous_builder.py, tests/test_starter_stock.py, tests/test_prep_extraction.py
- What: Electric furnaces are demand-driven, early iron/copper/stone/steel furnace caps are 12/6/6/6, and new fast-belt production waits for a working electric-furnace producer.
- Why: The former electric-furnace reserve created premature steel, stone-brick, plastic, and advanced-circuit demand that starved the initial resource bootstrap.
- Next: Restart the Python runner; no Lua mod redeploy is required. Plastic blueprint ingestion remains a follow-up.

## [2026-08-09] Establish the mini-environment training contract
- Files: `training/`, `schemas/training_*.schema.json`, `tools/generate_training_scenarios.py`, `tests/test_training_scenarios.py`, `docs/32_training_curriculum.md`, canonical docs
- What: Added strict scenario/transition contracts and a deterministic generator for batches of isolated mining-and-delivery curricula.
- Why: The real-base builder retries hand-authored remedies but cannot persist experience or learn better choices across safe-save resets.
- Next: Add an explicitly authorized Lua provisioner that creates, seeds, validates, and recycles one isolated training surface per episode.
## [2026-08-09] Wait for temporarily reserved construction stock
- Files: orchestrator/autonomous_builder.py, tests/test_ghost_diagnostics.py
- What: A ghost material check now waits and retries when force-wide stock exists but the target construction network temporarily reports zero available items; true stock shortages still become mall demand.
- Why: The 15:39:22 run stopped on one automation-science transport belt even though the transport-belt reserve existed; the network count reflected reservations from other construction jobs, not a missing provider.
- Next: Restart the Python runner; no Lua mod redeploy is required. The separate experimental RL path was not modified.

## [2026-08-09] Training contract and dependency firewall
- Files: `training/canonical.py`, `training/contracts.py`, `training/isolation.py`, `training/scenarios/mining_delivery.py`, training schemas/tests.
- What: Upgraded training artifacts to tick-native v1.1 contracts with canonical scenario/plan/policy hashes, finite-number checks, integral fixtures, and active/training import isolation.
- Why: Thousands of retries need immutable evidence and a hard boundary from the deterministic Nauvis runtime.
- Next: Provision disposable episodes through the separate training lab.

## [2026-08-09] Disposable Factorio training lab
- Files: `factorio_training_lab/**`, `schemas/training_episode_report.schema.json`, Lua contract tests.
- What: Added RCON-only provision/observe/recycle commands, protected fixtures, budget audits, per-tick measurement, and asynchronous force recycling.
- Why: Small live tasks must fail safely and recycle indefinitely without touching Nauvis/player or exhausting Factorio's force limit.
- Next: Execute deterministic candidates and persist learning evidence.

## [2026-08-09] Isolated RL training runtime
- Files: `training/**`, `tools/run_training_batch.py`, `tools/run_autoresearch.py`, `tools/report_training.py`, `docs/33_training_architecture.md`, focused tests.
- What: Added deterministic mining candidates, one-plan episodes, SQLite experience, contextual policy learning, seeded evolution, frozen holdout promotion, 1-20 worker scheduling, and bounded loopback LLM proposals.
- Why: Enable repeated randomized trial-and-error and recursive policy improvement while deterministic planners retain all structural authority.
- Next: Deploy only the training mod to a dedicated worker, benchmark one worker, then scale concurrency from measured UPS and latency.

## [2026-08-09] RL observatory and bounded research guidance
- Files: `training/telemetry.py`, `training/observation.py`, `training/store.py`, `training/episode.py`, `training/research/controller.py`, `tools/run_training_batch.py`, `tools/run_autoresearch.py`, `tools/training_observer.*`, focused tests and training docs.
- What: Added live worker/autoresearch telemetry, incremental durable progress, a loopback read-only dashboard, structured bottlenecks, policy/LLM views, and expiring CLI nudges for bounded autoresearch packets.
- Why: Long 10-20 worker experiments need visible progress and evidence-backed human steering without granting RL or the observer authority over deterministic planners, rewards, safety, deployment, or Nauvis.
- Next: Restart only the Python training batch/autoresearch processes, run `python tools/training_observer.py serve`, and open `http://127.0.0.1:8765`; no Lua or deterministic-mod redeploy is required.

## [2026-08-09] Prioritize mission inputs over background mall reserves
- Files: orchestrator/autonomous_builder.py, tests/test_construction_stock.py
- What: The ready loop now attempts the research goal before starting another background mall producer; a missing prerequisite still queues the normal mall shortage.
- Why: The 17:53 run had 28 starter iron gears in the provider, but background requesters claimed the scarce stock while the gear cell was supply-starved, leaving the automation-science requester empty and ending the run.
- Next: Restart the Python runner and rerun from the safe save; no Lua mod redeploy is required. The separate RL training path was not modified.

## [2026-08-09] Isolated training worker 01 provisioned
- Files: `training-workers.json`; external `C:/Users/djsma/AppData/Local/Factorio-training-01` profile.
- What: Created a dedicated Factorio training save with the training lab and its required executor dependency, running on game UDP 35001 and RCON TCP 28001 with loopback-only worker configuration.
- Why: Provide a disposable live worker for bounded RL episodes without changing the deterministic Nauvis server.
- Next: Set the runner's current-shell password and run a small benchmark before scaling worker count; do not redeploy or restart the deterministic server.
## [2026-08-09] Training execution transport and fixture audit
- Files: `factorio_training_lab/**`, `training/factorio_bridge.py`, training candidates/schema/tests.
- What: Replaced oversized monolithic RCON BuildPlan calls with bounded training-only upload/execute commands, made training candidates physical placements, and validated protected fixtures from their exact surface identity.
- Why: The first live batch spent 120 seconds per episode waiting for dropped 11-13KB RCON commands and falsely rejected intact fixtures through global unit lookup.
- Next: Deploy only `factorio_training_lab` to the isolated worker, restart that training server and the Python batch, then benchmark a small batch before scaling workers.
## [2026-08-09] Reviewed training execution safety gates
- Files: `factorio_training_lab/training_geometry.lua`, execution/measurement bridge, focused tests.
- What: Enforced cumulative per-episode budgets and complete entity footprints before placement; execution failures now produce negative transitions.
- Why: Prevent a training plan from escaping its isolated scenario or vanishing as a generic worker error.
- Next: Deploy only the training lab to worker 01, restart that worker and the Python training batch, then verify a 5-attempt benchmark.
## [2026-08-09] Training observation report mapping
- Files: `training/factorio_bridge.py`, bridge regression tests.
- What: Mapped `training_observe` to the lab's `observation` report kind.
- Why: The first deployed benchmark correctly emitted observations but Python rejected the singular report name as unexpected.
- Next: Restart only the Python benchmark; the training server already has the required Lua revision.
## [2026-08-09] Factorio 2 training prototype lookup
- Files: `factorio_training_lab/training_geometry.lua`, Lua regression tests.
- What: Resolved entity collision boxes through the Factorio 2 `prototypes.entity` API.
- Why: The first physical execution reached the guard, where the removed `game.entity_prototypes` API failed before placement.
- Next: Redeploy only the training lab, restart worker 01, then restart the Python benchmark.
## [2026-08-09] Factorio empty execution-failure representation
- Files: `schemas/training_episode_report.schema.json`, training-lab schema tests.
- What: Accepted Factorio's `{}` serialization for an empty Lua `placement_failures` table.
- Why: Physical executions succeeded but the Python schema rejected the empty success detail as an object instead of an array.
- Next: Restart only the Python benchmark; no Lua deployment is required.
## [2026-08-10] Training primary-research baseline
- Files: `factorio_training_lab/episode_world.lua`, training docs and Lua regressions.
- What: Completed finite primary technologies for each disposable training force while excluding repeatable research.
- Why: Layout exercises need their construction technologies unlocked without changing real-base research or polluting research-focused curricula.
- Next: Deploy only the training lab, restart worker 01, then restart the training batch.
## [2026-08-10] Runtime-safe training primary research completion
- Files: `factorio_training_lab/episode_world.lua`, training-lab regression tests.
- What: Completed each finite technology through its numeric maximum level and excluded Factorio's numeric infinite-level sentinel.
- Why: The runtime API represents infinite levels as `4294967295`, not the data-stage string form.
- Next: Deploy only the training lab, restart worker 01, then restart the training batch.
## [2026-08-10] Training research final-level completion guard
- Files: `factorio_training_lab/episode_world.lua`, Lua regression tests.
- What: Included unresearched technologies already at their maximum numeric level.
- Why: Factorio represents a pending single-level technology as `level == max_level` until its researched flag is set.
- Next: Deploy only the training lab, restart worker 01, then restart the training batch.
## [2026-08-10] Scoped training-worker launcher
- Files: `scripts/launch_training_worker.ps1`.
- What: Added a fixed-profile launcher for the isolated training worker on ports 35001/28001.
- Why: Training-server lifecycle can be authorized narrowly without broad PowerShell launch permission.
- Next: Deploy the committed training lab and restart worker 01 with this launcher.
## [2026-08-10] Training launcher log-path correction
- Files: `scripts/launch_training_worker.ps1`.
- What: Redirected child-process output into the worker logs directory.
- Why: Redirecting into Factorio's own `factorio-current.log` prevented a second worker start.
- Next: Relaunch worker 01 and verify its RCON endpoint.
## [2026-08-10] WSL-native training worker
- Files: WSL lifecycle helpers, batch secret support, worker contract tests, training architecture.
- What: Added an unprivileged Linux headless-worker path with native runtime data and automatic local RCON secret loading.
- Why: Run isolated RL training without Windows UAC or manual password entry while preserving the existing Windows bridge.
- Next: Bootstrap the WSL worker, verify its bridge, then retire the Windows training worker.
## [2026-08-10] WSL worker RCON bind correction
- Files: `scripts/wsl/training_worker.sh`, WSL worker contract test.
- What: Used the port-bearing `--rcon-bind` form without the mutually exclusive `--rcon-port` option.
- Why: Factorio rejected the initial headless launch before opening RCON.
- Next: Relaunch the isolated WSL worker and verify bridge connectivity.
## [2026-08-10] WSL training worker live validation
- Files: WSL worker runtime under `~/factorio-training-01`; ignored local WSL worker config and bridge data.
- What: Replaced the Windows training worker with a loopback-only Factorio 2.0.77 WSL headless worker and started a bridge-compatible smoke episode.
- Why: Training now launches without Windows UAC or manual RCON password entry while keeping deterministic Nauvis isolated.
- Next: Join `127.0.0.1:35001` from the GUI for the visual smoke check; diagnose the independent zero-delivery policy outcome after the episode ends.

## [2026-08-10] WSL GUI UDP binding
- Files: `scripts/wsl/training_worker.sh`, `tests/test_wsl_training_worker.py`, `docs/33_training_architecture.md`
- What: Bound the isolated WSL game UDP socket to its private virtual interface while retaining loopback-only RCON.
- Why: A Windows Factorio GUI cannot reach a WSL NAT game server bound only to WSL loopback.
- Next: Restart the WSL training worker and join through its current WSL IP on port 35001.

## [2026-08-10] Training-lab visual observer surfaces
- Files: `factorio_training_lab/episode_world.lua`, `factorio_training_lab/episode_measurement.lua`, training-lab tests and docs.
- What: Replaced black lab tiles with deterministic grass floors and added a client-only observer view that is excluded from audits and evacuated before recycling.
- Why: GUI viewers could only see black surfaces and their player character could otherwise contaminate or pin a disposable episode.
- Next: Deploy the separate training mod, restart only the WSL training server, and reset/recreate training episodes to use visible tiles.

## [2026-08-10] Training observer safety correction
- Files: `factorio_training_lab/episode_world.lua`, `factorio_training_lab/episode_measurement.lua`, training-lab tests and docs.
- What: Changed live viewing to Factorio's no-character spectator controller and reserved an observatory surface outside the episode namespace.
- Why: A normal player character could alter an episode or collide with placement, and the original observatory name could collide with a valid scenario.
- Next: Deploy the separate training mod, restart only the WSL training server, and reset/recreate training episodes to use visible tiles.

## [2026-08-10] Training visual observer live validation
- Files: WSL training worker runtime and its backed-up disposable save; `CURRENT_STATUS.md`.
- What: Deployed the separate training lab, removed only the stale orphaned training surface/force, and verified a fresh live episode uses `grass-1` with zero audit violations.
- Why: Existing lab-dark surfaces were blank in the GUI and the baseline save retained one obsolete episode without lab ownership state.
- Next: Observe the active `training/mining-delivery-00000000` episode with `/training_view training/mining-delivery-00000000`.

## [2026-08-10] Training GUI mod synchronization
- Files: GUI client mod copy under `%APPDATA%\Factorio\mods\factorio_training_lab`; `CURRENT_STATUS.md`.
- What: Synchronized and hash-verified the GUI training-lab scripts against the deployed WSL training worker.
- Why: Factorio multiplayer rejected the connection because the client retained an older lab revision.
- Next: Restart only the Factorio GUI client and rejoin the WSL worker.

## [2026-08-10] Training-lab chequerboard floor correction
- Files: `factorio_training_lab/episode_world.lua`, training-lab tests and docs.
- What: Replaced the grass workaround with Factorio's alternating `lab-dark-1`/`lab-dark-2` chequerboard floor.
- Why: `generate_with_lab_tiles` alone emits a single dark tile, not the normal visible laboratory template.
- Next: Deploy the separate training mod, restart only the WSL training server, sync the GUI training mod, and reprovision a visual smoke episode.

## [2026-08-10] Training-lab chequerboard live validation
- Files: deployed WSL and GUI training-lab copies; `CURRENT_STATUS.md`.
- What: Redeployed and hash-synchronized the lab revision, then verified a live episode alternates `lab-dark-1` and `lab-dark-2` tiles.
- Why: The expected laboratory floor is a two-tile chequerboard, not a single generated lab tile or grass workaround.
- Next: Restart only the Factorio GUI client, join the active WSL episode, and use `/training_view training/mining-delivery-00000000`.

## [2026-08-10] Automatic training observer visibility
- Files: `factorio_training_lab/control.lua`, `factorio_training_lab/episode_world.lua`, training-lab regression tests, `CURRENT_STATUS.md`.
- What: Charted every active training surface for connected players at provision time and when a GUI client joins.
- Why: Factorio Remote view rendered an existing checkerboard lab black when the observer force had not charted it.
- Next: The isolated WSL visual smoke episode is active; continue diagnosing its zero-delivery policy result separately.

## [2026-08-10] RL Observatory training-surface viewer
- Files: `training/observer_control.py`, `tools/training_observer.*`, `factorio_training_lab/episode_world.lua`, worker-config loader, tests and training docs.
- What: Added a loopback-only View in Factorio button that moves only the configured connected observer into spectator view of a current owned `training/*` episode.
- Why: Let human supervision inspect active training attempts without radar placement, episode contamination, or any Nauvis authority.
- Next: Reconnect the GUI observer, use the active `training-wsl-01` button, and retain the zero-delivery result for separate policy diagnosis.

## [2026-08-11] RL-first operating and documentation model
- Files: `AGENTS.md`, `README.md`, active `docs/`, archived legacy docs, documentation links, `scripts/combine_docs.py`, `FULL_DOCUMENTATION.md`.
- What: Replaced deterministic-only guidance with a concise two-runtime charter that allows learned structural planning, routes agents to ten small active docs, and preserves prior designs under `docs/archive/legacy-canonical/`.
- Why: Repeated project failures were being converted into fixed recipes while runtime drift, incomplete live evidence, and stale deterministic invariants kept overriding the intended learning system.
- Next: Design the first parameterized structural action and mutation space for mining-delivery training without granting it Nauvis authority.
## [2026-08-11] AGENTS learning-first refinement
- Files: `AGENTS.md`, `docs/factorio_operations.md`, `FULL_DOCUMENTATION.md`.
- What: Elevated learn-don't-accumulate-exceptions, moved learning principles earlier, made doc routing procedural, added an evidence ladder, and moved volatile runtime details to the operations runbook.
- Why: Keep enduring agent behavior prominent while isolating operational details that will change with the repository.

## [2026-08-11] Training power-source connectivity
- Files: `training/candidates/mining_delivery.py`, `factorio_training_lab/episode_measurement.lua`, training report schemas and focused tests.
- What: Anchored each generated pole route inside its generator fixture's supply area and classified disconnected source/drill networks as `power_unconnected`.
- Why: The catalog treated an energy interface like a pole wire endpoint, so every candidate timed out without electricity.
- Next: Deploy the training mod, restart worker 01 and the batch, then verify a fresh powered episode.

## [2026-08-11] Training electricity roles and four-worker isolation
- Files: `training/power.py`, training contracts/candidates, `factorio_training_lab` validation/measurement, WSL worker scripts, focused tests, and RL training docs.
- What: Defined producer roles for electric interfaces, solar panels, steam engines/turbines, and fusion generators; defined accumulators as networked storage; and made the worker manager provision four independent WSL instances on game ports 35001-35004 and RCON ports 28001-28004.
- Why: All electrical producers must be supplied by a pole network, while an accumulator must charge/discharge as a power bank rather than satisfy a generation objective alone.
- Next: Archive the invalid pre-fix evidence, deploy the training lab to the four isolated workers, then begin a fresh four-worker smoke batch.
## [2026-08-11] Four-worker bootstrap runtime reuse
- Files: WSL worker manager/script and worker regression tests.
- What: Additional WSL workers now clone the verified worker-01 runtime when the original headless archive is unavailable.
- Why: Scaling a known-good local runtime must not depend on retaining a one-time download.
- Next: Bootstrap workers 02-04, deploy the training lab, and run the fresh four-worker smoke batch.
## [2026-08-11] WSL-private shared training RCON secret
- Files: WSL worker manager/script, WSL batch wrapper, and worker regression tests.
- What: The four workers share worker-01's `chmod 0600` secret inside WSL; the Windows controller reads that private WSL path without storing or rewriting a bridge-folder password.
- Why: Scaling training must not depend on Windows ACL mutation or expose a copied credential in the bridge output tree.
- Next: Bootstrap and start the four workers, then launch a fresh four-worker smoke batch.
## [2026-08-11] Training consumer-power coverage
- Files: training power roles, mining-delivery candidate/scenario, training-lab measurement, and focused tests.
- What: Power planning and measurement now include every current electrical consumer, including the final delivery inserter, instead of treating drill power as sufficient evidence.
- Why: Four live episodes mined ore but delivered none because their sink inserters were outside the pole network while drill-only checks reported power healthy.
- Next: Redeploy the training lab to the four WSL workers and rerun the fresh four-worker smoke batch before any long experiment.
## [2026-08-11] Training rolling throughput measurement
- Files: `factorio_training_lab/episode_measurement.lua`, `episode_world.lua`, and Lua measurement tests.
- What: Replaced the 60-tick instantaneous delivery rate with a bounded 600-tick rolling rate for sustain evaluation.
- Why: Low-rate inserters deliver in small batches; a zero item 60-tick slice was false evidence of a throughput collapse despite sustained average delivery above target.
- Next: Redeploy the training lab to four WSL workers and run the clean four-worker smoke batch.
## [2026-08-11] Shared-runtime parallel training slots
- Files: `training/scheduler.py`, WSL training manager/config example, RL training docs, focused scheduler/launcher tests.
- What: Replaced the four-server default with four logical slots sharing one explicit WSL Factorio runtime. Each slot has unique telemetry and episode identity but shares worker 01's loopback endpoint and report directory; the scheduler validates that sharing is intentional and that separate runtimes retain unique ports and output paths.
- Why: The training lab already owns multiple isolated `training/*` surfaces and samples them together, so duplicating Factorio processes and saves was unnecessary overhead.
- Evidence: A fresh live smoke batch completed 4/4 on `training/mining-delivery-00000000` through `00000003` using only game `35001` / RCON `28001`; workers 02-04 were stopped.
- Next: Run the longer population batch with four concurrent slots while observing UPS, report latency, and cleanup behavior.
## [2026-08-11] One-runtime adaptive slot capacity probe
- Files: `scripts/benchmark_wsl_training_slots.ps1`, WSL worker manager, RL docs, `.gitignore`, and focused worker tests.
- What: Added a one-server capacity probe that configures logical slots in bounded stages up to 20, samples host CPU and available memory while real disposable episodes run, and isolates all probe evidence from the learner's policy/checkpoint/database.
- Why: Concurrency must be measured on the laptop's actual Factorio surfaces, RCON traffic, and report cleanup rather than inferred from four independent servers or a synthetic benchmark.
- Safety: The probe halts on low completion ratio, CPU/memory pressure, or major wall-time regression. Candidate timeouts are retained as learning evidence but are not automatically misclassified as host exhaustion.
- Next: Resume the live capacity probe from 12 slots after the clean reset, retaining the highest healthy slot count for normal batches.
## [2026-08-11] Verified twenty-slot shared training runtime
- Files: `CURRENT_STATUS.md` and isolated ignored capacity evidence.
- What: Measured one WSL Factorio runtime with 12, 16, and 20 simultaneous `training/*` surfaces. The 20-slot stage completed 19/20 attempts (95%), averaged 28.8% host CPU, retained at least 9,984 MiB available memory, and stayed within the bounded wall-time gate.
- Why: Confirmed that this laptop can run the RL training system at 20 logical concurrent slots without maintaining twenty Factorio servers.
- Next: Normal training now uses 20 slots on game port 35001/RCON 28001; watch the Observatory and promote only held-out policy evidence.

## [2026-08-12] RL adaptive shared-server parallelism
- Files: training/scheduler.py, tools/run_adaptive_training_batch.py, scripts/run_wsl_adaptive_training_batch.ps1, tests/test_training_scheduler.py, docs/rl/training.md
- What: Added staged UPS-gated concurrency for RL batches. The controller samples tick advancement, treats the conservative lower-tail equivalent of P98 UPS as the safety gate, and grows or shrinks by four slots at stage boundaries. Fixed-runner behavior is unchanged.
- Validation: focused scheduler suite passes; Python compilation and diff checks pass. Activation requires configuring enough logical slots after the current batch completes.

## [2026-08-12] RL Observatory row cleanup and forty-slot restart
- Files: `factorio_training_lab/episode_world.lua`, training-lab cleanup tests, and the committed Observatory controls.
- What: The live worker cleanup now distinguishes terminal/expired-heartbeat episode records from active leases; the Observatory exposes a row-scoped `×` immediately after View. The isolated WSL worker was redeployed and the stale reset verified zero disposable surfaces before launch.
- Evidence: `48 passed`; worker `training-wsl-01` is running on game `35001` / RCON `28001`; Observatory `127.0.0.1:8766` returns HTTP 200 with 40 configured workers; adaptive controller PID 48356 started at 40 slots with bounds 4–40 and 55 UPS P98 gate.
- Next: Observe the fresh adaptive batch; it may shrink by four when the measured lower-tail UPS falls below the safety gate.

## [2026-08-12] RL surface ownership and compact scenario identities
- Files: `factorio_training_lab/episode_world.lua`, `factorio_training_lab/scenario_validation.lua`, `training/scenarios/mining_delivery.py`, shared-batch assignment tools, schemas, and focused tests.
- What: Reconciled stale or terminal disposable surfaces before provisioning, protected fresh active leases, serialized repeated attempts of one scenario onto one shared-runtime slot, and shortened mining-delivery identities to four hexadecimal seed characters with a 65,535 seed ceiling.
- Why: Repeated attempts reused scenario-scoped surfaces concurrently and orphaned records were rejected as collisions; eight-character names also obscured the active surface list.
- Validation: 72 focused tests passed. The deployed WSL worker `training-wsl-01` is running on game `35001` / RCON `28001`; a read-only check shows 40 active `training/mining-delivery-0000` through `0027` surfaces, no old eight-digit names, and no collision errors in live telemetry.
- Next: Let the adaptive batch continue; observe stage completion and UPS before changing concurrency.

## [2026-08-12] RL training GUI mod synchronization
- Files: `%APPDATA%\Factorio\mods\factorio_training_lab`, `docs/factorio_operations.md`, `docs/rl/training.md`.
- What: Replaced the stale GUI training-lab copy with the repository revision and documented that every Lua mod change must update server and GUI copies, followed by a GUI Factorio restart.
- Evidence: Repository, GUI, and WSL worker copies contain 9 training-mod files; GUI and repository manifests match, including `control.lua` SHA-256 `5912fb55...`.
- Next: Restart the GUI Factorio session, then rejoin `172.17.71.87:35001`.

## [2026-08-12] RL adaptive slot-cap preparation
- Files: `scripts/manage_wsl_training_worker.ps1`, `tools/run_adaptive_training_batch.py`, `docs/rl/training.md`, and slot-cap tests.
- What: Raised the configurable shared-runtime slot ceiling to 80 and generated `training-workers-wsl.json` with 80 logical slots. Future adaptive runs now default to 40 initial slots, a 4-slot adjustment, and an 80-slot maximum.
- Safety: The current batch remains on its original 40-slot configuration; no worker or server restart was performed.
- Validation: 14 focused slot/scheduler tests passed; config verified from `training-wsl-01-slot-01` through `training-wsl-01-slot-80`.

## [2026-08-12] RL report retention and orphan-force cleanup
- Files: `training/factorio_bridge.py`, `factorio_training_lab/episode_world.lua`, and focused bridge/training-lab tests.
- What: Bounded the shared training-report directory to the newest 512 artifacts and added training-only cleanup for orphan forces with no owned surface, while preserving active leases and pending force merges.
- Why: 40 concurrent bridges were scanning about 35,910 old JSON reports and timing out before finding their own reports; after that was fixed, stale training forces exhausted Factorio's force budget.
- Evidence: Moved 35,398 old reports to a recoverable archive, leaving 512 hot reports; cleanup returned `ok=true`, the worker measured 3 built-in forces and 0 training surfaces before restart, and a fresh 40-slot stage is delivering reports with no new timeout or force-limit failures. Focused RL suites pass `69 passed`.
- Lifecycle: The WSL worker was redeployed/restarted and the GUI training-lab copy synchronized. Restart the GUI Factorio session before joining the training server.
- Next: Observe the adaptive controller's UPS-gated scale-up carefully; one training force is allocated per active surface, so the 80-slot ceiling must not be reached without a force-budget strategy.

## [2026-08-12] RL mining-efficiency reward contract
- Files: mining-delivery scenario/candidates, training reward/episode/evaluation/observation code, `factorio_training_lab` measurement and validation, versioned schemas, and focused tests.
- What: Added the `mining-efficiency-v1` profile. Candidates now expose collection versus delivery belt length, Manhattan lower bound, excess route, route efficiency, poles, material cost, and footprint. Factorio measures real poles, footprint, productive drills, and cumulative working/blocked/idle capacity in the existing 60-tick audit. Rewards now price time, materials, poles, excess route, land, failed placements, and unused drill capacity separately; safety remains a non-tradeable gate.
- Compatibility: Fresh checkpoints use the v2 feature registry. Existing v1 checkpoints project candidates onto their immutable known features and remain replayable. The Observatory exposes efficiency evidence and bottlenecks.
- Validation: 114 focused RL tests and the full suite (1515 passed, 1 skipped) passed; schema meta-validation, Python compilation, and diff checks passed. No live process, training data, server, save, or loaded mod was changed.
- Lifecycle: After the current batch reaches a safe boundary, deploy `factorio_training_lab`, restart the WSL training worker/controller, synchronize the GUI mod copy, and restart GUI Factorio. Start the v2 experiment with a fresh database/checkpoint instead of mixing contracts into the active v1 lineage.
- Next: Add the production-cell upgrade-versus-expand curriculum covering assembler tier/quality, modules, inserters, belts, energy, land value, and live repeatable-research modifiers.
## [2026-08-12] RL v1.2 clean restart and process-pinned schemas
- Cause: The old controller retained the v1.1 scenario generator in memory but `training/contracts.py` reread the newly committed v1.2 schema for every validation, causing all remaining attempts to fail contract validation.
- Fix: Scenario and transition validators are now constructed once at controller import, so repository schema edits cannot alter an active batch. Added regression coverage proving validation performs no runtime schema reads.
- Recovery: Stopped the failed controller/Observatory and WSL worker, archived its evidence under `data/training-archive/training-20260812-160013`, deployed the matching training mod, synchronized the 9-file GUI mod copy, restarted the isolated worker, and launched a fresh adaptive batch.
- Evidence: Focused contract suites pass `24 passed`. The new database contains scenario `1.2.0` with reward profile `mining-efficiency-v1`; Observatory `127.0.0.1:8766` reports 40 live workers, 120 queued, no terminal failures, and no schema errors. The controller starts at 40 slots and is capped at 60 until training forces can be safely reused.
- Lifecycle: The WSL worker and Python controller already run the new code. Restart GUI Factorio before joining because its mod copy was synchronized while the GUI process remained open.

## [2026-08-12] RL shared work queue for adaptive slots
- Cause: The adaptive controller permanently assigned each scenario to one logical slot for surface safety. With variable episode durations, some slots exhausted their local lists and remained idle while other slots still held queued jobs. This produced queued work beside only a handful of live workers.
- Fix: Replaced permanent slot ownership with a shared, scenario-locked queue in both batch runners. A completed slot claims the next unlocked scenario immediately; the same scenario remains serialized until its prior attempt has recycled. Episode records begin unassigned and capture the slot that actually starts them.
- Evidence: Focused scheduler, store, batch, and observer suites pass `33 passed`; Python compilation and diff checks pass.
- Operational state: Paused `policy-g0001-fc8c141a11b6` before restart. The server currently has zero training surfaces. Its database preserves 160 completed first-stage transitions plus 144 interrupted second-stage queue records, so do not resume it blindly; choose a clean new batch or add explicit resumable-stage recovery first.
- Lifecycle: Python-only change. The WSL Factorio worker and GUI mod do not need redeployment; restart the Python controller only when restarting the batch.

## [2026-08-12] RL GPU learner and controller CPU budget
- Files: `training/compute.py`, `training/surrogate.py`, `tools/train_gpu_surrogate.py`, `scripts/setup_rl_gpu_environment.ps1`, bridge/episode polling, focused tests, and RL training docs.
- What: Added an optional lazy CUDA runtime and an offline dual-head reward/failure surrogate that reads terminal transitions read-only and writes a separate checkpoint. Reduced per-slot measurement polling from 0.25 s to 1.0 s and throttle shared report-directory pruning to one scan per five seconds.
- Why: Factorio simulation itself cannot use CUDA; RCON and filesystem work are CPU-bound. The control-plane reduction protects UPS, while batched GPU learning uses the RTX only where it can amortize kernel overhead.
- Safety: The surrogate never controls a live episode or mutates the experience database. It is opt-in, requires an isolated CUDA PyTorch environment, and should run between Factorio collection stages rather than alongside a GPU-heavy LLM.
- Next: Install the isolated CUDA environment, confirm `cuda_available=true`, and train/evaluate the surrogate on a clean batch before considering any candidate-ranking integration.
## [2026-08-12] RL randomized energy-source fixtures
- Files: mining-delivery scenario/candidate generation, Python and Lua scenario validation, focused tests, and RL training docs.
- What: Mining-delivery episodes now seed the 2x2 power source at a clear, in-bounds position near the ore patch instead of fixing it at `[0, 0]`. Its pole budget includes source-to-production distance.
- Fix: Candidate collection rows now reserve underground-belt endpoints as well as ordinary belt tiles, eliminating the overlap exposed when a randomized protected fixture changes a bridge route.
- Lifecycle: Training Lua changed. Deploy the training mod to WSL, synchronize the GUI copy, restart the WSL worker and GUI Factorio session, then start a fresh Python controller with a new database/checkpoint.

## [2026-08-12] RL live UPS backoff and truthful episode age
- Cause: A 40-slot stage provisioned all episodes and then issued one observation/report per second per slot. Factorio fell to 1.67 UPS, but the adaptive controller only evaluated its safety gate after the entire 160-episode stage completed. The Observatory labelled fresh telemetry heartbeat as `Age`, obscuring that the same episodes were still running.
- Fix: Measurement now uses a five-second cadence. An unsafe live UPS window interrupts the disposable stage, recycles active episodes, records them as `aborted` without reward/transition evidence, requeues fresh immutable episode IDs, and reduces slots by four immediately. The Observatory now labels heartbeat separately from elapsed Factorio ticks.
- Evidence: Headless baseline was restored after the aborted overload; RCON and the worker listener are healthy. Focused controller, episode, store, observer, and CLI suites pass `42 passed`; compilation and diff checks pass.
- Lifecycle: Python-only. Restart the adaptive controller from a fresh archived database; no training-mod redeploy or GUI Factorio restart is required for this controller change.
## [2026-08-12] RL safe concurrency calibration start
- Live evidence: A 40-surface cold stage dropped the isolated headless worker to 1.67 UPS and made RCON/client interaction unreliable. After reset, the same worker sustained 60.7 UPS with no training surfaces.
- Decision: Adaptive collection now defaults to the four-slot floor and grows only after healthy windows. Starting at 40 is no longer treated as a safe default; it is a load test above measured capacity.
- Validation: Focused RL controller, episode, store, observer, scheduler, and CLI suites pass `43 passed`; compilation and diff checks pass.
- Lifecycle: Python-only. Start a fresh adaptive controller from four slots; no training-mod deployment or GUI restart is required.
## [2026-08-12] RL 20-slot dual-percentile policy cohorts
- Decision: Restore a 20-slot starting point, changing capacity by four slots. A stage is healthy only when lower-tail UPS meets both `P95 >= 57` and `P98 >= 55`; one unsafe window backs off immediately.
- Fix: Decoupled capacity stages from learning. A policy now collects 100 terminal episodes across however many safe stages are required before creating the next generation. Capacity-aborted probes are recycled/requeued and are excluded from both the cohort and reward evidence.
- Evidence: The interrupted four-slot run showed 28 transitions spread across seven policies, confirming the former four-result generation boundary was too small. Focused scheduler, controller, episode, store, observer, and CLI validation passes `45 passed`; compilation and diff checks pass.
- Lifecycle: Python-only. Restart the isolated adaptive controller from a fresh database; no Lua deployment or GUI Factorio restart is required.
## [2026-08-12] RL graceful five-minute capacity gates
- Changed adaptive concurrency to hold each target for a five-minute rolling UPS window, then change by four slots when both P95 >= 57 and P98 >= 55 are met or missed.
- Scale-down is now a drain: live episodes are never interrupted or requeued for capacity; the controller waits for the current stage to empty before starting at the lower target.
- The sampler persists across stages so short episodes cannot bypass the five-minute requirement.
- Validation: focused RL regression suite 45 passed; Python compilation and `git diff --check` passed.
- Lifecycle: Python controller restart required; no Lua deployment or GUI Factorio restart.

## [2026-08-12] RL Observatory UPS stability graph and relaxed gate thresholds
- Added a read-only P95/P98/worker-limit history graph sourced from bounded controller-state windows, including target lines and latest slot count.
- Lowered adaptive gates to P95 >= 50 and P98 >= 45 as requested; the five-minute drain behavior remains unchanged.
- Validation: 29 focused scheduler and observer tests passed; Python compilation and `git diff --check` passed.
- Lifecycle: restart the Python controller and observer to activate new thresholds and dashboard assets; no Lua deployment or Factorio GUI restart.

## [2026-08-12] RL five-server independent capacity gates
- Changed training collection to five isolated WSL Factorio runtimes with six active slots per server, up to 16 configured slots per server.
- Each server samples its own RCON UPS window and changes capacity by one slot every five minutes; per-server evidence is exposed in the Observatory.
- Evidence: all five servers booted on ports 35001-35005/RCON 28001-28005; first window reached 7 active slots per server with P95 56.6-57.9 and P98 54.5-57.2 UPS.
- Lifecycle: training workers and Python controller restarted; no deterministic mod or Nauvis runtime change.

## [2026-08-13] RL twenty-server capacity-filled cohorts
- Prepared the next run for twenty isolated WSL runtimes, eight initial slots per runtime, sixteen-slot per-runtime ceiling, and two-second staggered bootstrap/deploy/start operations (320 maximum logical slots).
- Adaptive policy cohorts now retain the 100-episode minimum while topping up to active capacity with fresh seeded attempts, preventing idle slots at cohort tails.
- Lifecycle: current generation-9 controller was left running; restart the RL controller only when launching this next run. No Lua deployment or Factorio GUI restart is required.

## [2026-08-13] RL 50-server staged mining scale test
- Fixed staged candidate regeneration so 10/s -> 30/s -> 60/s upgrades are executable on one persistent surface; the final stage now uses two independent 30/s sink corridors with 120 electric drills and express belt/loader transport.
- Contract: each stage must sustain its target for 600 ticks (10 seconds). The launched curriculum uses 500 unique scenarios per policy, four policy cohorts (2,000 attempts), 50 isolated WSL servers, and adaptive capacity from one to ten slots per server.
- Evidence: a hard-fail live smoke completed all three stages with 1 completed / 0 failed. All 50 RCON endpoints registered `training_execute`; PID 3372 is running from `data/training-staged-scale-20260813-50x10/` with zero controller stderr and live full-fleet measurement telemetry. Focused validation passes 78 tests plus a 500-seed / 1,500-stage candidate sweep.
- Lifecycle: the training mod was deployed and all 50 isolated WSL workers plus the Python controller were restarted. Real Nauvis and the deterministic runtime were not touched; GUI observation would require syncing the training mod copy and restarting the GUI client.

## [2026-08-18] Native Linux RL worker and Factorio 2.1.14 headless migration
- What: Restored the Windows-port evidence bundle under ignored `data/`; added a native Linux worker manager and native Observatory adapter using direct, isolated `~/.local/share/factorio-rl/training/<index>/worker` roots. Installed the supplied Factorio 2.1.14 headless archive privately at `~/.local/share/factorio-rl/runtime/factorio-2.1.14` and made it the manager default.
- Compatibility: Updated both project mods to declare Factorio 2.1 and replaced the now read-only `LuaEntity.minable` fixture assignment with `minable_flag`.
- Evidence: Focused native worker/Observatory tests pass (18). One private worker started headless build 87180 with its bundled read-data, registered the training commands, provisioned `training/mining-delivery-0002` at tick 108121, wrote parseable reports, and recycled the disposable force successfully. Real Nauvis/player was not opened or mutated.
- Lifecycle: the private training worker is running on game `35001` and loopback RCON `28001`; no deterministic deployment or restart is required.

## [2026-08-18] Deterministic Factorio 2.1 science-control implementation plan
- Files: `docs/deterministic/science_control_plan.md`, deterministic and schema documentation.
- What: Defined a staged science telemetry contract, pure diagnosis/ranking layer, disposable-only lab-circuit experiment, and later 2.1 integration backlog.
- Why: Keep deterministic research selection auditable while adding the lab status and inventory feedback needed to diagnose science throughput.
- Evidence: Documentation review only; no Lua, deterministic server, or Nauvis/player state changed.
- Next: Implement Phase 1 `ScienceStatus` v1 as a read-only mod report, schema, bridge method, and focused disposable-runtime validation.

## [2026-08-18] Deterministic ScienceStatus v1 repository implementation
- Files: `factorio_mod/science_telemetry.lua`, `factorio_mod/control.lua`, `schemas/science_status.schema.json`, `orchestrator/game_bridge.py`, `tools/science_status.py`, and focused tests.
- What: Added a strict, read-only force/surface-scoped lab and research report plus bridge collection and offline inspector.
- Evidence: Lua syntax validation and focused schema/bridge/inspector/Lua-stub tests passed. The behavioral Lua checks remain skipped when optional `lupa` is unavailable.
- Lifecycle: Lua source is not deployed. Deploy the deterministic mod and restart its Factorio runtime and Python runner before any runtime validation; no Nauvis/player state changed.

## [2026-08-18] Deterministic science diagnosis and selection advisory
- Files: `orchestrator/science_diagnosis.py`, `orchestrator/research_scheduler.py`, and ScienceStatus replay fixtures/tests.
- What: Added pure diagnosis for supply, power, progress, and capacity observations plus deterministic, finite allow-list research ranking.
- Evidence: Focused diagnosis/ranking replay tests passed; modules neither open RCON nor mutate a game.
- Lifecycle: Restart a Python consumer only when it is deliberately wired to use this advisory output. There is no new research actuator, deterministic deployment, or Nauvis/player state change.

## [2026-08-18] Native Linux deterministic server manager
- Files: `scripts/manage_linux_deterministic_server.sh`, operations documentation, and focused manager tests.
- What: Added a lifecycle manager that copies a selected source save once into an isolated deterministic root, deploys only the repository mod copy, uses a local mode-600 RCON secret, and keeps both game and RCON loopback-bound.
- Evidence: Shell syntax and focused manager/worker regression tests passed. The source save and server root have not yet been opened by this repository change.
- Lifecycle: Bootstrap the isolated root with an explicit source save, then start it. Lua is current in the manager's deployment source; starting loads it into the new server process.

## [2026-08-18] Deterministic server started on native Linux
- Runtime: isolated `~/.local/share/factorio-rl/deterministic` root with a hash-verified copy of `~/.factorio/saves/mod_playground.zip`; original save was not modified.
- Evidence: Factorio 2.1.14 build 87180 loaded the repository `factorio_cursor_rl_agent` mod, migrated only the copied 2.0.77 save, and opened game/RCON on loopback `34199/27017`. Read-only RCON returned tick `147087`; `/help science_status` registered; a `nauvis`/`player` ScienceStatus report at tick `147091` validated with zero current research and zero labs.
- Lifecycle: server PID `161496` is running. No Python runner is started. Stop it with `scripts/manage_linux_deterministic_server.sh stop` before any future mod deployment; synchronize/restart a GUI mod copy before joining.

## [2026-08-18] Native deterministic control center
- Files: dashboard runtime/server, operations documentation, and focused tests.
- What: Added a native-manager option so dashboard deploy/start/stop actions target the isolated Linux server rather than Windows PowerShell; the dashboard can read the local RCON secret from a file.
- Evidence: Focused dashboard and deterministic-manager tests passed (9). The control center is ready to start on loopback port `9137` with the isolated deterministic root.
- Lifecycle: Start or restart the Python dashboard process after this change. It does not restart Factorio or begin an autonomous runner.

## [2026-08-18] Complete Linux deterministic control-center lifecycle
- Files: native deterministic server/runner managers, dashboard runtime/server/assets, process tracking, operations documentation, and focused tests.
- What: Every dashboard lifecycle action now dispatches bounded Linux commands: runner start/stop, server restart, isolated-save reset with backup, and mod deployment to both the isolated server and `~/.factorio/mods`. The GUI `mod-list.json` is updated atomically without removing other enabled mods; deployment still leaves the GUI client for the user to restart.
- Evidence: Shell syntax, manager help, whitespace, and 29 focused dashboard/server/runner/research tests passed. Linux runner PID records now validate process identity using `/proc` start time, protecting stop/status actions from PID reuse.
- Lifecycle: Restart the Python control center with both `--server-manager` and `--runner-manager` options to activate the mappings. These changes do not deploy a mod, reset a save, or launch a runner on their own.

## [2026-08-18] Native dashboard UDP game-status correction
- Files: dashboard runtime/status test and this status log.
- What: The native dashboard now checks Factorio's game endpoint as a UDP listener while retaining a TCP reachability check for RCON. This corrects the false offline indicator for the loopback game port.
- Evidence: Deterministic Factorio is listening on UDP `127.0.0.1:34199` and TCP RCON `127.0.0.1:27017`; 30 focused dashboard/server/runner/research tests passed.
- Lifecycle: Restart the Python control center once to load the corrected status code. No Factorio restart, save reset, mod deployment, or runner launch is required.

## [2026-08-18] Native deterministic control center running
- Runtime: `tools/dashboard_server.py` is running through `uv` on loopback `127.0.0.1:9137` with the isolated deterministic server root, Linux server manager, Linux runner manager, and GUI-mod destination configured.
- Evidence: An unauthenticated read-only `GET /api/status` returned `game=true`, `rcon=true`, `runner=false`; the dashboard is listening on TCP `127.0.0.1:9137` and has no stderr output.
- Lifecycle: No Factorio or runner action was executed during this validation. Dashboard buttons are ready for explicitly selected lifecycle actions; deploy/reset/full refresh remain state-changing operations.

## [2026-08-18] Dashboard Factorio GUI address
- Files: dashboard status payload, header display, styling, and focused test.
- What: The control center now shows the configured Factorio multiplayer address, `127.0.0.1:34199`, in its header for direct entry in the Factorio GUI. The address is supplied by the live dashboard configuration rather than copied into the page.
- Evidence: Focused dashboard tests passed (9). The Python control center must be restarted once to serve the expanded status payload; no Factorio state changes are involved.

## [2026-08-18] Dashboard GUI-address display live
- Runtime: The loopback control center was restarted with the native deterministic configuration after the GUI-address change.
- Evidence: Read-only `GET /api/status` returns `game_address="127.0.0.1:34199"`, `game=true`, and `rcon=true`; the served dashboard header contains `CONNECT IN FACTORIO` with that address and has no stderr output.
- Lifecycle: Enter `127.0.0.1:34199` in Factorio's multiplayer connect dialog. No mod deployment, save reset, Factorio restart, or runner action was performed.

## [2026-08-19] Unified Linux Factorio mod synchronization
- Files: shared GUI mod-sync script, deterministic and training managers, operations runbook, and focused tests.
- What: Both `factorio_cursor_rl_agent` and `factorio_training_lab` are now synchronized and enabled on the Linux GUI profile, deterministic server, and native RL workers. The GUI mod list is updated atomically while unrelated mods remain intact.
- Safety: The deterministic and RL runtimes remain isolated by their distinct roots, saves, ports, and explicit surface/force commands; loading the training-lab mod does not by itself create or mutate any training surface.
- Evidence: Shell syntax, manager help, whitespace, and 19 focused synchronization/manager/dashboard tests passed.
- Lifecycle: Stop, redeploy, and restart each live isolated server; then restart the GUI Factorio client before connecting.

## [2026-08-19] Matching two-mod set deployed live
- Runtime: Stopped and restarted the isolated deterministic server (`34199`/`27017`) and native RL worker 01 (`35001`/`28001`) after deploying both project mods; the original normal-profile save was not modified.
- Evidence: Repository-to-server-to-GUI directory comparisons were exact for both mods. Both server logs show `factorio_cursor_rl_agent` control checksum `43958595` and `factorio_training_lab` control checksum `807751437`; all three mod lists enable both names. The deterministic dashboard reports `game=true`, `rcon=true`.
- Lifecycle: Restart the Factorio GUI client, then connect it to `127.0.0.1:34199`. No autonomous runner was started.

## [2026-08-21] Native Factorio services restarted
- Runtime: Restarted the isolated deterministic server on game/RCON `34199`/`27017`, RL worker 01 on `35001`/`28001`, and the loopback dashboard on `9137`.
- Evidence: Listener checks confirm all five endpoints; `GET /api/status` reports deterministic `game=true` and `rcon=true`. The dashboard has no stderr output and no autonomous runner is active.
- Lifecycle: Connect the GUI to `127.0.0.1:34199`; start a runner only through the control center when a deliberate mission is desired.

## [2026-08-21] Linux-only Factorio 2.1 recipe catalog fix
- Files: `factorio_mod/recipe_catalog.lua` and its focused compatibility test.
- Cause: Factorio 2.1 removed the singular runtime `LuaRecipe.category` field. The unguarded access at line 68 raised `LuaRecipe doesn't contain key category`, transitioned the deterministic server to `Failed`, and closed RCON during the research runner's catalog export.
- Fix: Export a deterministic primary category from Factorio 2.1's plural `LuaRecipe.categories`, with a prototype-category fallback. Only the Linux deterministic server's deployed `recipe_catalog.lua` was updated; GUI, Windows, and RL worker copies were left unchanged.
- Evidence: `luac` validation plus 62 focused catalog/research tests passed (1 optional skip). A live read-only `GameBridge.export_recipe_catalog(force="player")` generated and schema-validated 651 recipes/16 machines at tick `316428`; RCON remained healthy.
- Lifecycle: The Linux deterministic server is running with the fix. The failed research runner was not retried automatically; retry it deliberately from the control center when ready.

## [2026-08-21] Recover blocked refinery tails and partial mining rows
- Files: orchestrator/autonomous_builder.py, orchestrator/stage_extraction.py, focused extraction/cohesion tests.
- Cause: A refinery extension collided with infrastructure added after the original block, while a partially built adjacent mine row was rejected before coverage/power remediation could run.
- Fix: On that specific collision, preserve the owned refinery and open the already-selected clear managed site; partial rows now continue through normal service/reconciliation and are counted conservatively until the next survey.
- Evidence: Focused planner regression suite passes (57 tests). Live Nauvis/player was not retried or mutated after diagnosis.

## [2026-08-21] ScienceStatus disposable validation started
- Runtime: Native Factorio 2.1.14 worker 01 only (`35001`/`28001`), with a temporary isolated science fixture; deterministic Nauvis/player was not used.
- Evidence: `/help science_status` registered and `GameBridge.science_status` produced a schema-valid report at tick `956646` with two sorted labs, aggregate `automation-science-pack=100`, and an explicit `no_power` lab. The report path belonged to the worker's server-owned `script-output`.
- Remaining acceptance gap: the temporary training force had no eligible active research target, so the powered lab reported `no_research_in_progress` rather than `working`. Build a disposable research-enabled fixture before marking Phase 1 runtime acceptance complete.
- Lifecycle: Worker 01 was stopped after validation; deterministic server and control center remain untouched.

## [2026-08-21] Linux deterministic research queue control
- Files: `orchestrator/research_queue.py`, `tools/autonomous_run.py`, native runner/dashboard controls, queue schema, focused tests, and operations documentation.
- What: The operation console can replace or append an explicit ordered technology queue. The Linux runner persists item state, prepares each target's science packs through the existing planner, calls `/set_research`, skips completed items, and records failures without silently changing the RL runtime.
- Persistent-memory boundary: The queue is durable user intent. The mod and planner still do not maintain a single canonical manifest of every product, measured rate, capacity, target, and expansion location; live reports/catalogs/snapshots and current priority files are reconstructed operational state. Automatic production-rate increases remain intentionally unsupported until measured rate windows and capacity policy are implemented.
- Evidence: Focused queue, research, dashboard, runner, and server tests pass (36); Python compile, shell syntax, and whitespace checks pass. No Factorio or Lua lifecycle action was required for this Python/controller-only slice.
- Lifecycle: Restart the loopback dashboard on `9137` to load the new controls. Queue submission is state-changing: it restarts the Linux deterministic runner and may build science-pack production on Nauvis/player.

## [2026-08-21] Live-open research picker and repeatable prerequisite gating
- Files: `factorio_mod/research.lua`, `orchestrator/game_bridge.py`, dashboard research API/UI, live-target validation, and focused tests.
- What: The operation console now discovers enabled/open targets from the live force. Immediate repeatable levels are open; later levels become selectable only when every preceding level is already running or queued. Thus `mining-productivity-5` can be appended while `mining-productivity-4` is active, but cannot replace that active target alone.
- Safety: Queue requests are checked again against live `/research_status` before persistence. Locked, completed, unknown, and prerequisite-ineligible targets fail closed.
- Evidence: `luac` validation, compile/whitespace checks, and 42 focused tests pass. Lua changes are repository-only until the deterministic server is explicitly redeployed/restarted.
- Lifecycle: Deploy the updated `factorio_mod` to the deterministic server and restart Factorio before using the live picker; then restart the dashboard to load the Python/UI changes. No live deployment was performed in this slice.

## [2026-08-21] Keep an unresearched repeatable level selectable
- Cause: Factorio reports the unresearched current repeatable target as `technology.level=4`, `researched=false`, `state=current`; the picker interpreted that level as already complete and exposed level 5. Live evidence at tick `152019` also showed no active research, while the persisted queue contained a stale `mining-productivity-5` item.
- Fix: The picker now shows the unresearched current level, and Python queue validation requires that level to be active or queued before accepting its successor. The unrelated runner attempt ended at `16:17:20` after a transport-belt production stall; no manual recovery was performed.
- Evidence: `luac`, compile/whitespace checks, and 38 focused picker/queue tests pass. Fix commit: `efac56c`.
- Lifecycle: Redeploy the deterministic Lua mod and restart Factorio before the corrected picker is live. The dashboard has been reloaded; no queue clearing or server restart was performed.

## [2026-08-21] Restore PR validation contracts
- Files: `tools/build_processing_units.py`, `scripts/wsl/training_worker.sh`, `scripts/manage_wsl_training_worker.ps1`
- What: Restored the explicit topology-policy wording expected by the processing CLI contract and bounded indexed WSL workers to the documented `01`–`20` range.
- Evidence: Full Python suite passes (`1613 passed, 30 skipped`); focused CI regression tests pass (`8 passed`); shell syntax and whitespace checks pass.

## [2026-08-21] Align stale CI assertions with current RL worker contracts
- The latest RL parallelism commit intentionally expanded WSL worker capacity from 20 to 50; CI assertions now verify that current contract instead of narrowing the runtime back to 20.
- The electronics CLI assertion now checks the argparse choice set semantically, avoiding a Python-version-specific rendering of the same valid choices.

## [2026-08-21] Console layout: priorities under Frequent actions
- Files: tools/dashboard.css
- What: Explicit grid rows pin Construction priorities directly below Frequent actions in column 1, the console keeps column 2 rows 1-2, research lands below it, and panels hug their content instead of stretching.
- Why: The console panel's row span auto-pushed research to row 3 and left priorities stranded mid-page under a large gap.
- Evidence: Full suite passes (1674 passed, 1 skipped with lupa present); the restarted console serves the new grid at 127.0.0.1:9137.
- Lifecycle: Dashboard process restarted; no Factorio, mod, or runner action required.

## [2026-08-21] Coverage chains serve plans, and pending systems defer
- Files: orchestrator/{autonomous_builder,stage_services,stage_chemical,live_base,stage_extraction,extraction_state}.py, focused tests
- What: Four changes from one failed run. (1) Plan coverage now targets every uncovered ACTION POSITION instead of bounding-box corners -- a 350-tile pipe route had chained eleven roboports and their power poles to two corners where nothing existed. (2) Coverage infrastructure stages only AFTER the material check passes (`_submit` gained a `stage_coverage` hook), so a plan that fails affordability never leaves service infrastructure behind; preflight-only dry runs no longer mutate the world at all. (3) Roboport chains land in waves of three while the network generates under 100 MW (live-probed `get_max_energy_production`, kW), each wave charging to ~95 MJ before the next lands; big grids skip the wait. (4) A pending plate system is now any furnace ghost within 96 tiles of its mine's output -- the old horizontal-row walk never matched the modular templates' vertical furnace columns, so the runner opened a duplicate landfill system whose preflight collided with the first system's own ore bridge and ended the run; that condition now raises `PendingSystemDeferred`, which defers the mission ("PLATE SYSTEM PENDING") instead of killing it.
- Evidence: Live probes on 2.1.14 confirmed roboport buffer 100 MJ, placed-at-~50%, vertical furnace columns at the landfill refinery, and zero remaining ghosts (bots finished the abandoned system). Focused suites pass (102); full suite 1674 passed, 1 skipped.
- Lifecycle: Python/controller only -- restart the runner before the next mission; no mod redeploy or Factorio restart is required.

## [2026-08-21] Research-queue run autopsy: own-scaffold collision class closed
- Files: orchestrator/{autonomous_builder,live_base}.py, focused tests
- What: Three fixes from observing the failed 20:29 research-queue run. (1) Refinery preflight collisions are now classified by OCCUPANT IDENTITY (`occupied_tile_owners`, name + exact planned centre): entities belonging to this system's co-submitted mine bill are construction in flight, not conflicts -- the iron run deferred forever because its own mine's substation at (10,4) covered ore-route tiles via a live bounding box wider than any declared footprint. (2) A genuine foreign-infrastructure blockage on the initial-refinery path now raises `ProductionPrerequisiteDeferred` instead of letting the re-plan queue a phantom transport-belt=53 mall demand every pass -- that phantom bill starved (no iron) and tripped the livelock guard after 12 passes. (3) `_livelock_step` resets the no-progress counter whenever the pending-ghost count falls: bots visibly building is patience, not a spin (the dead run's copper system came online 6/6 working while it was being killed).
- Evidence: Full suite 1678 passed, 1 skipped; new tests cover owner-excused collisions, deferral over phantom bills, and ghost-trend reset. Failure forensics in docs/archive/handoffs/30_session_handoff_2026-08-21.md.
- Lifecycle: Python-only -- restart the runner and re-run the research queue from the console; no Lua redeploy or Factorio restart required.

## [2026-08-22] Bootstrap-circle fixes: four live runs, two issues remain
- Files: orchestrator/{autonomous_builder,stage_services,live_base}.py, tests/{test_plate_bootstrap_circle,test_logistic_coverage,test_ghost_diagnostics}.py, docs/archive/handoffs/31_session_handoff_2026-08-22.md
- What: Five fixes from four observed mining-productivity-4 runs. (1) Roboport chains now travel link-distance per hop instead of clustering at coverage entry (user-flagged waste at (45,-1)/(47,-5)/(49,-9)). (2) A cold-start belt-only shortage on the first plate system opens the temporary requester-fed smelter instead of re-queuing transport-belt demand that fed back into itself -- the circular shortage behind run 1's livelock. (3) Stale UNBACKED_DRAWS entries expire when a mining stage succeeds, so a saturated provider stops reading as "nothing is producing". (4) The logistic_coverage remedy waits up to 90s for a charging roboport instead of burning six no-op rounds in 2s. (5) Belt-only mines get a logistic ore intake (chest+inserter at the belt run's end) and build_logistic_smelter takes ore_pickup so the coverage check names a chest, never a belt tile.
- Evidence: Full suite 1638 passed/1 skipped-family (30 env skips). Run-by-run: iron system built end-to-end for the first time since the direct-belt change; intake placed live; each earlier failure class has not recurred.
- Remaining: copper-area logistic networks hold zero logistic robots (fragmented from starter networks) and the fallback rebuilds elsewhere instead of healing an existing cell; both detailed in docs/archive/handoffs/31_session_handoff_2026-08-22.md.
- Lifecycle: Server running on 34199/27017 with post-run world; runner stopped; nothing committed pending a run clearing the copper bootstrap; no Lua changed so no redeploy is required.

## [2026-08-22] Native Linux RL training resumed from Windows generation 20
- Runtime: Synchronized both repository mods to the isolated native worker and Linux GUI profile, then started Factorio 2.1.14 worker 01 on game `35001` and loopback RCON `28001`. The loaded commands, server-owned `script-output`, disposable `training/*` surfaces, and recycle reports were verified; Nauvis/player and the deterministic runtime were not used.
- Continuity: The restored Windows/WSL run ended normally after 8,000 episodes (5,865 completed, 2,059 timed out, 76 failed) at `policy-g0020-4f3c34225134`. A three-episode Linux smoke resumed that checkpoint, completed without failures, passed the paired holdout, and promoted `policy-g0021-562dc7723428`.
- Active run: Controller PID `367440` is collecting one 100-episode generation-21 cohort in `data/training-linux-20260822-g21/`, followed by the full paired 20-candidate/20-champion held-out gate. At the first five-minute window, 4 episodes were complete with zero failures and UPS was mean `60.000`, P95 `59.998`, P98 `59.998` against required `50/45` floors.
- Evidence: Focused Linux worker, Observatory control/HTTP, and adaptive scheduler tests pass (56). The repository `.venv` supplies the pinned controller dependencies; plain system Python lacks `jsonschema` and should not be used for training.
- Lifecycle: Leave worker PID `346105` and controller PID `367440` running. No deterministic restart or Nauvis action is required; restart the Linux GUI client only if joining to observe because its mod copies were synchronized.

## [2026-08-22] Bootstrap circle broken end-to-end; thirteen observed runs
- Files: orchestrator/{autonomous_builder,stage_services,live_base}.py, orchestrator/parts_mall.py, tests/{test_plate_bootstrap_circle,test_bootstrap_priorities,test_power_and_belt_economy,test_ghost_diagnostics,test_logistic_coverage}.py
- What: Nine further fixes driven by live observation of runs 2-13. (1) Real 90s charge wait in logistic_coverage remediation. (2) Beltless-smelter fallback extended to inserter shortfalls. (3) Intake v3: blocked-drill drop-chest interface plus belt-tap candidates validated against ground truth (items present or drill drop), all four directions, dead-pair liveness gate, anchor-level exclusion. (4) Bootstrap-cell reuse by requester-section signature. (5) Bot-flight-aware health grace. (6) Essential belt economy: stocked fast tiers first, bidirectional rebalance toward covering stock, mall belt cell prepped before any mine. (7) Priority inversion: a task that queues its own prerequisite defers behind it; bootstrap-cap gates queue the electric-furnace producer as concrete work from both prep and expansion paths. (8) Solar top-up beside every power bridge on lean networks; solar-panel added to starter reserves. (9) Own-infrastructure adoption: refinery surveys excuse our poles/roboports/belts; _submit adopts our own standing belts on direction_mismatch/occupied joins instead of dying.
- Evidence: Full suite 1666 passed / 30 skipped. Run 13 (08:46-09:42) went further than any predecessor: iron AND copper plate providers recorded, automation-science-pack GOAL MET, landfill prerequisites all MALL READY (inserters 13/12, belts 127/127), steel-plate conversion under construction -- then ended on one undiagnosed lingering ghost.
- Remaining blocker: single steel-plate ghost stuck with "no blockage found" for 360s -- _diagnose_blockage needs per-ghost cause naming for ghosts whose area-level checks pass.
- Lifecycle: Server running on 34199/27017 with run-13 world; runner stopped; nothing committed pending mission completion; no Lua changed.

## [2026-08-22] Session log: 24 observed runs, full campaign record
- Files: docs/archive/handoffs/32_full_session_log_2026-08-22.md
- What: Complete record of the observation campaign -- every run, failure, fix, and live-debugging technique from boot through run 24 (healthy landfill build, stopped deliberately).
- Evidence: Suite 1669 passed/30 skipped; run-24 world preserved on the running server; nothing committed.

## [2026-08-23] Deterministic fresh episodes and rectangular power districts
- Files: scripts/manage_linux_deterministic_{campaign,runner,server}.sh, factorio_mod/layout_executor.lua, tools/{autonomous_run,dashboard_runtime,dashboard_server,dashboard.html,dashboard.js}, orchestrator/{power_district,controller_budget,autonomous_builder,live_base,stage_services,mall_builder}.py, focused tests
- What: Added a verified fresh campaign lifecycle with source/isolated SHA-256 manifests and a distinct controller-resume action; replaced chained solar placement with fixed medium-pole/substation templates, Python plus atomic-executor footprint preflight, exact pending-plan reservations, durable next-unit state, one active unit, and live-storage-aware convergence; bounded passes, plans, remediations, waits, and diagnoses; corrected saturation/control-gating/empty-feed classifications.
- Evidence: Final relevant offline suite passes (111 tests), including reset-manifest, atomic-plan contract, adjacency, convergence, repair, budget, dashboard, and executor checks; shell syntax, Python compilation, Lua syntax, and whitespace checks pass. No Factorio process was started, stopped, deployed, reset, saved, or otherwise mutated.
- Lifecycle: Implementation is offline only. A runner restart/redeploy/fresh-server cycle is required only when the user explicitly authorizes a fresh campaign.

## [2026-08-23] Fresh 4,000-episode mining curriculum launched on 200 Linux slots
- Runtime: Reset and bootstrapped 20 disposable Factorio 2.1.14 training saves, each exposing 10 isolated slots (200 concurrent episodes). Source and deployed training/shared mods match, all 20 RCON preflights advanced, and no deterministic or real-base save was used.
- Curriculum: Added the requested 15 items/s fixed-demand contract and launched 4,000 training episodes in ordered phases: 1,334 at 5/s, 1,333 at 15/s, and 1,333 at 30/s. Twenty 200-episode policy cohorts start from `policy-g0022-c46cce935ddf`; each generation also runs the normal paired 20-candidate/20-champion held-out gate.
- Live evidence: The final clean database at `data/training-linux-mining-5-15-30-4000-20260823/experience.db` showed exactly 200 running episodes across 200 live worker files with zero launch failures. The loopback Observatory serves the same run on port `8766`. Focused scenario, batch CLI, and adaptive scheduler tests pass (53).
- Recovery: Two aborted launch attempts and their databases were preserved below the run directory; their stale disposable worker saves were moved to `/home/djsm/.local/share/factorio-rl/training/failed-launch-saves-20260823T1503/` before the fresh bootstrap.
- Lifecycle: User service `factorio-rl-mining-5-15-30-20260823-run.service` owns the controller. Both its wrapper and `ExecStopPost` invoke the scoped cleanup script, so training workers 01-20 close after completion or failure; the read-only Observatory remains available for final results. The concurrently updated deterministic server is separate and was observed running, while its Python runner remained fail-closed on a deployed-mod/episode-manifest hash mismatch.

## [2026-08-23] Replace impossible fixed 5/s probes with viable staged 5 -> 15 -> 30/s training
- Cause: The fixed-demand compact candidate catalog intentionally caps layouts at eight drills. The fully researched training force gives those drills 20% mining productivity, yielding exactly 4.8/s, so 400 attempts timed out below the 5/s threshold and the later 15/s and 30/s phases could never learn. Every live slot showed eight productive drills, 4.8/s measured, 5/s demanded, and zero sustain.
- Correction: Stopped the fixed-demand service and archived its evidence and disposable saves. Launched 4,000 persistent staged episodes where every episode must sustain 5/s, then upgrade the same factory to 15/s and 30/s. The existing staged action space supplies 10/11, 30/31, and 59/60-drill alternatives with express belts/loaders and a larger bounded patch.
- Live evidence: All 200 initial episodes cleared 5/s at 6.6/s, advanced through 15/s at up to 18.6/s, and reached the 30/s stage with 60 drills. At the verification snapshot, 145/200 were already at or above 30/s, peak measured rate was 36/s, and the first 10 episodes had completed the full ladder.
- Dashboard: Observatory port `8766` now points at `data/training-linux-mining-staged-5-15-30-4000-20260823/` and each Live workers row includes the loopback Factorio multiplayer address and game port (`127.0.0.1:35001` through `:35020`). Focused staged catalog and Observatory suites pass (87), plus 100 seeds x 3 demand levels compiled with a target-capable candidate.
- Lifecycle: User service `factorio-rl-mining-staged-5-15-30-20260823.service` owns the corrected controller. Its wrapper and `ExecStopPost` both stop only training workers 01-20 at completion or failure. No deterministic runtime or real-base state was changed.

## [2026-08-23] Balance staged mining drills across both belt sides
- Cause: The staged candidate generator enumerated the complete upper drill row before the lower row, so every bounded prefix looked one-sided even though an opposing row was available.
- Fix: Mining lines now grow as opposing drill pairs at each belt position; odd drill counts leave at most one unmatched drill.
- Evidence: The focused RL suite passes (49 tests), including both candidate variants at 5/15/30 items/s across 100 seeds (600 valid plans total).
- Lifecycle: Python controller only. The active 4,000-episode run remains pinned to its startup code and was left uninterrupted at 3,820 completed plus 20 running; balanced placement starts with the next controller run. No deterministic runner, mod, or Factorio restart is required.

## [2026-08-23] Obstacle-aware staged mining campaign launched with real exploration
- Change: Staged 5/15/30 mining scenarios now provision immutable wall fields that require three-turn belt detours, paired drills grow on both sides of the collection belt, training uses seeded 20% epsilon exploration, held-out evaluation stays greedy, and promotions reject identical action traces.
- Evidence: Focused offline suite passes (85 tests; Lua syntax clean). A live smoke completed all three stages with no failure, 11/31/60 productive drills, three turns, 15 detour tiles, and successful surface recycle. The first full cohort reached 200/200 running slots; stage-one exploration selected 10 drills in 19 episodes and 11 drills in 181, with every live report at 6.0 or 6.6 items/s against 5/s and no safety violations.
- Runtime: User services `factorio-rl-mining-obstacle-5-15-30-v2-20260823.service` and `factorio-rl-observatory-obstacle-v2-20260823.service` own the 4,000-episode controller and dashboard on `127.0.0.1:8766`. The controller wrapper closes only training workers 01-20 on exit. Deterministic runtime and Nauvis were untouched.
- Lifecycle: Training workers and Linux GUI mod copies were synchronized and the workers restarted. Restart the Linux GUI Factorio client before joining because Lua changed.

## [2026-08-24] Persistent deterministic resource-district foundation
- Files: orchestrator/resource_district.py, planners/transport_occupancy.py, tools/evaluate_resource_district_variants.py, deterministic extraction/transport/refinery ownership paths, focused tests, resource-district schema.
- What: Added episode-scoped persistent mine-refinery district state with reserved growth envelopes, exact owned placements, recovery reconciliation, capacity-aware lane grouping, typed surface/underground routing, whole-footprint transport planning, and an A/B/C comparison harness. Removed independent mine/refinery fallbacks and same-force infrastructure adoption; removals and service rewires now require exact persisted identities. Threaded the verified episode ID into the managed bridge.
- Evidence: Offline repository level only. Full suite passed 1862 tests with 30 environment skips; five RL HTTP tests were deselected because this sandbox denies loopback sockets. The preserved deterministic world was not mutated.
- Lifecycle: Runner remains stopped. The Lua executor changed, so deploy/reload the project mods before the next authorized deterministic run; do nothing until then.
- 2026-08-24: Fixed dashboard fresh-campaign startup. Root cause: `tree_hash` sorted files under the ambient locale while `tools/autonomous_run.py:_directory_hash` uses C byte order, so every episode manifest failed hash re-validation and the runner exited before publishing its PID ("runner did not publish a PID record"). `tree_hash` now pins `LC_ALL=C sort -z`; contract test hardened with collation-sensitive names. Console relaunched on 127.0.0.1:9137 (PID 168141). Lifecycle: Python/shell-only change, no mod redeploy needed; next Fresh Campaign will write a locale-independent manifest.
- 2026-08-24: Deterministic fix loop, 9 fresh campaigns. Fixed: gate livelock (unlock now demands stock+1; run loop never preempts ready work), east-flow mine collectors with anchor-compatible west tails, phantom through-source bridge exits, stated-flow bridge exits, intake placement (downstream end only, 16-value belt dirs, ore ground buildable, self-powered), removal-only retirement plans. Verified live: electric-furnace gate lifts, producer built, automation-science-pack GOAL MET (run 7), copper intake chest fills at the head (run 9, 1600+ ore). Committed 277c563, 82a8b86, ed27604. Next blocker: a second consumer of one mine (landfill bridging from the stone head) conflicts direction-wise with the standing haul; needs a shared-trunk/splitter design or sourcing landfill from the mall's stone-brick chain. Lifecycle: server stopped, runner stopped, dashboard on 9137; Python-only changes since last mod deploy, no redeploy needed.
- 2026-08-24: Copper bootstrap migration now follows build -> validate -> retire. A fresh isolated episode verified six working direct furnaces before the requester cell and mine-side intake were removed; a read-only RCON survey then found no bootstrap or intake entities. Focused tests passed; repository suite passed 1875 tests with 30 skips, with five unrelated HTTP tests denied loopback sockets by the sandbox. Lifecycle: Python/controller only; restart the deterministic runner for later changes, no mod redeploy.
- 2026-08-24: Deterministic mine growth now uses complete `6 -> 12 -> 24 -> 48 -> 96` checkpoints, preserves a 24-tile straight collector before routing, falls back from blocked longitudinal growth to an atomic splitter-fed parallel band, proactively advances direct iron to 12 then 24, stages coherent iron expansion ghosts while queued materials arrive, and searches bounded alternate roboport corridors. Focused integration set passed 325 tests; full suite reached 1893 passed/30 skipped plus one fixed stale mock and five sandbox-denied loopback HTTP tests. Python/controller only; restart the deterministic runner for runtime validation, no mod redeploy.
- 2026-08-25: Iron refinery growth now follows the mine in six-furnace modules (12 drills -> 12 furnaces; 24 -> 24), recovers working owned blocks despite missing retained belts while exact-checking removals, recognizes retained live block entities during collision preflight, and relocates connected roboports before an extension occupies their footprint. Focused tests passed 155; full suite passed 1897/30 skipped with five sandbox-denied loopback HTTP tests. Python/controller only; restart the deterministic runner for runtime validation, no mod redeploy.
- 2026-08-25: Closed run 00:37 capacity/fluid faults: mine growth now discards partial sub-six tails, builds matching furnace modules before drills, and relocates safely movable poles off collector rows. Oil/plastic/sulfur are sited near crude oil; pumpjack connectors rotate toward the cell; offshore pumps require straight shore and an adjacent land-side pipe, with redundant offshore substations removed. Evidence: 262 focused tests and full suite 1911 passed/30 skipped; five loopback tests passed separately outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Persistent steel no longer stops at one 0.125/s furnace. It builds or completes a six-furnace source-local belt-fed baseline only after iron reaches 12 furnaces and 12 drills; retained one-furnace saves add the missing five. Evidence: 78 focused tests and full suite 1915 passed/30 skipped; five loopback tests passed separately outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Chemical coverage now reserves the complete future oil/pipe footprint, so coverage roboports cannot occupy later pipe tiles. Remote coverage waves defer after 30s when ports cannot charge, and a retained low-power port cannot anchor another wave; oil substations are bridged immediately after submission. Evidence: 129 focused tests and full suite 1919 passed/30 skipped; five loopback tests passed separately outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Power bridges now receive and avoid the full future refinery footprint, so an emergency pole cannot occupy a planned belt/furnace tile during a 6->12 iron extension. The controller now selects the primary generated network by live capacity rather than the nearest powered island and joins it before sizing new power units; this prevents a 167 kW local EEI island from masking the supplied 10 MW grid. Evidence: focused expansion/power suite 134 passed; full suite 1922 passed/30 skipped plus five loopback tests passed outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Corrected a startup regression in the primary-grid survey: Factorio rejected a Lua `goto` crossing local scope and returned `Cannot ...`, which the Python parser attempted to treat as coordinates. The network-selection loop now uses a scope-safe eligibility predicate and malformed replies raise telemetry context. Evidence: focused power/coverage diagnostics 85 passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Corrected the remaining primary-grid Lua syntax error: the final electric-pole `for` loop lacked its closing `end`. Evidence: generated RCON Lua passes `luac -p`; focused coverage suite 56 passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Plate startup is now explicitly foundation-first: build direct six-furnace iron, copper, and stone-brick lines in that order, then permit demand-driven capacity growth. Iron's proactive 12/24 ladder is disabled until copper and stone are direct and healthy, so a failed/deferred iron expansion cannot skip either line. Evidence: 90 focused tests; full suite 1922 passed/30 skipped plus five loopback tests passed outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Corrected run 14:50 terminal behavior. Power extension now distinguishes no-op pole coverage from a submitted primary-grid bridge, incomplete furnace clusters defer rather than being adopted as capacity or killing the run, and a refinery that has never produced plates cannot trigger mine expansion or re-site a duplicate system. Direct east-flow mines persist their future growth envelope as a reservation; an owned roboport blocking the currently needed expansion is relocated before placement. Evidence: 259 focused tests passed. Commit 302996a. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Corrected run 19:12 foundation sequencing and terminal crash. Foundation readiness now merges recipe-visible and unset furnaces into an exact six-furnace managed module, and fixed foundation work cannot invoke iron growth or unfunded expansion. Stone-brick foundation prep derives its own declared rate instead of indexing the iron/copper draw map. Evidence: 244 focused deterministic tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Simplified deterministic scheduling around measured demand. Startup now establishes only six-furnace iron and copper foundations; drill count alone cannot force growth. Coherent unfunded blueprints may be submitted only when every missing construction item has a producer and its complete solid prerequisite chain reaches active raw extraction, then receive a five-minute initial construction window with recurring backlog diagnosis. Power capacity telemetry now anchors to the selected primary generated grid through electric poles rather than a nearby roboport island. Evidence: full suite 1934 passed/30 skipped inside the workspace plus 33 loopback tests passed outside the socket sandbox; focused regression suite 165 passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-25: Empty-stock startup no longer asks a downstream intermediate to recursively choose its missing plate system. Baseline order is iron gears, copper cable, then circuits, and each becomes eligible only after every direct input is working or has produced output. The explicit iron/copper foundation path remains the sole authority for opening those raw systems; the belt cell follows both foundations. Evidence: 106 focused startup, prep, mall, and autonomous-builder tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Charging roboports no longer synchronously wait or raise a production-wide deferral. The controller completes and power-connects every required coverage wave before judging the plan, so remote pipe ghosts cannot be stranded outside construction range; only their eventual bot activity waits for charge. Iron/copper bootstrap migration now uses the source-local managed mine/refinery builder, queues its direct-system shortage, validates production, and retires the requester cell only through the existing build-validate-retire lifecycle. Evidence: focused coverage/chemical and migration suites passed; full suite previously reached 1940 passed/30 skipped with five sandbox-denied loopback HTTP tests. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Replaced requester-fed iron/copper bootstrap with the recorded removable direct stack: one drill feeds one electric furnace, one inserter, and one provider chest. Startup now builds iron and copper starters before either full six-furnace foundation; the proper system's belt bill stays visible, and the starter retires only after its replacement is healthy. Removed the requester-smelter constructor, repeated mine-intake placer, bot-haul timeout, and tests that prescribed them. Evidence: live read-only RCON validated recognition and legal siting; 246 focused tests passed; full suite 1940 passed/30 skipped plus five loopback tests passed outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Persistent refinery siting now minimizes the complete ore-plus-plate belt bill and uses demand proximity as the tie-breaker, replacing the obsolete ore-distance-first contract. The existing modular layout and reserved 48-furnace expansion footprint are unchanged. Evidence: 89 focused extraction tests and 8 siting tests passed; full suite 1940 passed/30 skipped plus five loopback tests passed outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Corrected direct-starter promotion. The temporary one-drill iron/copper stack is excluded from persistent mine discovery and drill-phase capacity, so foundation planning opens an independent six-drill belt collector and six-furnace refinery instead of trying to side-feed the starter chest. The starter remains live until the replacement validates and retires it. Evidence: 140 focused extraction/startup tests; full suite 1943 passed/30 skipped plus five loopback tests passed outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Corrected oil-source connection geometry and premature health failure. Pumpjack and offshore-pump pipe endpoints now occupy the first external tile beyond their full footprints; collision checks no longer excuse source/pipe overlap, shoreline surveys emit the external land endpoint, and oil-cell diagnosis honors the computed coal delivery grace instead of failing at 20s before its own ~40s estimate. Evidence: exact live-coordinate regressions passed; full suite 1946 passed/30 skipped plus five loopback tests passed outside the socket sandbox. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Removed the cold-start belt-tier contradiction from the opening metal foundations. Initial iron/copper mine, haul, and refinery plans now remain regular-belt builds, price their complete combined bill once, and cannot promote into fast belts whose producer is intentionally gated. Evidence: 297 focused startup, extraction, refinery, and transport tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Plastic now compares existing coal transport against the nearest viable source-local patch, sites its plants between that coal source and the refinery, and preflights one continuous coal belt into the oil-cell plan with no requester. Offshore-pump direction now matches the Factorio entity (toward water) while its verified pipe remains on the opposite land side; the bad live pump was confirmed north-facing at (-97.5,15.5), and the corrected plan faces south. Evidence: 281 focused tests passed; the prior full run reached 1948 passed/30 skipped, its five socket-denied tests passed separately, and one stale source-string test was removed. Lifecycle: Python and Lua changed; redeploy the mod and restart the deterministic runner before live validation.
- 2026-08-26: Fixed repeated local-coal mine duplication. Chemical planning now recognizes when the nearest viable coal tile belongs to the already-owned mine's patch, observes the collector's real belt direction to recover its downstream endpoint, and orients new local collectors toward plastic before extending the existing continuous belt feed. Evidence: exact run-coordinate regression plus 119 focused chemical, extraction, resource-district, and transport tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Corrected two oil-cell pipe gaps. Machine-row underground endpoints are now directional routing obstacles, so the petroleum link emits the cardinal corner at (-252.5,-107.5) instead of attempting a diagonal connection through a pipe-to-ground. Offshore output returns to the real adjacent land connector at (-97.5,14.5); the coarse 2x2 collision model exempts only that exact verified attachment. Evidence: 218 focused fluid, chemical, validation, survey, and electronics tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Remote chemical construction now overlaps production and bot work. The monolithic 562-ghost oil cell is split into coal-belt, power, machine, crude, plastic-petroleum, sulfur-machine, sulfur-petroleum, and water packets under one reserved footprint; plastic and sulfur receive separate reusable petroleum routes. A blocking mall target releases as soon as its complete supply chain is producing, and construction polls spend one wait-budget token per window rather than per second. Evidence: 271 focused tests; full suite 1956 passed/30 skipped in the sandbox plus all 33 loopback tests passed with network permission. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Closed run 20:13 oil/iron causes. Oil packet submission now preflights every missing construction item's complete supply chain before placing the first packet, then connects the remote power backbone to the primary grid immediately when that packet lands. Plate foundations release as soon as their queued materials have active production instead of waiting for the full bill in stock. Direct-mine observation now scopes drills to their contiguous collector component, preventing an unrelated same-row coal belt from becoming the iron district. Evidence: 264 focused tests; full suite 1960 passed/30 skipped plus five loopback tests passed with network permission. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Closed run 20:57 battery/stone/power contract gaps. Startup now builds iron, copper, and stone-brick starters and six-furnace foundations in order; accumulator recursion delegates to an explicit water -> sulfuric-acid -> battery chemical stage; both solar templates place a 1:1 panel/accumulator ratio. Live-catalog contracts now cover battery, acid, advanced oil, and cracking, with correct pumpjack/refinery rate math and basic-only-first policy; source-capacity-aware advanced expansion remains an explicit known gap. Evidence: 313 focused tests, full suite 2011 passed/30 skipped, and all 33 loopback tests passed with network permission. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Fixed paired mall request under-sizing. Shared gear and copper-cable requesters now carry one labelled 15-craft section per consuming machine, totaling 30 for a matching pair; baseline prep migrates already-built cells from the legacy shared section without touching machines or provider limits. Evidence: 109 focused mall, Lua-section, recovery, and prep tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Fixed the stone-brick starter crash and strengthened its bootstrap. Direct smelting now supports stone -> stone-brick with two perpendicular drills feeding one electric furnace; siting requires clean stone under both drills, recognition records both identities, permanent-mine planning excludes both temporary drills, and migration retires both while retaining their grid poles. Evidence: 137 focused starter, siting, extraction, collision, and expansion tests passed; both generated Lua surveys pass `luac -p`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-26: Closed the run 23:15 iron-extension crash. The old provider/output End can legitimately be absent after an interrupted build; its exact executor removal is a no-op, so recovery now permits that absence and installs the replacement End. A different live occupant or a changed matching entity still fails ownership validation. Evidence: 120 focused refinery/extraction tests passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-27: Removed the cold-start belt-reserve deadlock from iron foundation construction. A no-output regular-belt starter may spend one recipe's belt inputs (for example, a splitter's four belts) instead of waiting for the 50-belt reserve. Once every missing construction item has a scheduled chain, the opening mine, continuous haul, and six-furnace refinery are submitted as nonblocking ghosts rather than re-queuing their full bill. The reserve still applies once belt production is working. Evidence: 112 focused startup, mall, stock, and refinery tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-27: Prevented false construction completion after empty-stock startup. A mall item now remains an active demand until its own producer has worked or produced an item; merely finding a recipe-configured but supply-starved assembler no longer removes its target. This keeps belt and splitter recovery active while their upstream plate ghosts complete. Evidence: 113 focused startup, mall, stock, and refinery tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-27: Applied explicit starter-migration mall caps. While either removable direct iron/copper stack remains, electronic circuits, splitters, and underground belts stop at five items. After the second stack retires, circuits return to normal reserve policy while splitter and underground-belt storage is capped at one stack. Evidence: 98 focused construction-stock, startup, mall, prep, and request tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-27: Enforced starter-migration mall caps at the compact-cell production target. Electronic circuits, splitters, and underground belts now request and store only five units until the direct iron/copper starters retire; the original job target can no longer override that cap. Focused tests: 99 passed.
- 2026-08-27: Splitter and underground-belt requester sections now use multiplier 2, rather than normal multiplier 8, while either direct iron/copper starter remains. Existing paired cells are refreshed, and normal multiplier 8 returns after starter retirement. Focused tests: 80 passed.
- 2026-08-27: Automation-science-pack construction now defers while either direct iron/copper starter remains, preventing a new science assembler from consuming opening metal. Existing science lines remain eligible for normal recovery. Focused tests: 90 passed.
- 2026-08-27: Tightened opening metal controls. Splitter storage now caps at two; the circuit cap is applied in the central line planner so baseline prep cannot re-expand it from requester demand; and the iron direct starter now requires two legal iron drills feeding two furnaces into one provider chest. Focused tests: 114 passed, generated starter-survey Lua passes `luac -p`.
- 2026-08-28: Aligned direct-starter recognition with the straight dual-iron layout so one healthy starter is reused instead of duplicated. Power bridges now reserve resource tiles and let each surface/force bridge settle before another target surveys the grid, so neighboring mall cells extend the new chain instead of opening a parallel one. Evidence: 31 focused starter and power tests passed plus `compileall`. Python/controller only; restart the runner for live validation, no mod redeploy.
- 2026-08-28: Roboport coverage chains now reserve resource patches in both local and alternate-corridor placement. This prevents a coverage port from landing inside a future mine and then becoming impossible to power after pole routing correctly avoids ore. Evidence: 57 roboport/logistics tests passed plus `compileall`. Python/controller only; restart the runner for live validation, no mod redeploy.
- 2026-08-28: Power bridges now choose resource-clear anchors on the selected generated grid. A powered starter pole trapped inside an ore patch can no longer become the origin of a bridge that is required to avoid that same patch. Evidence: 62 focused power and roboport tests passed plus `compileall`. Python/controller only; restart the runner for live validation, no mod redeploy.
- 2026-08-28: The Factorio operations console now copies the newest complete `RUN START` through `RUN END` block and exposes separate confirmed controls to stop the runner plus Factorio server or stop only the console. Evidence: 27 focused dashboard/log tests passed, Python compilation, JavaScript syntax, and diff checks passed. Dashboard Python/static files only; restart the operations-console server, no mod redeploy or Factorio restart required.
- 2026-08-28: Enforced the five-item starter electronic-circuit limit on the assembler's logistic-network stock gate, not only its provider chest target. New cells receive the gate during construction, and already-built baseline cells run through the central refresh path before being marked ready. Evidence: 115 focused startup, prep, mall, and stock-gating tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Fixed earmarked plate-foundation startup livelock. A pending mine/refinery now owns the startup pass and polls construction every 30 seconds instead of falling through to the automation-science gate twelve times in one minute. Once an asynchronously built foundation has live output, its direct starter is retired so the metal-transition gate can advance to copper and eventually release science. Evidence: 175 focused startup, extraction, mall, and run-loop tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Copy-last-run now identifies a stale operations-console process when the new route returns plain-text 404 and tells the operator to restart the console instead of exposing a JSON parser error. Evidence: 27 focused dashboard/log tests and JavaScript syntax validation passed. Restart the operations-console server; Factorio and the runner are unchanged.
- 2026-08-28: Mine starvation now verifies the standing drills' power before requesting more capacity. Unpowered drills are serviced through the existing mine power-remediation path and given one pass to deliver ore; only an already powered mine may trigger another drill phase. Evidence: 129 extraction tests and 111 power/coverage tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: The compact mall now reserves an electronic-circuit half for copper-cable and never pairs it with transport-belt. Transport-belt requester sections use multiplier 30 in every phase. Evidence: 110 focused mall and construction-stock tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Corrected the compact-mall pairing intent: duplicate copper-cable halves now occupy separate cells, so one pairs with electronic-circuit and the other with transport-belt; cable/cable and circuit/belt pairs are disallowed. Transport-belt requester sections remain at multiplier 30. Evidence: 110 focused mall tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Managed mine power now uses one mine-owned substation per paired six-drill module, with direct substation wires across the ore patch. Initial paired rows, longitudinal growth, parallel splitter bands, and existing-mine repair all use the same grid positions; mine plans no longer add medium-pole scaffolds or energy interfaces. Evidence: 195 focused extraction, resource-layout, power, transport, and collision tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Fixed earmarked iron-foundation service and material-delivery churn. A recurring missing-ghost diagnosis reuses one nearby stage provider chest under verified logistic coverage and moves a useful batch, instead of placing one chest per retry. Earmarked mines power their real substation immediately; earmarked refineries reserve logistic coverage, promote and connect their power anchor when stocked, and return no plate provider until the block is real. Evidence: 201 focused ghost, mining-coverage, refinery, extraction, startup, stock, and power tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Material-delivery chests for mine construction now sit outside the complete mine service/growth envelope and are cached per stage for reuse. A mine shortage can no longer place a passive provider on a westward reserved drill column, as observed at `(49.5,-12.5)` in the 14:38 run. Evidence: 107 focused ghost, mining, extraction, and prep tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Copper-foundation recovery now owns the startup pass after a mine power repair or while waiting for first refinery output, so the automation-science gate cannot be misclassified as twelve no-progress passes. Metal refinery placement also reserves each selected block's full future footprint plus a 12-tile buffer for the rest of the run, keeping later copper/iron blocks out of each other's expansion corridor. Evidence: 100 focused prep, extraction, and bootstrap tests passed plus `compileall`. Python/controller only; restart the deterministic runner for live validation, no mod redeploy.
- 2026-08-28: Added explicit `reduced-v1`/`supplied-v1` bootstrap profiles, an episode-scoped mission ledger spanning per-science-pack controllers and restarts, and schema-backed typed blocker JSONL with mandatory `bug` or `intended_difficulty` classification. The native reset manifest records the profile and clears prior mission telemetry. Evidence: 64 focused tests passed; repository suite reached 2079 passed/30 skipped in the sandbox and all five sandbox-denied loopback tests passed with network permission; Python compilation and shell syntax passed. Commit `2f90ee2`. Python/runner tooling only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-28: Added an episode-scoped Bootstrap District Lifecycle (`pioneer -> provisioning -> validating -> retiring -> released`) for iron, copper, and stone, with persistent full mine/refinery/transport/power/roboport reservations, exact pioneer/replacement ownership, restart restoration, and measured replacement output before retirement. The 19:55 iron-extension crash was a false ownership mismatch caused by a hovering logistic robot sharing the End chest coordinate; stationary infrastructure now wins exact-coordinate surveys and the mismatch emits a typed blocker if real. Evidence: 93 focused tests passed; repository suite passed 2090 tests/30 skipped apart from five sandbox-denied loopback tests, which all passed with network permission; Python compilation and diff checks passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-29: Added and audited the Helper Agent post-run observer with schema-gated case packets, bounded per-attempt evidence, deterministic fallback reviews, append-only feedback/casebook state, focused-edit briefs, and operations-console rendering. Invalid model output now falls back safely, successful short runs retain report evidence, feedback filenames cannot overwrite, and dashboard validation errors return structured conflicts. Evidence: 36 focused tests passed; repository suite reached 2098 passed/30 skipped apart from five sandbox-denied loopback tests, which all passed with loopback permission; Python compilation, JavaScript syntax, and diff checks passed. Python/runner tooling only; restart the deterministic runner and operations console for live validation, no mod redeploy or Factorio restart.
- 2026-08-29: Helper Agent packet queueing now launches an independent processor automatically after every `RUN END`; systemd-managed runners start a separate transient user service, direct runners use a detached child, filesystem locking serializes overlapping workers, and processor output is retained under its state directory. Evidence: 40 focused tests, Python compilation, diff checks, and a detached-worker integration that consumed a temporary packet and wrote JSON/Markdown reports. The three existing queued packets were processed successfully; the two requested reports are present as deterministic fallbacks because no local model endpoint is configured. Python/runner tooling only; restart the deterministic runner for automatic processing on future runs, no mod redeploy or Factorio restart.
- 2026-08-29: Added the episode-scoped material reservation ledger. Named construction projects now persist exact bills, allocations, sources, rates, ETAs, and lifecycle; compact producers reserve their full cell plus first craft before recursive prerequisites, blocking bills override idle starter caps, and prerequisites preempt parent delivery. Zero self-seed fails as typed intended supply difficulty. Also fixed the post-retirement copper-foundation resurvey `KeyError`. Evidence: 2080 non-HTTP repository tests passed/30 skipped, all five loopback-only tests passed with permission, and the final affected regression set passed 173 tests plus compilation and diff checks. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-29: Closed the two-requester-chest bootstrap cycle with a finite, restart-recoverable mall recipe loan. When a compact producer cannot fund its own requester chest, the controller borrows one duplicate copper-cable or gear assembler, recursively makes any missing solid prerequisites, gives the existing requester an exact temporary ingredient group, produces the aggregate ledger seed target, then restores the original recipe and requests. A new `configure_entity` executor action fails closed if any borrowed entity vanished and never creates replacements. Evidence: full repository suite passed 2126 tests/30 skipped with loopback permission; focused loan, reservation, requester, and executor suites passed 38 tests plus compilation and diff checks. Python and Lua changed; redeploy the mod, restart Factorio, and restart the deterministic runner before live validation.
- 2026-08-29: Connected Helper Agent reviews to the local Freetoken endpoint using `Qwen3.6-35B-A3B-NVFP4`, supplied the complete report schema in-prompt, bounded model output to 4096 tokens, and retained exact provider errors in the ledger while preserving deterministic fallback. Both requested historical reports were regenerated successfully with `status=model_review` and `model=Hermes`; their earlier fallbacks remain archived. Evidence: 41 focused tests, Python compilation, diff checks, endpoint capability probes, and two observed model-review results. Python/Helper configuration only; restart the deterministic runner for future automatic model reviews, no mod redeploy or Factorio restart.
- 2026-08-29: Hardened Helper Agent as an inference-only observer after reviewing Hermes API, cron, blueprint, and loop contracts. Added a workdir-specific `AGENTS.md`, composed the previously unused observation rubric into every request, constrained recommendations to evidence-grounded categories, and disabled Qwen thinking through Freetoken's request API so the bounded response completes reliably. Evidence: 35 focused tests, Python compilation, diff checks, and an isolated live Freetoken review with `status=model_review` and contract-compliant findings. Python/Helper prompts only; restart the deterministic runner for future reviews, no mod redeploy or Factorio restart.
- 2026-08-29: Operations-console log refresh now appends immutable text nodes instead of rebuilding the console text, preserving a user selection while new output arrives. Evidence: 19 focused dashboard tests and JavaScript syntax validation passed. Dashboard static file only; restart the operations-console server and refresh the browser, no mod redeploy or Factorio restart.
- 2026-08-29: Corrected the empty-mall requester bootstrap missed by the recipe-loan fallback. `reduced-v1` now supplies exactly two requester chests into existing connected logistics before production prep and records the one-time application in the episode material ledger, preventing later science controllers from replenishing it. The borrowed copper/gear cell remains later recovery once duplicates exist; any residual self-seed blocker is now classified as a bug. Evidence: run `episode-20260828T201350Z-7815` proved zero gear/cable machines made borrowing impossible; source/deployed mod hashes matched. Full suite passed 2134 tests/30 skipped, focused profile/supply/ledger tests passed 88, generated RCON Lua passed `luac -p`, and compilation/diff checks passed. Python/controller only relative to the already-deployed matching mod; start a fresh episode and runner for live validation.
- 2026-08-29: Starter retirement now recovers its material value instead of deleting it. Direct iron/copper/stone pioneers, legacy requester starters, and recognized legacy mine-side intake use exact authorized construction-bot deconstruction, wait for every owned entity to disappear before lifecycle release, remain restart-safe when a subset is already absent, and fail with typed `starter_deconstruction_failed` telemetry on ownership, command, or bot-progress failure. Shared starter grid poles remain in service. Evidence: source traced the old build executor to `entity.destroy()`; focused retirement/lifecycle suites passed 85 and 57 tests, the full repository suite passed 2138 tests/30 skipped with loopback permission, and compilation/diff checks passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
 - 2026-08-29: Bootstrap replacement retries now resume the lifecycle-owned refinery origin instead of resurveying after ghosts become real, and provisioning retries no longer crash on an existing mine with `mine_origin=None` or misclassify the unproven block as a mine-expansion request. East-flow mines no longer add the obsolete 24-tile collector tail because their reserved growth is westward, refinery separation is 10 tiles, Helper Agent packets retain unprefixed traceback frames, and runner tests can disable or isolate review output. Evidence: 165 focused tests passed; the full sandbox-compatible suite passed 2138 tests/30 skipped and all five loopback-only tests passed with permission; compilation and diff checks passed. Python/controller tooling only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-29: Automation science is transition-aware rather than starter-retirement-only. It may overlap final teardown only after both lifecycle-owned metal replacements retain full reservations, match their exact six-furnace foundations, and show output from their persisted machines; otherwise it resumes one named district with a structured reserve, construct, or power/transport remedy. Evidence: 93 focused tests passed; the full suite passed 2146 tests/30 skipped with five loopback-only cases rerun with permission; compilation and diff checks passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-29: Fixed the disconnected copper-mine construction grid from run `episode-20260828T224445Z-20388`. Medium-pole bridge hops now use Factorio's actual half-tile centres and preflight every edge against wire reach; submission is accepted only after the target observes generation, with one fresh retry if it does not. Removed the append-only roboport repair cache so a still-disconnected port remains repairable on later surveys. Evidence: the observed `(89.5,-68.5)` to `(98.5,-67.5)` overreach is covered exactly; 347 affected tests and the full suite of 2149 tests/30 skipped passed, including five loopback-only cases rerun with permission; compilation and diff checks passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-29: Fixed copper bootstrap retry ownership in run `episode-20260828T235633Z-30192`. A provisioning district may reuse the two persisted transport-service tiles immediately upstream of its refinery only when live belts still face the required bus direction; unreserved, absent, or wrong-facing infrastructure remains blocked. Evidence: 170 focused transport/bootstrap tests passed; the repository suite passed 2147 tests/30 skipped in the sandbox and all five loopback-only cases passed with permission. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-30: Runner evidence now records one second-precision absolute timestamp in `RUN START` and whole-second `+Ns` offsets thereafter; structured events similarly retain one `ts` anchor plus compact `seq`/`dt` fields. Helper Agent packets, the deterministic journal, dashboard run copying, and log retention read both the compact format and immutable legacy all-ISO logs. Evidence: 56 focused compatibility tests, compilation, diff checks, and an independent Luna medium review passed. Python runner and operations-console backend only; restart both processes to load the format, no mod redeploy or Factorio restart.
- 2026-08-29: Migrated the isolated deterministic server and four native RL workers from Factorio 2.1.14 to the private 2.1.17 headless runtime. Manager, campaign, dashboard, and training defaults now agree; deployed deterministic/RL configs use the 2.1.17 read-data root. Evidence: version `2.1.17 (build 87315)`, shell/Python syntax checks, deterministic server restart, and a read-only RCON tick probe at `2,321,380`. The deterministic server is running on `127.0.0.1:34199` (RCON `27017`); RL workers remain stopped until a training run is requested.
- 2026-08-30: At the dashboard's single-column breakpoint (viewport <=980px), the Live Output / Autonomous runner panel now follows Control Room / Frequent actions directly, ahead of research, priorities, and Helper Agent. Evidence: served stylesheet check and clean diff validation. Dashboard CSS only; refresh the operations-console page, no server restart required.
- 2026-08-30: Fixed repeated bootstrap haul-belt duplication. Direct-mine survey now ends the collector at the drill-row head instead of absorbing a contiguous downstream route, and the bootstrap lifecycle persists the exact haul source/actions so retries resubmit the same owned route or fail typed on identity drift. Deferred work and terminal blockers now share one canonical lifecycle signal; material shortages, construction, power repair, and first-output waits carry stable states/classifications instead of relying on message matching. Pytest defaults, CI, README, and AGENTS.md now use compact token-efficient output; the stale Linux-worker runtime assertion follows Factorio 2.1.17. Evidence: 174 focused tests passed; the 2,184-case repository run exposed only the stale assertion plus five sandbox-denied loopback tests, and all six nodes passed after correction (the five socket cases with permission); compilation and diff checks passed. Python/controller only; use a fresh deterministic episode for live validation because an already-provisioning external lifecycle record predates exact route identity. No mod redeploy or Factorio restart is required.
- 2026-08-30: Submitted bootstrap districts now persist their execution boundary and reconcile the exact owned ghosts/live entities through construction, coverage, machine-power, transport, output validation, and starter retirement instead of re-billing and zero-action resubmitting the full plan. Refinery siting evaluates all six anchors from one shared terrain/resource survey, and deferred priorities sleep to their due tick with a 30-second bound. Evidence: 204 affected tests passed plus compilation/diff checks; the broad suite reached 1,742 nodes before its intentionally slow remainder was stopped, with its sole failure confirmed as a sandbox loopback restriction and passed separately with permission. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart.
- 2026-08-30: Reduced-supply bootstrap now rations compact-mall slots through restart-safe recipe loans until permanent assembler-2, fast-inserter, provider/requester-chest, and substation producers exist; low-demand buildings are batch-produced with exact requester inputs and mixed provider storage. Chemical capability advances explicitly through pipe, one-furnace steel, chemical plant, refinery, offshore pump, pumpjack, plastic, advanced circuits, sulfur, and sulfuric acid. Refinery siting compares mirrored layouts by end-to-end belt cost; extraction surveys are cached and timed; construction diagnostics distinguish power/charging/coverage; alternating priorities can no longer conceal livelock; and runs emit compact structured decision telemetry for Helper Agent review. Evidence: 232 focused tests and the full 2,200-test repository suite passed with loopback permission; compilation and diff checks passed. Python/controller/runner tooling only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart.
- 2026-08-30: Fixed the twice-reproduced rationed-mall loan handoff failure. A new finite batch now services and restores the one active recipe loan before borrowing for another target, so a completed splitter batch cannot terminate copper expansion one drill short. Genuine no-borrower blockers include stock and active-loan state, Helper Agent packets retain loan transitions, and fallback reviews explain loan lifecycle failures. New paired-mine siting surveys each candidate once under one global 300-candidate budget, derives its largest supported reserve without restarting the scan, and emits a timed `new_mine_site` event. Evidence: 170 focused tests and the full 2,206-test repository suite passed with loopback permission; compilation and diff checks passed. Python/controller/Helper tooling only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart.
- 2026-08-30: Fixed the stone bootstrap false-readiness loop and disconnected copper service grids. Lifecycle-managed foundations now accept only their persisted real furnace positions, pioneer-only districts cannot enter retirement, and new mines/refineries verify that their exact power anchor belongs to a generating network after any repair. Failures emit typed `stage_power_connection_failed` telemetry. Evidence: 124 focused bootstrap, extraction, refinery, and power tests passed plus compilation/diff checks. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy required.
- 2026-08-30: Recipe-loan completion now persists its assembler craft baseline and required batch size, so construction may consume scarce splitters immediately without trapping the controller in a stock-accumulation loop. Active loans poll once per pass, report monotonic progress, recheck completed gates, detach stale requester tags, and rewrite exact ingredient slots; released bootstrap districts cannot recreate pioneers. Evidence: 321 focused bootstrap, mall, requester, recipe-catalog, and executor tests passed with one environment-dependent skip; Python/Lua compilation and diff checks passed. Python and Lua changed; redeploy the mod, restart Factorio, and restart the deterministic runner before live validation.
- 2026-08-30: Rotating mall loans now persist separate blocking and spare targets, finish the construction bill before optional stock, preempt spare production for competing work, keep one stack of splitters after metal transition, and preserve one permanent gear and cable assembler. Large regular-belt backlogs borrow the duplicate cable cell before permanent six-machine promotion; its requester makes missing gears first and then belts. Copy-last-run accepts compact or legacy timestamps and includes post-run Helper Agent lines. Evidence: 189 affected tests passed; the 2,227-case suite's five loopback-only cases passed with permission and its two stale power-probe mocks passed after correction; compilation and diff checks passed. Python/controller/dashboard backend only; restart the deterministic runner and operations console for live validation, no mod redeploy or Factorio restart.
- 2026-08-30: Reworked repository verification into explicit domain, cost, environment, and evidence markers. Every change now runs a curated 615-case fast gate in about 21 seconds, while all 2,270 cases remain scheduled/manual; expensive 100-seed candidate catalogs are reused within their module. Removed per-test import-path mutation and repository-writing retention fixtures, expanded module integrity to training and Helper Agent, added recipe-fixture provenance, centralized reusable bridge fakes, and relabeled fake/live and source-tripwire evidence. Evidence: the fast gate passed; the full suite's only five sandbox-denied loopback cases passed separately with localhost permission; compilation, YAML parsing, and diff checks passed. Test, schema, documentation, and CI changes only; no runtime lifecycle action is required.
- 2026-08-31: After both metal districts release their pioneers, reduced-supply bootstrap now blocks stone construction until one stack of electronic circuits and then one stack of splitters is stocked. The post-transition provider limits use live stack sizes; unrelated queued mall batches are no longer treated as prerequisites, and monotonic recipe-loan crafts count as progress even when construction consumes their output immediately. This closes the observed drill/splitter/inserter peer cycle from run `episode-20260830T174840Z-27946`. Evidence: 80 focused mall/loan tests, the 618-case fast gate, the 528-case deterministic tier, and all 2,273 repository tests passed; compilation and diff checks passed. Python/controller only; restart the deterministic runner or start a fresh episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-08-31: Fixed the first splitter loan stall in `episode-20260830T184244Z-21615`. Live inspection found the repurposed cable assembler at `(47.5,32.5)` starved with 78 cable and 26 iron in its requester because its input inserter still held two copper plates from the old recipe. Recipe switches now clear the borrowed requester group first and atomically return every feeding inserter stack to its pickup chest before changing recipe; recovery delivery targets the loan's exact requester. Evidence: Lua syntax, 98 focused tests, the 618-case fast gate, the 528-case deterministic tier, and the 2,275-case repository suite passed (five loopback-only cases rerun with permission). Python and Lua changed; redeploy the mod, restart Factorio, and restart the deterministic runner before live validation.
- 2026-08-31: Reduced-supply demand allocation may now grow from the six standing prep assemblers to a reservation-funded ten-slot bootstrap pool. Paired demand halves share one provider until all five core mall producers are live, then retrofit one independently funded provider at a time. Active rotating loans keep their mixed-output provider uncapped instead of lowering a ten-slot copper chest to a blocked one-slot target; restoration reapplies the normal cap. Evidence: 128 focused tests passed; the full 2,280-case suite had only five sandbox-denied loopback cases, all of which passed separately with socket permission; compilation and diff checks passed. Python/controller/planner only; restart the deterministic runner for live validation. The already-pending Lua deployment from the preceding milestone still requires mod redeploy and Factorio restart if not yet applied.
- 2026-08-31: Productive bootstrap waits no longer exhaust the 100-pass controller bound: required-stock growth, falling ghosts, loan craft progress, and changed outstanding work credit the current pass while the 12-pass contradiction guard remains active. Blocking one-stack circuit/splitter reserves may fund a second compact producer when backlog exceeds one minute and the exact bill fits inside the ten-slot pool. Unchanged mall maintenance is submitted once per run (and invalidated by recipe loans), while Helper Agent decision summaries now isolate the newest completed structured-event block. Evidence: Luna Medium passed 89 focused tests and the 620-case fast gate with one environment-dependent skip; diff checks passed. Python/controller/Helper tooling only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required for this milestone.
- 2026-08-31: Corrected released-district expansion accounting after episode `episode-20260830T214306Z-2797`: the bootstrap furnace cap now evaluates the cohesive total, rejected refinery deltas cannot commit future ownership, and the opening live six-furnace module remains valid while later modules construct. Mall provider/gate targets cannot shrink within a run, one selected substation power unit is fully stocked at 12 panels/12 accumulators, and unchanged new-mine site surveys are reused until construction changes occupancy. Evidence: Luna Medium passed 128 focused tests and the 622-case fast gate with one environment-dependent skip; compilation and diff checks passed. Python/controller only; start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-08-31: Plate-refinery expansion now constructs every non-conflicting addition, including the new provider, while the old output remains live; only a zero-ghost, exact-placement, ownership-verified, fully reserved cutover may retire the old End/provider. Retry recovery uses the persisted pre-cutover owner even after all new furnaces become visible. Rotating mall loans restore their borrowed assembler and advance the chemical capability ladder before admitting advanced circuits, so zero plastic can no longer leave an advanced-circuit cell permanently starved. Evidence: Luna Medium passed 86 focused tests plus Python compilation and diff checks. Python/controller/planner only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required.
- 2026-08-31: Pipe now remains a finite, recoverable rotating-mall batch while any iron, copper, or stone pioneer exists. After all three district ledgers reach `released`, the completed loan permanently converts only a stocked low-demand building slot, reusing its assembler, requester, provider, inserters, and power instead of funding another cell; gear and cable anchors remain protected. Steel demand bypasses mall reservation policy and stays on its dedicated one-furnace iron-provider conversion stage. Evidence: Luna Medium passed 42 focused tests and the 628-case fast gate with one environment-dependent skip; Python compilation and diff checks passed. Python/controller/planner only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required.
- 2026-08-31: Fixed cross-recipe refinery recovery and empty emergency-provider placement from `episode-20260831T115417Z-10764`. Recipe-less furnaces now join recovery only when they complete a six-furnace module connected to a recipe-visible machine, preventing the copper pioneer from adopting the unfinished iron block. Stage delivery now requires genuinely transferable provider/storage stock and places any temporary chest outside the complete stage area. Evidence: 141 affected plate/transport tests and the 628-case fast gate passed with one environment-dependent skip; Python compilation and diff checks passed. Python/controller only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed the steel feed failure from `episode-20260831T192930Z-12315`. The dedicated steel furnace may now drain the persistent iron provider through one powered source inserter into a continuous belt that joins the furnace input bus inline; raw mine-to-refinery feeds still require a real source belt and fail closed rather than falling back to a chest hop. Evidence: 85 focused transport/conversion tests, the 628-case fast gate, and the 549-case deterministic tier passed; Python compilation and diff checks passed. Python/controller/planner only; start a fresh deterministic runner episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed short iron-foundation failures from episodes `episode-20260831T201145Z-30951` and `episode-20260831T205248Z-17308`. A submitted bootstrap district now keeps reconciling its persisted construction, coverage, power, transport, and output before structural furnace count can advance startup; exact live-furnace observation filters directly for the force-owned machine so transient item-request proxies cannot invalidate a just-released foundation. A read-only post-run snapshot confirmed all six owned furnaces were working with roughly 310 completed crafts each after the stopped controller had left construction to converge. Evidence: 113 affected tests, the 629-case fast gate, and the 551-case deterministic tier passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed the consumed-prerequisite recipe-loan loop from `episode-20260831T211433Z-9431`. Live read-only inspection confirmed the drill loan still carried gear baseline `324` and requirement `25` after the borrowed assembler reached `946` crafts; completion had only been recognized for final products. V4 loan tags now persist each step's absolute target plus completed prerequisite credits, migrate active V1-V3 tags on their next transition, and keep an unfinished parent recipe stable across stock churn. Evidence: the exact live V3 regression, V4 restart recovery, and parent non-regression tests pass; 79 affected tests, the 631-case fast gate (one environment-dependent skip), and the 554-case deterministic tier passed. The full 2,312-case repository run had only five sandbox-denied loopback failures, and all five passed with localhost permission; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed the chemical recipe-loan handoff recursion from `episode-20260901T100525Z-30542`. Restoring a downstream rotating loan now ends the controller pass; the missing chemical predecessor is borrowed only after the next live observation confirms restoration, preventing the stale pumpjack loan from recursively servicing itself until the 800-plan budget expires. Evidence: exact pumpjack-to-chemical-plant regression, 85 affected tests, the 631-case fast gate (one environment-dependent skip), and the 554-case deterministic tier passed; the full 2,312-case suite had only five sandbox-denied loopback failures, and all five passed with localhost permission; compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Bounded deterministic-run evidence after `episode-20260901T100525Z-30542` produced a 614 KB run block and a recursive 144 KB-scale traceback. Semantically unchanged retries now use exponential samples, heartbeats emit once per minute, zero/nonzero outcomes and positions remain distinct, and the terminal compaction summary records exact counts for the top repeated templates; unhandled tracebacks keep the final 24 frames. Helper packets deduplicate and cap excerpts at 24,000 characters, keep at most eight blockers, and deterministically fall back above a 120,000-character model prompt. The archived run rebuilds to an estimated 140 KB human log and an 18 KB packet / 26.5 KB complete model input. Evidence: 75 focused tests, the 631-case fast gate (one environment-dependent skip), and the 554-case deterministic tier passed; the full 2,318-case suite had only five sandbox-denied loopback failures, and all five passed with localhost permission; compilation and diff checks passed. Python runner/Helper Agent only; restart the deterministic runner for future runs to use compaction, no mod redeploy or Factorio restart required.
- 2026-09-01: Removed heartbeat events from append-only deterministic evidence. Runner liveness was not consumed from the log: the dashboard already validates the PID plus OS process-creation token, and systemd owns service state. One atomically overwritten `autonomous-run.heartbeat.json` sidecar now preserves the last-seen PID/time for abnormal-death diagnosis and is deleted on clean exit, so healthy waits add zero log or Helper tokens. Evidence: 56 focused runner/log/dashboard tests, the 631-case fast gate (one environment-dependent skip), and the 554-case deterministic tier passed; compilation and diff checks passed. Python runner only; restart the deterministic runner to activate, no mod redeploy or Factorio restart required.
- 2026-09-01: Helper Agent now treats a missing local FreeToken endpoint as an incomplete LLM review, not a finished fallback. Its detached worker health-checks port 1919, makes one cooldown-guarded `ft serve` restart attempt with a one-request Qwen3.6 16K helper profile, waits up to 180 seconds, and retries the packet; a continuing outage leaves the packet in `inbox/` for later recovery. The profile uses offloaded automatic MoE caching and FreeToken-managed Mamba sizing with an 8K KV reserve; malformed reachable-model output remains a clearly labeled fallback. Evidence: 27 focused Helper-Agent tests, Python compilation, and the 631-case fast gate passed. Python/Helper configuration only; no Factorio restart or mod deployment is required because the next detached review worker imports the updated code.
- 2026-09-01: Aligned Helper Agent's request with its 16K local FreeToken profile: model output is explicitly capped at 1,536 tokens and the complete packet prompt at 36,000 characters, leaving room for the system rubric and report schema instead of overriding the server's smaller response budget. Evidence: focused Helper-Agent tests and compilation passed. Python/Helper configuration only; no Factorio restart or mod deployment is required.
- 2026-09-01: Fixed the pre-logistics mall over-allocation seen in `episode-20260901T114103Z-24245`: the ten-assembler bootstrap ceiling now applies to all new compact cells, not only demand-owned cells. Circuit work beyond two cells promotes to a six-assembler direct block with a recipe-derived nine-assembler cable companion; cable uses a full continuous input belt, iron sideloads it, and replaced mall halves retire only after the line is healthy while preserving any shared provider still serving its sibling. Queued requester work now contributes to promotion evidence. Evidence: 117 focused promotion, mall, belt, and layout tests plus the 631-case fast gate, compilation, and diff checks passed. Python/controller/planner only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed the ten-slot bootstrap-mall deadlock in `episode-20260901T125706Z-29363`. The ordinary 12-inserter stone-foundation burst is now a finite rotating batch before logistics, so at the hard ten-assembler ceiling it borrows and restores a completed demand cell instead of trying to allocate an eleventh permanent compact producer. Evidence: 64 focused reservation/baseline/recovery tests and the 632-case fast gate passed with one environment-dependent skip; diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy required.
- 2026-09-01: Fixed pre-core mall slot ownership: all non-anchor construction outputs now use demand-plus-margin rotating batches until the five core mall producers are working, while one gear and one copper-cable anchor remain protected and may gain a bill-funded temporary half when backlog exceeds a minute. Pipe cannot become permanent until both plate pioneers are released and the core mall is self-sufficient; fast-tier loans retain their capability gate. Evidence: 127 focused reservation, construction, recovery, baseline, and extraction tests passed; Python compilation and diff checks passed. Python/controller/docs only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed paired metal-expansion ordering after the last run placed six additional iron furnaces without their six-drill mine batch. The combined mine/refinery bill is still preflighted atomically, but the mine growth is now submitted before refinery-growth construction waits; pending mine ghosts defer duplicate expansion submissions until bots finish. Evidence: 231 affected expansion/extraction/mall tests, Python compilation, and diff checks passed. Python/controller/docs only; restart the deterministic runner for live validation, no mod redeploy or Factorio restart required.
- 2026-09-01: Fixed the follow-on core-mall deadlock from `episode-20260901T160841Z-20480`. When all ten pre-logistics assembler slots were committed, a stocked assembling-machine-2 seed could no longer fall through to the cap and retry forever: core promotion now forces the rotating-batch path, borrows a non-anchor temporary cell, and converts that same cell into the permanent core producer after one real craft (including clearing legacy request groups). Evidence: focused core-loan/cap regressions plus the deterministic fast gate passed; Python compilation and diff checks passed. Python/controller/planner/docs only; restart the deterministic runner for live validation, no mod redeploy required.
- 2026-09-01: Fixed the submitted-foundation shortage from `episode-20260901T172828Z-7454`. Copper replacement reconciliation now queues an exact missing construction item, returns the pass to mall production, and retries the same persisted mine/refinery identity after stock recovers instead of aborting on `MaterialShortage`. Evidence: 176 focused prep/reconciliation/mall tests, the 638-case fast gate with one environment-dependent skip, and the 539-case deterministic subset passed; Python compilation and diff checks passed. Python/controller/docs only; restart the deterministic runner for live validation, no mod redeploy required.
- 2026-09-02: Fixed core logistic-chest admission from `episode-20260901T182016Z-6822`. Passive-provider and requester chest promotion now waits for a working steel-chest producer and an advanced-circuit producer; advanced-circuit setup continues through the oil/plastic chemical ladder before any chest requester is configured. The ten-slot reclaim path now catches expected recipe-loan deferrals and re-observes instead of terminating the controller with `ProductionPrerequisiteDeferred`. Evidence: 54 focused reservation/loan tests, the 641-case deterministic fast gate, Python compilation, and diff checks passed. Python/controller/docs only; restart the deterministic runner for live validation, no mod redeploy required.
- 2026-09-02: Corrected the follow-on steel-chest topology regression in `episode-20260901T202951Z-9021`. Steel chests are now temporary mall/loan batches before core-mall readiness, never a two-assembler rate-sized conversion line; an exact stocked batch may seed one core chest cell after its borrowed assembler is restored. Existing-line recovery now validates requester feeders for both logistic and belt steady-state transport, matching the real executor contract. Evidence: 93 focused mall/bootstrap/recovery tests and the 643-case fast gate passed, plus Python compilation and diff checks. Python/controller/docs only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-02: Fixed the follow-on core-mall deadlock in `episode-20260901T215728Z-19697`. Logistic-chest admission now establishes the dedicated steel-plate capability before borrowing a temporary steel-chest cell, so the chest batch cannot wait on zero steel while advanced-circuit chemical setup is blocked. Independently allocated core-mall recipes are excluded from bootstrap-loan donors, preserving permanent assembling-machine-2 and fast-inserter production. Evidence: 58 focused reservation/priority tests, the 644-case fast gate (one expected skip), Python compilation, and diff checks passed. Python/controller/docs only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-02: Rotating mall loans now recursively switch to missing solid assembler dependencies, but may wait on furnace/fluid/extraction inputs only when those inputs have a live producer. An unproduced external prerequisite is established before a new loan; an active loan is restored before the handoff so its requester cannot hoard unrelated ingredients. Evidence: 61 focused tests and the 646-case fast gate (one expected skip) passed; Python compilation and diff checks passed. Python/controller/docs only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-02: Replaced the legacy overlapping upgrade ghost and assembler destroy/rebuild plan with Factorio's native bot-driven `order_upgrade` path. Upgrade actions are exact-position, source-prototype, recipe, surface, and force scoped; incompatible recipe downgrades, unrelated occupants, pending deconstruction, and conflicting upgrade orders fail closed. The primitive preserves configured machines and returns old tiers through construction logistics, but autonomous AM1-to-AM2 promotion remains a separate caller-policy milestone. Evidence: all 26 focused schema/planner/Lua tests and the 646-case fast gate passed (one expected skip); Lua syntax passed. Python and Lua changed; redeploy the mod, restart Factorio, then restart the deterministic runner before live use.
- 2026-09-02: Reduced-supply bootstrap now treats assembler-1 and regular inserters as the only gifted mall tiers. Direct smelter starters and compact cells no longer demand fast inserters; live recipe planning selects assembler-1 until the base proves its own assembler-2 production; rotating loans preserve the borrowed machine's actual tier; mixed-tier rows remain observable during bot replacement. After core production starts, the mall stocks exact AM2/fast-inserter replacements plus a four-item reserve and submits bounded native in-place upgrades while preserving recipes and wiring. Evidence: 103 focused tests and the 646-case fast gate passed (one expected skip); compilation and diff checks passed. Python/controller/planner only on top of the already-pending native-upgrade Lua deployment; redeploy the mod, restart Factorio, then start a fresh deterministic episode from the new source save.
- 2026-09-02: Fixed the first live AM1-bootstrap follow-on in `episode-20260902T140537Z-31786`. When a standing intermediate cell starts a rotating recipe loan, its expected supply-wait signal is now consumed as successful work for the pass and re-observed instead of escaping as an unhandled `ProductionPrerequisiteDeferred`. Evidence: the exact existing-cell loan regression plus 94 focused tests and the 646-case fast gate passed (one expected skip); Python compilation and diff checks passed. Python/controller only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy required beyond the preceding native-upgrade deployment.
- 2026-09-02: Fixed the regular-inserter starvation in `episode-20260902T141147Z-11463`. Live RCON confirmed all 12 remaining inserters were trapped in the fast-inserter requester at `(50.5,38.5)` while the refinery's final ten inserter ghosts had no transferable supply. Finite pre-core requester buffers are now capped to their actual batch crafts, and requester/buffer WIP is excluded from construction affordability, compact-cell reservation, and new-cell stocked-input sourcing. Evidence: 169 focused tests, the 647-case fast gate (one expected skip), and the 583-case deterministic domain gate passed; Python compilation and diff checks passed. Python/controller only; start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-02: Fixed the AM1 bootstrap stall from `episode-20260902T144108Z-19109`. Regular `inserter` is now in `PERSISTENT_INTERMEDIATES` so unbacked cell draws schedule a producer before starter stock depletes; `electronic-circuit` is in `_MALL_RECIPE_ANCHORS` so its stock gate cannot collapse to 3 and disable the assembler; `_serve_mall_task`, `_survey_pass`, and `_bootstrap_demand_cell_affordable` observe transferable stock so requester WIP does not mask shortages or starve prerequisites; and `intermediate_scaling.py` plus `_bootstrap_reserve_machine_target` evaluate live AM1 speed (0.5 crafts/s) and scale anchor/circuit capacity aggressively within the 10-assembler bootstrap ceiling. Evidence: 134 focused unit and recovery tests passed; the 647-case fast test gate passed (1 expected skip); Python compilation and diff checks passed. Python/controller only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-02: Fixed the single-sided loan parsing stall in `episode-20260902T174840Z-18961`. `active_bootstrap_loans` required both cell sides to contain machine strings before splitting machine tier and recipe; when the opposite side was unbuilt (`-`), `left_recipe` was corrupted to `assembling-machine-1,electronic-circuit`, preventing `loan.current_recipe == step.recipe` from ever matching. This trapped the controller into re-submitting configuration every pass without waiting or advancing loan revisions, causing the livelock detector to trip at 12 passes while the loan was actively crafting circuits. `_parse_side_state` now parses each side independently; loan submissions credit `_BOOTSTRAP_LOAN_PROGRESS_REVISION`; and active loan targets and step recipes are tracked under `relevant_mission_items` for stock progress. Evidence: 104 focused tests and the 647-case fast gate passed; live RCON confirmed exact loan recovery. Python/controller only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-02: Scaled mall requester buffers to 15s (`MALL_SUPPLY_SECONDS = 15.0`) to provide at least 15x craft capacity for AM1 (23x AM2, 38x AM3) and absorb worker bot flight latency from distant smelters; established 1 permanent copper-cable baseline machine while dynamically maintaining at least 2 machines when active consumers (e.g. electronic-circuit) are being produced; protected baseline machine quotas from rotating loan conversion; expanded rotating splitter batch targets to 15 once iron supply is established; and recognized active bootstrap loans in supply chain scheduling to pipeline construction ghosts earlier. Evidence: 83 focused mall/construction/baseline tests, 19 request multiplier tests, 36 extraction prep tests, and the 647-case fast test gate passed; live campaign run (PID 1659792) confirmed 15x requester multipliers, 1 permanent cable machine, and proper baseline loan restoration. Python/controller only; restart the deterministic runner or start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-03: Fixed the transferable-stock stall that ended the fresh episode at the copper refinery: ghost remedies waited 20 rounds on 1 belt with 12 in force stock but 0 transferable (committed requester WIP bots never release), while force cover suppressed the shortage that would restart production. `_apply_remedy` now escalates to `MaterialShortage` (mall demand) after 3 consecutive transferable waits, with delivery resetting the count. Evidence: 2 new remedy tests, 30 ghost-diagnostics tests, 127 deterministic domain tests, and the 661-case fast gate passed. Python/controller only; runner restart (world preserved) picks it up, no mod redeploy required.
- 2026-09-03: Opened concurrent rotating mall loans: a new batch borrows a free cell (`MALL BOOTSTRAP LOAN PARALLEL`) instead of queueing behind the active loan, so X and Y (or X and its precursors) build at once; each cell hosts at most one loan and the serial handoff remains only when no cell is free. Loan service, rationed-batch routing, and belt-borrower checks now match by target across several loans. Evidence: 4 new parallel-loan tests and the 661-case fast gate passed (1 expected skip). Python/controller only; takes effect on the next runner start, no mod redeploy required.
- 2026-09-03: Parallelized the deterministic bootstrap: compact mall cells now start building once half their bill is stocked and every missing bill item has a scheduled supply chain (`MATERIAL PROJECT PIPELINE READY`), instead of waiting for the complete bill behind the slowest ingredient; and each ready pass serves up to 3 mall tasks while they keep completing, stopping at the first explicit defer, instead of one task per pass. Evidence: 9 new pipeline/multi-serve tests, 44 construction-stock tests, and the 657-case fast gate passed (1 expected skip). Python/controller only; takes effect on the next runner start, no mod redeploy required.
- 2026-09-03: Fixed the rationed mall completion stall from `episode-20260902T194823Z-23112`. When a pre-core demand batch reached its target, `_ensure_mall_item` reported `ready=True, output=None` (`RATIONED MALL READY: batch has reached target; no permanent cell consumed`), but `_serve_mall_task` omitted handling for `output is None` and bypassed task completion. This left satisfied batches (e.g. `transport-belt` at 89% transferable stock) un-popped in the priority queue across 12 consecutive passes until tripping the livelock detector. `_serve_mall_task` now completes the priority and pops `mall_targets` when `ready is True` and `output is None`. Evidence: 35 construction stock tests and the 648-case fast test gate passed; Python compilation and diff checks passed. Python/controller only; start a fresh episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-03: Rationed mall now builds spares on top: every pre-core batch targets its bill plus at least 20% more (rounded up, covering requester/buffer WIP), and readiness is measured in transferable provider/storage stock rather than force-wide stock. A lagging demand names its transferable shortfall, locked WIP, and pending ghosts while the mall tops up. Evidence: 4 new spare/readiness/lagging tests, 48 construction-stock tests, and the 665-case fast gate passed; compilation and diff checks passed. Python/controller only; restart the runner (world preserved) to pick it up, no mod redeploy required.
- 2026-09-03: Ghost diagnosis names missing items before coverage: `_diagnose_blockage` now scans `missing_material` ghosts first regardless of probe order, and a ghost inside a charging network whose placing item has zero transferable and zero force-wide stock reports `materials:<item>:<pending>` instead of `coverage_charge_wait`. Live 2026-09-03 copper run built 2/6 drills while 4 drill ghosts looped on charge waits with zero drills in stock; the material verdict re-queues mall demand through the existing shortage path. Stocked ghosts keep the coverage wait. Evidence: 3 new ordering/stocked/unstocked tests, 33 ghost-diagnostics tests, 206 focused tests, 548-case deterministic tier (3 pre-broken files excluded, failing identically on the clean tree), and the 665-case fast gate passed; compilation and diff checks passed. Python/controller only; restart the runner (world preserved) to pick it up, no mod redeploy required.
- 2026-09-03: Loan completion is spendable-aware and belts earn parallel capacity: `_submit_bootstrap_loan` measures its finished-goods target in transferable stock, so a drained batch keeps producing spares on its cell instead of restoring and re-borrowing every pass (belt churn: 150 available hit the 147 spare ceiling while transferable sat at 138 until the 12-pass guard tripped). The craft-proof restore now requires the blocking bill spendable; preemption still time-slices contended cells. Separately, reserved-but-flowing inputs fund new mall cells from surplus flow, and a lone belt cell with a large backlog claims a second pool producer. Evidence: 5 new loan/flow/capacity tests, 670-case fast gate, 553-case deterministic tier (3 pre-broken files excluded), compilation and diff checks passed. Python/controller only; restart the runner (world preserved), no mod redeploy required.
- 2026-09-03: Loan gate follows the loan step (live run-killer): the 18:10 run died at +653s on `mall_loan_gate_mismatch` when a drill loan's machine gate froze at the bill of 6 while its step advanced to the spare ceiling of 8. `_submit_bootstrap_loan` now resubmits whenever the persisted step target drifts from the computed step, and the STUCK branch re-reads machine status once before dying so a stale disabled sampling during bot drain becomes one more wait round. Evidence: 2 new gate/race tests, 672-case fast gate, 555-case deterministic tier (3 pre-broken files excluded, failing identically clean), compilation and diff checks passed. Live observation journal: docs/archive/handoffs/33_live_observation_2026-09-03.md. Python/controller only; restart the runner on the preserved world (copper resumes at 6/8 drills), no mod redeploy required.



- 2026-09-03: Drain-aware demand pop: a bill-met rationed demand retires once drain is proven (its loan advanced this pass yet transferable stock did not grow since the last survey, with an active loan held), instead of pinning the cell until the 12-pass guard fires on a produce-eat equilibrium. Climbing stock, idle loans, and missing history keep the full spare gate; the cell finishes spares in the background and preempt frees it on contention. Evidence: 4 new drain tests, 676-case fast gate, 559-case deterministic tier (3 pre-broken files excluded, failing identically clean); compilation and diff checks passed. Python/controller only; restart the runner on the preserved world, no mod redeploy required.
- 2026-09-03: Standing reserves grow past demand once metal flows: the pre-core need-plus-margin squeeze now applies only while starter metal carries the base; after the direct iron/copper replacements release, items keep their grown mall reserve and build ahead, and finite requester buffers carry ceil(crafts x 1.2) headroom past the batch. Evidence: 4 new reserve/headroom tests plus updated anti-hoard expectations, 680-case fast gate, 564-case deterministic tier (3 pre-broken files excluded); compilation and diff checks passed. Python/controller only; restart the runner, no mod redeploy required.
- 2026-09-03: Loan pad survey rejects non-assembler machine names: a logistic robot hovering over a borrowed cell at survey tick was parsed as the loan's machine, so the next submit configured a logistic-robot and died on configure_target_missing (18:41 run-killer at +988s). Only assembler tiers are accepted, falling back to tier 1. Evidence: regression test plus 680-case fast gate; compilation and diff checks passed. Python-only; no runtime action beyond the next runner restart.
- 2026-09-03: New-mine rows scale with ore per product: the legacy three drills per row feeds iron/copper exactly but fed only 3 of 6 stone furnaces (2 stone per brick, 1.25 ore/s appetite each). Rows now scale with the recipe's own ore-per-product ratio (iron/copper unchanged at 3, stone-brick opens 6 for a 12-drill mine, clamped to [3,12] with patch-fit fallback); the furnace side was already rate-aware. Evidence: 2 new row-sizing tests, 684-case fast gate, 570-case deterministic tier (3 pre-broken files excluded, re-verified identical clean); compilation and diff checks passed. Python/controller only; the live stone mine grows via normal expansion, no mod redeploy required.
- 2026-09-04: Trimmed the post-metal splitter reserve from a full stack (50) to 12: the 22:33 run held the single rotating assembler on splitters from +1538s to +1950s (~412s with its 200-belt prerequisite ladder) before stone, while measured refinery demand is 3 per plate project (iron PREP DEMAND; +2 for ghosts). That run reached the steel starter at +3573s and ended at +5177s STUCK on the crude pipeline vs its own power pole (blocker-0001). Evidence: updated post-metal reserve test, 110 construction-stock tests, 740-case fast gate; compilation and diff checks passed. Python/controller only; takes effect on the next fresh episode, no restart issued per user request.
- 2026-09-05: Reworked oil-district piping/power from user screenshots (commit `50c6fad`; takes effect on a fresh episode): keepout now reserves the whole built refinery row instead of a 1-machine phantom (the gas-into-crude merge), extra pumpjacks tap the nearest trunk/header tile instead of running parallel full-length pipelines, and the shared pumpjack power anchor tries all four jack corners (substation+EEI clear of jacks and stub pipes) instead of dropping a colliding substation and leaving jacks dark. Evidence: 4 new tests (header reservation, tap targets, tap picker, surviving substation), 43 stage-chemical tests, 740-case fast gate; compilation and diff checks passed. Not yet live: the 00:33 fresh session runs pre-fix code; coal-mine power and the pipeline-vs-dynamic-pole run-killer stay watch items for live verification.
- 2026-09-05: Fixed the deterministic oil run-killer that ended two runs on the same tile (00:33 run +3971s, 22:33 run +5177s): the crude pipeline's pipe ghost at (-297.5,-57.5) collided with a mid-pass power pole. extend_power already routed around reserved_tiles but the oil callers never passed them; _connect_oil_cell_power now forwards the link corridor at both oil sites and the roboport power hookup inherits its reservation. Evidence: live-game probe of the killer pole, 3 new tests, 126 targeted + 648 fast green on the staged tree (commit 8fae36e). Python/controller only; needs a fresh episode for live proof.
- 2026-09-05: Taught fluid routes to dive under blocking poles with pipe-to-ground instead of dying: the oil router now tries surface, then a landfill-free dive span (pass 2), then water crossings. Decoded the user's interleaving blueprint to confirm the mechanic (outward-facing pairs, max span 10 / nine between heads, closed sides); endpoints avoid blocked and same-fluid tiles, foreign fluids stay impassable, orphaned tunnel endpoints fail loudly. Evidence: 5 new tests (4 router + 1 oil-layer), 652-case fast gate and 529-case deterministic tier green on the staged tree (commit 12816ef). Python/controller only; needs a fresh episode for live proof against the (-297.5,-57.5) killer.
- 2026-09-05: Fixed the plan-budget bleed that killed the 03:29 chemical run at +4338s: saturated-mall rotation submits 2-4 configure-only plans per productive pass while crafts advance, and the old 1-plan refund bled out with no construction fault. Productive passes now refund to their pass-start plan mark (banked unproductive spend is kept, so pure spins still trip the bound). Evidence: 2 new accounting tests, 744-case fast gate, 654-case deterministic tier green (commit d9c8b14). Python/controller only; needs a fresh episode for live validation.
- 2026-09-05: Diagnosed the 06:13 run's +977s no_progress death (splitter starved on circuits) with live RCON interrogation of the dead world: every yield/feeder path no-op'd because stale prerequisite credit (completed_step_targets) shielded zero-progress loans. Fixed by making current-step progress the only shield, plus a handoff fallback so loan-less waiters queue their holders' starving ingredients. Live-verified across three diagnostic restarts: yield fired pass 1, belt 48->64+, deaths moved belt-32% -> inserter-33% -> e-circuit-0% with real output banked each step. New frontier: belt cell output blockage (~64 cap, full_output + mixed provider). Evidence: 5 new tests, 749 fast + 659 deterministic green. Committed with its coupled loan/rotation base as `9e4051c`; needs fresh-episode validation.
- 2026-09-05: Reconciled the Sep 3–5 Antigravity, Muse, and OpenCode worktree into focused deterministic commits without staging ignored training/runtime data. The final repository-wide gate collected 2,519 tests with zero failures, and `luac -p` validated the changed mod files. Lifecycle: Python changes require a fresh deterministic runner/episode; requester-trash Lua (`8f6c79f`) requires mod redeploy and a Factorio restart before live use.
- 2026-09-05: Parked mall batches on unfunded oil-ladder rungs: the 15:00 run died at +4485s when bulk-inserter spun 500s of borrow/restore against advanced circuits while plastic-bar was not even sited (oil cell unbuilt). _rationed_mall_batch now raises a chemical-handoff park when an item's recipe closure contains an unstarted, unstocked rung from plastic-bar onward; rungs self-establish and early-rung items keep their active path. Evidence: 4 new tests, fast/deterministic gates green. Landed in history via parallel session commit 74c783c (which also carried the previously-uncommitted yield/queue fixes under 9e4051c); worktree verified in sync.
- 2026-09-06: Oil-patch pumpjack selection now decodes the default/rotated/mirrored connector variants, rejects 3x3 well footprints and output tiles blocked by terrain or infrastructure before packet submission, and selects a compact trunk plus nearest joining branches rather than blindly adding wells nearest the first source. Remote crude expansion applies the same placement preflight. Evidence: 62 resource-layout/chemical tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-06: Added a live Logistic network inventory panel to the loopback Operations Console. A read-only mod report collects each explicit `nauvis`/`player` roboport network's `get_contents()` data, preserving quality-qualified items and returning both per-network and combined counts; the dashboard refreshes the combined item table every five seconds. Evidence: 42 focused dashboard/bridge/Lua contract tests passed; JavaScript and Lua parsing plus diff checks passed. Redeploy the mod and restart Factorio, then restart the Operations Console to activate it; no runner restart is required for telemetry alone.
- 2026-09-06: Fluid layout search now generates all four rotations and their reflections while transforming machine, inserter, belt, underground-pipe, verified-network, and header geometry together. Oil-refinery and plastic-plant construction score legal row variants by obstacle-aware external distance, total pipe entities, poles, land, and expansion seam; restart recovery identifies the chosen refinery transform from live machines and pipes. Opening oil rows reserve the additional footprint of a compatible mirrored 4x2 growth block. The reusable compact expansion operator derives opposing half-rows with one shared same-fluid seam; for eight basic refineries it beats the generated long row on pipes, poles, and land without embedding a supplied blueprint. Evidence: 245 focused tests, the 759-case fast gate (one expected skip), and the 728-case deterministic gate passed; compilation and diff checks passed. Python/controller/planner only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-06: Added `tools/opencode_campaign_orchestrator.py` for unattended isolated deterministic campaigns. It starts each fresh episode through the existing campaign manager, captures read-only manager/log/logistic-inventory evidence every two minutes, creates a new Muse Spark OpenCode session per run, and uses only that session's end-of-run comparison with the prior documented run to authorize one focused code/test change. It stops on no justified change, a repeated terminal failure, or its wall-clock bound. Evidence: 10 focused orchestrator/lifecycle tests passed, Python compilation and diff checks passed. Lifecycle: launch the controller for a fresh isolated campaign; it owns runner/server lifecycle and does not require a separate manual restart.
- 2026-09-06: Hardened the unattended campaign handoff for hour-plus runs: the controller persists the active cycle and OpenCode session ID before its first checkpoint. After a controller restart it resumes that exact run/session and does not reset the isolated world or create a replacement observer. Completion clears the persisted handle before the next fresh cycle creates a new session. Evidence: 35 focused orchestrator/lifecycle/dashboard inventory tests passed; compilation and diff checks passed. Lifecycle: restart the controller process only; it resumes the current isolated run safely.
- 2026-09-06: Added the controller's explicit `--resume-active-run` recovery mode for the narrow startup race where the isolated runner is live but its parent controller has exited before recording an observer. It adopts that existing episode without a save reset, creates and persists one session for it, and then follows the normal two-minute observation and final-comparison protocol. Evidence: 7 focused orchestrator tests passed and Python compilation/diff checks passed. Lifecycle: use this mode only to attach after verifying the isolated runner is already live; it does not redeploy, reset, or restart Factorio.
- 2026-09-06: Kept the unattended campaign evidence-driven after an end-of-run `no-change` decision: instead of blindly resetting into the same unknown failure, the controller resumes that completed run's OpenCode session once to require a narrow zero-behavior telemetry patch and regression test. Only a resulting code change unlocks the next fresh cycle; a second refusal still fails closed. Evidence: 13 focused orchestrator/lifecycle tests passed with compilation and diff checks. Lifecycle: restart the controller; it will obtain the discriminator before its next isolated episode.
- 2026-09-06: Hardened the native managed runner's virtualenv provenance after an unattended fresh cycle proved that a transient user service could lose `jsonschema` despite the direct selected interpreter importing it. The runner now passes the selected venv's `VIRTUAL_ENV` and `PATH` explicitly into its systemd user service. Evidence: shell syntax plus 19 focused runner/campaign/orchestrator tests passed; live runner restart remains required to verify the systemd context. Lifecycle: restart only the deterministic runner on the existing isolated server; no deploy or Factorio restart required.
- 2026-09-06: Completed the managed-runner import provenance fix: transient execution now receives the selected interpreter's resolved `site-packages` as `PYTHONPATH`, in addition to `VIRTUAL_ENV` and `PATH`. This avoids the observed `ModuleNotFoundError: jsonschema` when service activation falls back to the base interpreter's import path. Evidence: runner shell syntax and 6 focused runner tests passed. Lifecycle: restart the deterministic runner on the already-running isolated server to prove the live service import; no mod deployment or Factorio restart required.
- 2026-09-06: Deferred mall-control telemetry now identifies the selected task, repeat/backoff, persisted material-project bill, belt-tier fallback state, and the exact chemical-goal relevant-credit inventory coverage. This is a zero-behavior diagnostic for the run-2 fast-belt/stone-expansion stall. Evidence: 147 focused controller, priority, and construction-stock tests plus compilation and diff checks passed. Python/controller only; start the next fresh deterministic episode for live evidence, with no mod redeploy or Factorio restart required.
- 2026-09-06: Oil-cell power bridges now reserve every future machine-row footprint, pipe tiles included, while the backbone packet is connected before later refinery/plastic packets submit. This prevents the observed bridge poles at `(-310.5,-37.5)` and `(-310.5,-43.5)` from severing refinery-row pipe continuity. Evidence: 95 focused chemical, infrastructure, and power-race tests passed with compilation and diff checks. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-07: Ghost-only roboport coverage now treats each newly submitted or newly powered port as construction in progress. It re-observes the port before using it for the next hop, preventing the `episode-20260906T185551Z-17575` terminal coverage verdict after the first `(28,-35)` port's power bridge was still pending. Evidence: 185 focused coverage, starter, ghost-diagnostic, and chemical tests passed with compilation and diff checks. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-07: Removed free executor-built service infrastructure from the deterministic runtime. The shared submission boundary converts every new electric pole, substation, and roboport into a material-ledger-funded construction ghost and waits for formerly synchronous infrastructure to be revived by bots; coverage now advances one powered roboport hop at a time, and pole relocation builds its ghost replacement before retiring the old pole. The old earmarked-refinery real-pole exception was removed, so medium-pole/substation demand now exposes its real mall and steel prerequisites. Evidence: 449 focused deterministic tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-07: Power bridges now score a clear direct line at any angle before orthogonal detours, sample real half-tile pole centres, and use the full nine-tile medium-pole wire reach only when every actual link is legal. Consumers on the inclusive medium-pole supply boundary now skip redundant bridge submissions. Evidence: 48 focused infrastructure and ghost-diagnostic tests, Python compilation, and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-07: Broke the steel-to-power bootstrap cycle exposed by `episode-20260906T195656Z-28057`: the one-furnace steel starter removes its steel-dependent substation and substitutes its local medium-pole anchors with material-ledger-funded small-pole ghosts. A stranded small pole now receives only small-pole bridge hops, so its first power connection does not demand steel. Evidence: 140 focused persistent-intermediate, material-reservation, infrastructure, power, and ghost-diagnostic tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-07: Resolved the follow-on `episode-20260906T212746Z-29624` steel/AM2 cycle: when the historical pipe-first capability ordering would defer the independent steel starter while an active loan is blocked on steel, the loan is restored and the steel conversion proceeds. The normal pipe ladder remains unchanged when no active loan needs steel. Evidence: 218 focused persistent-intermediate, construction-stock, material-reservation, and loan tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-07: Fixed the `episode-20260906T223640Z-170` copper-foundation liveness hold. A 4x4 roboport at the exact edge of a medium pole's nominal supply range can remain `no_power`; extend_power now treats that boundary as outside the usable area and places a normal material-funded hookup. Consumers strictly inside coverage still add no pole. Evidence: 125 focused power, coverage, infrastructure, and persistent-intermediate tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-07: Hardened the overnight observer/controller handoff. The loop persists OpenCode and code-fix transcripts per run, resumes the existing Terra code-fix task for the report, and starts no next episode until that task returns `continue` or creates a new focused-fix commit; failed observer or handoff attempts retry rather than ending the controller. Evidence: resume delivery verified against the completed copper-foundation report; script compilation, zero-hour invocation, and diff checks passed. Lifecycle: start the 12-hour controller for fresh isolated episodes; no mod redeploy or Factorio restart required.
- 2026-09-07: Prevented the pre-steel starter from retrying ahead of construction material it just discovered. A mall task now promotes and defers behind any stock target newly queued by its own attempted build, including the starter's three small poles, rather than only recipe or reserved-cell prerequisites. Evidence: 63 material-reservation and 10 persistent-intermediate tests passed, plus compilation and diff checks. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-07: Repaired the stone-foundation power geometry exposed by `episode-20260907T161820Z-32758`: when a support entity has no legal medium-pole terminal because its entire supply area is occupied, the material-funded bridge now falls back to a footprint-checked substation terminal with a wider supply area. Small-pole steel bootstrap bridges remain small-pole-only. Evidence: 54 focused ghost-diagnostic, mining-coverage, and infrastructure tests passed, plus compilation and diff checks. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Started conversion-stage material production at the discovery point. The steel starter now opens one unproduced rationed construction batch immediately after its conversion bill is short, so the three required small poles cannot remain merely queued behind core-mall preparation. Existing loan allocation, material checks, and restoration remain authoritative. Evidence: 74 focused material-reservation and persistent-intermediate tests passed, plus compilation and diff checks. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Replaced the automatic legacy post-run Freetoken Helper lifecycle with a permanent read-only OpenCode Helper. Every new deterministic runner starts one independent Muse Spark xhigh session, sends it only new runner-log, manifest, loopback logistic-inventory, prior-findings, and recent-commit evidence every minute, then steers that same session at `RUN END` to finish one findings document. Completion appends the absolute findings and run-directory paths to the runner log; a failure/timeout sends up to three same-session `continue` retries and then records an explicit stop. Legacy packet code remains source-compatible but is no longer launched. Evidence: 60 focused OpenCode-helper, legacy-helper compatibility, dashboard-runtime, and managed-runner tests passed; Python compilation and diff checks passed. Lifecycle: start a new deterministic runner/fresh run; no mod redeploy or Factorio restart required.
- 2026-09-08: Kept submitted plate-foundation reconciliation inside the construction lifecycle. A `ProductionPrerequisiteDeferred` from a bot-built roboport coverage wave now retains the persisted replacement, logs its exact wait, and performs the normal bounded foundation poll instead of escaping as an unhandled episode error. Evidence: 62 focused prep-extraction, bootstrap-district, and mining-coverage tests passed, plus compilation and diff checks. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Made code-fix commits the overnight loop's durable continuation signal. Before resuming the Terra task, the controller recognizes a descendant commit of the per-run report's revision as a completed handoff; this survives an app-only `continue` reply or a transient active-writer conflict and prevents a duplicate code-fix request. Evidence: descendant detection verified against the completed `bb7205e` report and its `edd948a` fix; script compilation and diff checks passed. Lifecycle: restart the controller for the next fresh isolated run; no mod redeploy or Factorio restart required.
- 2026-09-08: Added `/inventory` to the Operations Console. The read-only logistic-inventory polling path now persists raw distinct-tick snapshots by `RUN START` boundary, immediately begins a separate series for each new run, retains only the newest five runs, and renders selectable item charts with run comparison. Evidence: 29 focused inventory-history/dashboard tests plus Python and JavaScript syntax checks passed. Lifecycle: restart the Operations Console; no mod redeploy, Factorio restart, or runner restart required.
- 2026-09-08: Removed three deterministic bootstrap speed taxes found in `episode-20260907T190049Z-11608`: a combined foundation preflight now claims its own persisted mine/refinery packet reservations instead of demanding an exact duplicate bill; the belt stockout watchdog activates only after the standing belt cell is established, leaving the first foundation's exact target intact; and post-metal circuit/splitter reserves now fill alongside stone while yielding controller service to construction demand. Foundation polling is 10s while retaining a 120s unchanged-pass horizon. Evidence: 284 focused reservation, construction-stock, smelter, prep, and loop tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Made the steel starter's pole choice stock-aware after `episode-20260907T210255Z-27468`: when transferable medium-pole stock funds every local anchor, the starter retains those already-paid poles and avoids the wood-gated small-pole recipe; otherwise it preserves the all-small-pole anti-cycle plan. Evidence: 113 focused persistent-intermediate, material-reservation, and ghost-diagnostic tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Protected the steel starter's finite pole bill after `episode-20260907T213637Z-16983` showed its three-pole reservation losing one pole to earlier downstream and concurrent construction reservations. Submission now supports explicit material priority, and the one-furnace steel prerequisite reserves critically so other work carries any aggregate shortfall. Evidence: 248 focused reservation, conversion, coverage, infrastructure, and power tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Advanced the steel starter's pole reservation after `episode-20260907T223157Z-24466` drained medium poles before submit-time priority could engage. At run open, a construction target whose live recipe closure needs steel now declares the eventual steel-conversion project with its planner-derived three-pole seed bill, fencing those cyclic anchors before foundation spending; missions without a steel dependency reserve nothing. Evidence: 394 focused initialization, reservation, conversion, coverage, infrastructure, and power tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Made parked chemical handoffs advance their named rung after `episode-20260907T231341Z-19538` deferred bulk inserters on plastic for 181 seconds without creating any oil capability work. Before applying the existing 60-second anti-churn backoff, the controller now gives the rung one `ensure_produced` action and queues any resulting construction bill through the normal mall scheduler. Evidence: 271 focused construction-stock, reservation, chemical-stage, persistent-intermediate, and loop tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Collapsed the repeated early electrical repair loop seen in `episode-20260907T231341Z-19538`: reachable roboport ghosts now block concurrent near-duplicate coverage hops, new roboports/direct starters/compact mall cells stage their generated pole branch before the machine blueprint, and a standing starter repairs its anchor before coverage work. Direct-starter retirement now leaf-prunes only empty poles until a live/ghost load or wire junction, while paired requester refresh removes stale recipe groups only from the reassigned side. Evidence: 624 focused coverage, power, starter-retirement, mall, reservation, and construction tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Correlated every shared RCON command response with its request packet after the intermediate `episode-20260907T234941Z-10457` run handed a delayed `mall-bootstrap:v4` response to `find_line` and crashed while parsing it as a count. Packets belonging to prior requests are now drained instead of leaking into later telemetry, and matching packets with invalid response types fail explicitly. Evidence: 4 focused RCON/line-survey tests passed; Python compilation and diff checks passed. Python-only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Made stale-ghost remediation place-or-explain after `episode-20260908T004016Z-24135` repeated the compact gear-mall stall: an executor report with one attempted but zero newly placed ghosts no longer produces a false `STALE GHOST: rebuilt` claim or another ten no-op rounds. It now stops with typed `stale_ghost_rebuild_failed` evidence naming the ghost entity, position, pending diagnosis, and all placement counts/failures. Evidence: 58 focused remediation and ghost-diagnostic tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live discrimination, with no mod redeploy or Factorio restart required.
- 2026-09-08: Made stale-ghost removal exact after `episode-20260908T005625Z-14179` proved the generic radius remover reported success while the left mall assembler ghost remained and resubmitted as already present. Remediation now targets the named force-owned entity ghost, verifies it is absent before replacement, accepts a concurrently resolved target without recreating it, and raises typed `stale_ghost_removal_failed` before submission if clearing fails. Evidence: 60 focused remediation and ghost-diagnostic tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Preserved stale-ghost rebuild history through the construction watchdog after `episode-20260908T010753Z-1419` proved exact removal and 1/1 resubmission succeeded but the mall assembler ghost stayed pending through the second window. A rebuilt entity ghost that remains now stops with typed `stale_ghost_construction_failed` evidence naming its stage, entity, position, reason, remaining count, and elapsed rounds instead of falling back to `untyped_stuck/remedy=none`. Evidence: 61 focused remediation and ghost-diagnostic tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live discrimination, with no mod redeploy or Factorio restart required.
- 2026-09-08: Added dispatch-level ghost evidence after `episode-20260908T011911Z-2353` proved the rebuilt mall assembler remained generically `pending`. The shared blockage survey now preserves material-shortage precedence while reporting logistic network ID, total and available construction robots, placing item, required count, and network stock; a zero-free-bot ghost is distinguished as `no_available_construction_robots`, and the post-rebuild typed blocker carries those fields. Evidence: 62 focused remediation and ghost-diagnostic tests passed; Python compilation and diff checks passed. Python/controller telemetry only; start a fresh deterministic episode for live discrimination, with no mod redeploy or Factorio restart required.
- 2026-09-08: Correlated scoped research-status reports after `episode-20260908T013419Z-7420` received an impossible unscoped dashboard response during targeted preflight. Scoped bridge requests now carry a validated request ID, the mod writes that ID in the report filename, and collection waits for that exact file while preserving the legacy unscoped command. Evidence: 31 focused research/report tests passed; Lua parsing, Python compilation, and diff checks passed. Python and Lua mod change; the next fresh campaign must redeploy the mod and restart Factorio before runner validation.
- 2026-09-08: Distinguished physical ghost blockage after `episode-20260908T014829Z-31623` left one mall assembler pending despite 49 free bots and four placing items. The read-only ghost survey now applies Factorio's own `ghost_revive` collision check, records bounded footprint overlaps, and raises typed `ghost_placement_blocked` evidence instead of spending another window on the same pose-preserving resubmit. Evidence: 64 focused ghost-diagnostic and remediation tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live discrimination, with no mod redeploy or Factorio restart required.
- 2026-09-08: Corrected assembler footprint reservations after `episode-20260908T020335Z-1060` proved a mall AM1 ghost overlapped its own pre-power bridge pole. All three assembling-machine tiers now reserve their real 3x3 tiles through the shared footprint registry, so bridge routing excludes every future machine tile, including the observed `(35,32)` collision. Evidence: 106 focused footprint, mall, and ghost-diagnostic tests passed; both paired mall halves passed self-collision validation; Python compilation and diff checks passed. Python/planner only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Ordered direct-starter construction service before its synchronous power bridge after `episode-20260908T021314Z-4624` left two far stone-brick chain poles outside bot reach. Every direct starter now reserves its footprint, stages material-funded roboport coverage to the remote site, and only then submits and waits for its bot-built pole chain; the blueprint retains its idempotent pre-submit coverage check. Evidence: 82 focused direct-starter, bootstrap, and prep tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Reduced one-furnace sampled plate lines to two medium poles after `episode-20260908T022627Z-18659` reached steel with exactly two stocked medium poles but selected three wood-dependent small poles while wood had no source. The output-tap pole now also supplies the lower furnace inserter, eliminating the redundant lower-row pole; the steel starter's early critical reservation derives the same two-pole bill and avoids the wood branch when those anchors remain stocked. Evidence: 162 focused conversion, reservation, collision, and smelter tests passed; Python compilation and diff checks passed. Python/planner only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Staged complete construction coverage before submitting a new chemical-district coal mine after `episode-20260908T041654Z-11337` left its far substation at `(-329,7)` outside effective bot service for the full synchronous infrastructure window. The coal path now uses the same reserved-footprint coverage gate as other remote chemical packets, so each bot-built roboport wave is observed before the mine ghosts are released. Evidence: 118 focused chemical-stage, extraction, and mining-coverage tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Prevented producer-backed power bridges from deadlocking their own material supply after `episode-20260908T051731Z-1246` submitted a coal-coverage hop with zero transferable medium poles and then waited five minutes on five unbuilt ghosts. Synchronously observed infrastructure now requires its full transferable bill before submission, returning shortages to the mall loop so it can produce the poles before the bridge blocks. Evidence: 211 focused submission, power, coverage, chemical-stage, and mining tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Deferred conversion support-power diagnosis until the stage's planned local pole ghosts finish construction after `episode-20260908T061833Z-12992` treated the steel output inserter as stranded while its adjacent pole was still a ghost, attempted a redundant bridge, and replanned the starter onto wood-dependent poles. Conversion stages now complete normal stage remediation first and add an external support bridge only for an inserter that remains unpowered afterward. Evidence: 202 focused conversion, intermediate, power, mining, and smelter tests passed; Python compilation and diff checks passed. Python/controller only; start a fresh deterministic episode for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-08: Accounted for capped-assembler downstream draw from the 14:47 run: an upstream belt batch now reserves queued consumer recipes plus two preloaded crafts (three splitters add 12 consumed + 8 retained belts before normal spare/WIP margin). Once the direct metal starters retire, every assembled mall demand becomes a blocking whole-stack batch, rounded to additional stacks for larger bills, eliminating repeated one-to-five-item recipe switches. Evidence: the isolated commit snapshot passed 150 focused construction-stock/belt-economy tests and the 778-case fast gate (one expected skip); Python compilation and diff checks passed. Python/controller/docs only; start a fresh deterministic episode for live validation, no mod redeploy or Factorio restart required.
- 2026-09-08: Removed the latest run's post-starter serialization traps: full-stack blocking now applies to high-volume consumables and circuits, while four AM2s stay four and intermediates such as iron sticks stay exact; the compact mall grows from 8 to 16 slots after both metal starters retire, independent demands claim free cells even while another loan runs, and capped work can reclaim an idle completed cell. Pre-plastic power growth now builds complete solar-only lattice units instead of opening an impossible accumulator batch; later units restore 1:1 storage, and every selected unit's attainable materials enter the normal mall queue. Evidence: the exact commit passed 265 focused tests, 772 deterministic tests, and the 785-case fast gate (one expected skip); Python compilation and diff checks passed. Python/controller/docs only; keep the stopped loop stopped and use a fresh deterministic episode only when live validation is requested, with no mod redeploy or Factorio restart required.
- 2026-09-08: Replaced the post-starter 16-slot policy with the requested plastic boundary: twelve shared assemblers before plastic, then forty-eight permanent per-item halves plus twelve isolated demand-capacity halves. Post-plastic solid demand above 5/s promotes to a six-machine belt-fed line with logistic inputs disabled; iron demand continues past the 96-drill bootstrap ladder into later refinery generations, selects fast belts for new work, and opportunistically upgrades only exact ledger-owned yellow belts, undergrounds, and splitters. Evidence: 431 focused deterministic tests and the full 2,675-test suite passed (sandboxed loopback tests rerun with narrow socket permission); Python compilation and diff checks passed. Python/controller/docs only; keep the stopped loop stopped and use a fresh deterministic episode only when live validation is requested, with no mod redeploy or Factorio restart required.
- 2026-09-08: Deferred all new power-generation construction until advanced-circuit output is proven after `episode-20260908T140833Z-173` let an unnecessary twelve-panel solar batch outrank the iron foundation and chemical ladder. Grid-connection repair remains active before that gate, but capacity sizing cannot queue panels or accumulators, and solar panels are no longer unconditional opening mall stock. Evidence: 71 focused power and starter-stock tests plus the full 2,678-test suite passed; Python compilation and diff checks passed. Python/controller/docs only; keep the stopped loop stopped and use a fresh deterministic episode only when live validation is requested, with no mod redeploy or Factorio restart required.
- 2026-09-09: Replaced the five-minute blind infrastructure tail exposed by `episode-20260908T151329Z-24837`. A synchronous pole or roboport ghost that stays flat for ten seconds is now surveyed for network, bots, material, and revive legality; an item with no live supply chain returns immediately to the mall as a normal shortage, while any eventual timeout preserves the owning plan and full ghost diagnostics. The observed legal/reachable `(57.5,46.5)` pole had network 2, 50/50 free construction bots, and zero network pole stock; its recovered one-pole bill now invokes the existing post-starter whole-stack policy and makes 50 poles. Evidence: read-only isolated-server RCON diagnosis, 98 focused controller/power/ghost tests, and the full 2,681-test suite passed; Python compilation and diff checks passed. Python/controller/docs only; keep the stopped loop stopped and use a fresh deterministic episode only when live validation is requested, with no mod redeploy or Factorio restart required.
- 2026-09-09: Removed the initial plate-foundation supply-chain gate exposed by `episode-20260908T201853Z-7531`: the exact iron bill was known at +132s but mine/refinery ghosts did not appear until +1300/+1313s, versus roughly +694/+707s in recent runs. Collision-checked additive mine/refinery blueprints now submit immediately after reachable coverage is staged, and their exact material shortfall becomes binding mall priority so construction and production proceed concurrently; destructive cutovers and synchronously awaited service infrastructure remain fully funded. Evidence: 200 focused controller/planning tests passed, the full 2,684-test suite passed apart from five sandbox-denied loopback tests which passed with socket permission, and Python compilation plus diff checks passed. Python/controller/docs only; the active episode remains untouched and a fresh runner/episode is required for live validation, with no mod redeploy or Factorio restart required.
- 2026-09-09: Made binding construction drive mall handoffs: exact bills retire without optional spare margins, a pass-level sweep releases completed spare loans even after demand retirement, standing topology yields through the completion handoff, and deferred batches no longer hide ready peers. Additive direct-mine retries now reach refinery submission without waiting for every drill; power servicing and district-validation/retirement gates remain intact. Evidence: 342 focused scheduling, loan, foundation, and loop tests passed in an isolated snapshot of the exact scoped changes; compilation and diff checks passed. Python/controller only: restart the runner for activation and use a fresh episode for timing validation when authorized; no mod redeploy or Factorio restart required. Live base and update loop untouched.
- 2026-09-09: Audited the latest iron-expansion failure and repository maintenance hotspots. Refinery storage taps no longer demand bulk inserters on growth; chemical-dependent batches require observed predecessor production; power bridges compare fully stocked long-reach routes; first steel can use its stocked substation when medium poles are exhausted; existing mall stock-gate updates no longer reserve assemblers. Removed three obsolete/unused helpers and seven redundant source-inspection tests, preserving safety and behavioral coverage; findings and remaining accounting risks are in docs/deterministic/repository_audit_2026-09-09.md. Evidence: 468 focused tests passed against an isolated snapshot of the scoped staged changes; broader suite had only five sandbox-denied socket failures, all five passed with permission; compilation, steel-plan schema validation, and diff checks passed. Python/planner only: restart runner for activation, fresh episode for live validation; no Lua redeploy or Factorio restart required. No live base or update-loop changes.
- 2026-09-09: Starter completion now verifies generation at every built local pole, including both stone anchors. Retired starter branches can prune redundant loops only after a bounded observed alternate-wire-path proof; consumers and necessary grid links remain protected, and pole ghosts no longer count as consumers. Evidence: 61 focused tests passed, including leaf/loop cleanup and both-anchor verification; diff checks passed. Python only: restart runner for activation; no mod redeploy or Factorio restart required. Live layout and timing remain unverified; base and update loop untouched.
- 2026-09-09: Fixed the construction/mall scheduling deadlock from episode-20260909T150933Z-22622: pending extraction yields to queued mall production while retaining its district/bill, constructing growth retains binding priority, and optional core-mall promotion yields to binding construction. Duplicate expansion, safe cutover, prerequisite admission, and unchanged-pass thresholds remain intact. Evidence: 241 focused scheduling, construction-stock, persistent-intermediate, and cohesive-expansion tests passed; diff checks passed. Python only: restart runner for activation; fresh episode needed for live completion/timing validation. No mod redeploy or Factorio restart required; live base and update loop untouched.
- 2026-09-09: Fixed the belt stock-gate tier mismatch from episode-20260909T161630Z-22457. Mall refresh resolves actual per-position assembler tiers in one survey, includes tiers in its refresh signature, and defers absent/ghost targets without replacement placement. Evidence: 182 focused mall/recovery/stock tests passed, including mixed tiers and upgrade refresh. Python only: runner restart activates; user requested a fresh isolated episode for live validation. No Lua redeploy required; recurring update loop unchanged.
- 2026-09-09: Fixed the iron-to-steel feed direction mismatch that stopped episode-20260909T170646Z-6569 before plastic. Belt route survey preserves the observed live source heading, and belt-to-chest routing carries that constraint through direct and detour geometry without removing the source. Evidence: 94 focused transport/conversion tests passed, including four source orientations; diff checks passed. Python/planner only: restart runner for activation; plastic output remains live-unverified. No base mutation or update-loop change.
- 2026-09-10: Replaced early steel's routed one-furnace line with a persisted compact belt-side seed after iron-pioneer retirement, with a direct-mining fallback and no pipe prerequisite. Advanced-circuit output releases an additive splitter-fed six-furnace replacement near the mall; complete route/material preflight precedes submission, and bot retirement retains the seed until replacement craft evidence. Evidence: 272 focused steel, construction, bootstrap, and expansion tests passed; compilation/diff checks passed. User requested a fresh isolated episode; Python changes activate on runner restart, no Lua change. Live plastic and later steel transition remain unverified; recurring update loop unchanged.
- 2026-09-10: Starter retirement now triggers on owned replacement craft evidence plus matching product in its provider, with generated power verified, without waiting for all construction ghosts. Modular construction polls check this boundary; released partial districts remain eligible for reconciliation. Steel persists first delivery/seed retirement separately from completion. Remaining bills and protected power links stay intact. Evidence: 149 focused lifecycle, steel, foundation and expansion tests passed; live episode untouched. Python only: restart runner to activate; no mod redeploy required.
- 2026-09-10: Removed unsupported explicit recipe assignments from both compact steel-seed furnace variants; smelting is selected from delivered inputs, matching existing starter plans. Regression covers all orientations and both variants. Evidence: 56 focused steel/bootstrap/persistent-intermediate tests and diff checks passed. Planner-only; user authorized a fresh isolated run with latest committed retirement behavior. No Lua changes or recurring update-loop changes.
- 2026-09-10: Reconciled live entity-ghost bills into each scheduling survey so retired batches/starters cannot hide unfinished construction; stale craft proof no longer retires an unfunded live ghost bill. Core-mall readiness separately accepts real powered non-loaned assemblers stopped by verified satisfied item stock caps, without relaxing chemical production gates. Evidence: 218 focused scheduling/mall/foundation tests passed; guarded read-only isolated-server queries at tick 460795 confirmed exactly two splitter ghosts and the capped fast-inserter cell at (36.5,44.5). Python only: restart runner for controller validation; no mod redeploy. Base and recurring update loop unchanged.
- 2026-09-10: Fixed the AM2/steel/pipe loan order cycle from cycle 7 and added zero-behavior yield-decision telemetry from cycle 8. A bootstrap holder blocked on an input whose missing ladder predecessor is the waiter now yields despite partial progress or binding shields, sequencing the dependency instead of time-slicing. Every serial handoff also emits one guarded read-only `LOAN YIELD DECISION` line naming waiter flow, holder blocks/ladders/shields/progress, and blocker records carry `unbacked_draws` structurally. Evidence: 216 focused reservation/loop-bounds/stock tests passed; episode-20260910T011148Z-1291 ran +2443s with the behavior fix live (new drill-gate terminal, iron SWAP + refineries VERIFIED). Python only: fresh episode activates the telemetry line; no mod redeploy. Base and recurring update loop unchanged.
- 2026-09-11: Reviewed the twelve-hour campaign; completed pending terminal diagnostics and corrected reserved-container stock being described as network supply. Missing-input verdicts now distinguish transferable stock, per-craft shortages, and unreadable evidence. Campaign retries require an explicit change verdict plus a detected edit; stop cannot be overridden by dirty files. Evidence: 178 focused stock/controller/budget/lifecycle tests passed; review and revised experiment cycle in docs/deterministic/campaign_review_2026-09-11.md. Python only: restart campaign controller and runner for activation; no mod redeploy or Factorio restart. Underlying splitter stall remains live-unverified; no lifecycle or factory action performed.
- 2026-09-11: Reworked the campaign observation workflow around evidence discipline: observers record timestamped facts/deltas/evidence refs with labeled hypotheses (unknown stays unknown; total/accessible/allocated stock distinguished), comparisons use three references (preceding, best-milestone, same-mechanism), outcomes classify as improvement/evidence/regression/inconclusive, fixes require failure->hypothesis->evidence->competing-explanation->fix->predicted-result with missing-observation requests instead of guesses, failed episodes are inspected before reset, and no cycle must produce a code change. Evidence: 12 orchestrator prompt-contract tests passed; compile checks passed. Prompts/docs only; next fresh episode activates them, no mod redeploy. Base and recurring update loop unchanged.
- 2026-09-11: Fixed cycle 18's substation transmission corridor to the chemical coal expedition. Shared power bridges now place medium/big poles only (preserving the existing small-pole steel-bootstrap path), retain existing substations as connection anchors, and never choose a stocked substation trunk or dense-layout terminal fallback. Big-pole routes compare medium/big consumer hookups and log their actual pole bill. Local cluster layouts retain seeded substations; manufacturing remains gated on advanced-circuit production. Evidence: pasted episode-20260910T193449Z-14285 log traced to route selection; 84 focused infrastructure/ghost/coverage/power tests passed and diff checks passed. Python only: restart runner for activation; no mod redeploy or Factorio restart required. Live server and campaign untouched; guard-vs-coverage progress remains a separate unresolved issue.
- 2026-09-11: Reproduced cycle 19's exact pumpjack/body-versus-sibling-output collision, then fixed opening/expansion siting with shared body+outlet reservations and legal rotation fallback. Extracted pure geometry into planners/pumpjack_siting.py; delegated cleanup consolidated chemical power/construction packet splitting without changing metadata, reservations, or action order. Independent siting review found no blocking issue. Evidence: failing exact/translated reproductions before the fix; 314 combined chemical/resource/fluid/coverage/budget tests passed afterward, with unchanged plan collision validation. Python only: runner restart activates; no Lua redeploy or Factorio restart required. No live actions; oil/science completion and throughput improvements remain unverified.
- 2026-09-11: Fixed unexpected fast belts in longitudinal mine expansion: generator now receives the selected tier; expansion preflight no longer rewrites exact entity/removal identities according to incidental stock. Added bounded offline run context, batched coordinate/plan lookup, and a rebuildable SQLite full-text history index over raw run evidence; campaign/helper prompts use rolling packets, with matched inventory deltas and post-run samples excluded. Updated troubleshooting workflow/skill. Evidence: 216 combined transport, observer, inventory, retention and retrieval tests passed; actual supplied run packet and two-run historical search exercised; skill validator passed. Python only: restart runner, helper and campaign controller for activation; no mod redeploy/Factorio restart. No live mutation or lifecycle action; terminal drill/inserter circuit starvation remains a separate unresolved problem.
- 2026-09-11: Added Operations Console observability panel: offline current-run evidence, log freshness, copy/download handoff, inventory-history link, and bounded cross-run search. Read-only endpoints use fixed server-owned paths; missing index and stale refresh states are explicit. Evidence: 29 dashboard tests, JavaScript syntax and diff checks passed; browser fixture preview verified packet rendering and historical search. Lifecycle: restart Operations Console and refresh browser; no Factorio/runner restart needed. Live services untouched.
- 2026-09-11: Finalized the pending Window 4 belt-route diagnostics: unroutable bridges now emit belt_bridge_unroutable with ingredient, original/surveyed endpoints, source belt, direction constraints, attempted tiers, route limit and planner reason; planner direction errors name endpoints. No routing behavior changed. Evidence: 103 focused detour/feed/mission/chemical tests passed, including blocker JSONL persistence; diff check clean. Raw failed-run lines 3008–3027 also show the chemical-processing origin shifting from (-318,-39) to (-218,-39) after packet submission/coverage wait, a retry-identity hypothesis requiring separate investigation. Python only: restart runner to activate; no mod redeploy or Factorio restart required. Campaign and failed world left untouched.
- 2026-09-11: Made the campaign's main OpenCode session responsible for verifying subagent diagnoses and implementing, testing, reviewing and committing supported fixes without handing routine repository decisions back to the user. Analysis-only exits now require a concrete unresolved blocker; lifecycle and repeat-failure guards remain with the controller. Fixed retry change detection to include staged changes and committed trees, so committing a fix cannot hide it behind a clean worktree. Evidence: 20 campaign/lifecycle tests passed, including real temporary-repository staged/committed detection; diff check passed. Restart campaign controller to load new prompts; no runner/mod/Factorio change required. Stopped campaign remains untouched.
- 2026-09-11: Fixed starvation misclassified as gate drift: gate-refresh budget and mall_loan_gate_mismatch now fire only for a fed cell (transferable + requester covers one craft); starved cells supply-wait without consuming budget. Evidence: cycle-20 terminal (AM2 e-circuit step, 3 refreshes, cable net 4, crafts 0/2) unified with Sep-3/4 gate_mismatch terminals; 141 focused construction-stock tests passed. Python only: fresh episode activates; no mod redeploy.
