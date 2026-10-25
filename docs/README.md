<!-- Path: docs/README.md | Purpose: Route readers to the smallest relevant active document set. -->

# Documentation map

Read only the route relevant to the current task.

| Task | Documents |
|---|---|
| Project structure or shared boundaries | [Architecture](architecture.md), [System invariants](system_invariants.md) |
| Laya factory runner or resumption | [Current evidence](rl/Laya_Current_Handoff.md), [Runner contract](rl/Laya_Clean_Runner.md), [Strategy](rl/Laya_Strategy_Guidelines.md) |
| RL policy, structural planning, rewards | [RL system](rl/README.md) |
| Episodes, workers, evolution, autoresearch | [Training](rl/training.md) |
| Existing deterministic runtime | [Deterministic runtime](deterministic/README.md), [Planning](deterministic/planning.md) |
| Concurrent deterministic checkpoint runs | [Checkpoint fleet](deterministic/checkpoint_fleet.md) |
| RCON, deployment, restarts, live failures | [Factorio operations](factorio_operations.md) |
| Current priorities | [Roadmap](roadmap.md) |
| Parallel source work across laptops | [Two-machine task workflow](two_machine_workflow.md) |
| Game mechanics used by validators/rewards | [Factorio mechanics](reference/factorio_mechanics.md) |

The Laya route separates current capabilities from intended authority. Use
training and mechanics references only for the subsystem being changed.
`docs/archive/` and dated session/experiment reports preserve history; they are
evidence, not current lifecycle instructions. Keep documents quoted by active
guideline catalogs outside the archive, and verify references before moving files.
