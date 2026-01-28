# Checklist / TODO

## Phase 0 – Foundation
- [x] Initial documentation suite
- [x] System invariants established
- [x] Canonical JSON schemas drafted
- [x] Agent operating instructions (agent.md)

## Phase 1 – MVP (LocalLayoutPlanner)
- [ ] Lua mod skeleton
- [ ] Area snapshot export (conform to `snapshot.schema.json`)
- [ ] Recipe DAG loader
- [ ] Single grid layout (Deterministic math)
- [ ] Blueprint replication (conform to `build_plan.schema.json`)

## Phase 2 – Execution (RL Optimization)
- [ ] Headless Factorio setup
- [ ] Instruction language (FIL) parsing (`goal.schema.json`)
- [ ] RL executor (Targeted at build efficiency)
- [ ] Reward function definition (Invariants-checked)

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
