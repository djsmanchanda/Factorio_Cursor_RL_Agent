<!-- Path: docs/deterministic/README.md | Purpose: Describe the current deterministic runtime honestly and define what RL may reuse. -->

# Deterministic runtime

## Role

The deterministic runtime is the current real-base implementation and a measurable baseline for learning. It is not the project's final architecture and is not assumed correct merely because it has broad test coverage.

Active path:

```text
tools/autonomous_run.py
  -> orchestrator/autonomous_builder.py
  -> orchestrator/stage_*.py
  -> planners/*.py
  -> factorio_mod build-plan executor
```

Use explicit `surface=nauvis` and `force=player` for real-base work.

## Semi-stable mechanics worth sharing

- Live recipe, technology, entity, and inventory exports.
- Versioned snapshots, goals, plans, and reports.
- Collision and occupancy surveys.
- Material preflight and legal build-plan execution.
- Measurements for power, logistics, machines, belts, fluids, and research.
- Safe failure reporting and lifecycle tooling.

Sharing means extracting a narrow interface. It does not mean importing the full orchestrator or its layout choices into training.

## Known weaknesses

- Layout and transport are often added after machine placement instead of planned as one structure.
- Recovery can encode one observed failure as a new deterministic branch.
- Supply-starved machines, finite mall demand, and true capacity shortages have been confused.
- Power, roboport, belt, pipe, and future-expansion corridors are not consistently reserved.
- Live deployment drift can make tested code differ from running behavior.

Fix these when they block the real runtime or when the fix produces a reusable primitive. Do not let deterministic cleanup displace the RL roadmap.

## Real-base rule

Observe the live factory, identify the planner decision that caused the failure, fix the code and focused tests, deploy deliberately when Lua changed, then rerun from the requested save. Never manually repair the base to conceal a planner defect.

## Planned Factorio 2.1 science integration

[Science telemetry and research control plan](science_control_plan.md) stages
lab-inventory/status observation, deterministic diagnosis and research
ranking, and a disposable-only lab-circuit experiment.  The Python planner
retains research-selection authority until a separately authorized and tested
controller is promoted.
