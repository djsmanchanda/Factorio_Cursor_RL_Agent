# Checklist / TODO

Reconciled against actual code state on 2026-07-18 (see CURRENT_STATUS.md).

## Phase 0 – Foundation
- [x] Initial documentation suite
- [x] System invariants established
- [x] Canonical JSON schemas drafted
- [x] Agent operating instructions (AGENTS.md)

## Phase 1 – MVP (LocalLayoutPlanner)
- [x] Lua mod skeleton (`factorio_mod/control.lua` — targets Factorio 1.1, needs 2.0 port)
- [x] Area snapshot export (conform to `snapshot.schema.json`)
- [ ] Recipe DAG loader
- [ ] Single grid layout (Deterministic math) — `local_layout_planner.py` is a read-only inspector only
- [ ] Blueprint replication (conform to `build_plan.schema.json`)

## Phase 2 – Execution (RL Optimization)
- [ ] Headless Factorio setup
- [ ] Instruction language (FIL) parsing (`goal.schema.json`)
- [ ] RL executor (Targeted at build efficiency) — advisory-only `rl_advisor.py` exists; no learned policy
- [x] Reward function definition (Invariants-checked) — `rl_feedback_builder.py` (telemetry only, nothing consumes it yet)

## Phase 2.5 – Metrics & Policies
- [x] FactoryGraph-derived metrics (`core/factory_graph.py`, `core/metrics.py`)
- [x] Bot saturation detection (`core/bot_capacity_policy.py`, supervisor policy evaluator)
- [x] Power structure analysis (power stress ratio in metrics/observation)
- [x] Resource structure analysis (material supply, production gap, pressure attribution)
- [x] Policy threshold definitions (`policies/bot_thresholds.json`)
- [ ] Economic safety signal — source lost in bytecode-only commit d47af00; re-implement

## Phase 2.6 – Progress & Phasing
- [x] ProgressState schema (`core/progress_state.py`, `progress_reconciler.py`)
- [x] Capacity phasing policy (deterministic) (`core/capacity_phasing_policy.py`, `phase_advance_evaluator.py`)
- [x] Ghost-only incremental expansion (`core/ghost_slice_planner.py`, sandbox zoning, Lua ghost sink)
- [x] Deferred execution/RL (explicit future) — authorization gates enforced throughout

## Phase 2.7 – Game Bridge (added 2026-07-18; prerequisite for everything below)
- [x] Port `factorio_mod` to Factorio 2.0 (info.json, global→storage, game.*→helpers.*, created_entity→entity, get_recipe type gate)
- [x] Deploy script → `%APPDATA%\Factorio\mods` (`scripts/deploy_mod.ps1`)
- [x] Verified live snapshot from Factorio 2.0.77 validates against `snapshot.schema.json` (headless server + RCON, 2026-07-18)
- [x] RCON transport, Python→game commands (`tools/rcon_client.py`)
- [x] script-output watcher (game→Python file ingestion) (`orchestrator/game_bridge.py`)
- [x] Top-level orchestrator loop: snapshot → metrics → supervisor → planner → authorization → execution (`orchestrator/run_cycle.py`, one-shot cycle; recurring loop still TODO)
- [x] Fix capacity model: committed = current + pending ghosts; fill-delta semantics in ghost projection and execution readiness; snapshot-derived current capacity (verified live: cycle 1 projects 50 ghosts from pure observation, cycle 2 holds at delta 0)
- [x] First automated test suite: `tests/test_capacity_model.py` (9 tests; run `python -m pytest tests/`)
- [ ] Broaden test coverage beyond the capacity model (planners, executors, bridge)

## Phase N1 – Nauvis Core Mechanics (added 2026-07-18; see docs/22 curriculum)
- [x] Production lines: belts, inserters, machines with recipes (verified live, 9.6/s circuit line)
- [x] Miner-fed smelting chains; electric-only invariant; demand-scaled feeders/collectors
- [ ] T-junction sideload feeding + dedicated ingredient belts (docs/21 patterns)
- [ ] Line-to-line chaining: smelter output belt feeds assembler input
- [ ] Machine tier parameter (assembling-machine-1/2/3) and quality tiers
- [ ] Full science chain: ore → automation-science-pack → labs, research progressing (Phase N2 gate)
- [ ] Loop daemon builds lines (not placeholder grids); bottleneck diagnosis per line
- [ ] Action catalog + deterministic baseline policy (greedy bottleneck relief)
- [ ] Transition logging for the RL decision layer (docs/22)

## Phase 3 – Scaling (CityPlanner)
- [ ] Block schema & template system
- [ ] Rail corridor automation (Invariants-based)
- [ ] Station-as-interface logic
- [ ] Incremental city migration scripts

## Phase 4 – Interplanetary (PlanetPlanner & Supervisor)
- [ ] Space platform orchestration
- [ ] Inter-planet logistics DAG
- [ ] Supply chain bottleneck dashboard
- [ ] Anomaly detection for supervision
