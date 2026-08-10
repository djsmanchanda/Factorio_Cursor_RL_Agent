<!-- Path: docs/deterministic/planning.md | Purpose: Keep useful deterministic planning principles without making them RL constraints. -->

# Deterministic planning

## Current components

- `LocalLayoutPlanner`: entity placement and local connections.
- `CityPlanner`: larger zones, corridors, and interfaces.
- `PlanetPlanner`: planet-level production and transfers.
- `InterplanetarySupervisor`: global flows and recovery.

These names describe intended responsibilities, not permission to build speculative layers before current scenarios need them.

## Planning principles

- Plan machines, inputs, outputs, power, logistics, and expansion space together.
- Prefer a continuous direct belt over chest and inserter hops when both solve the same transport problem.
- Route around established infrastructure. A blocked endpoint should trigger another candidate or a safe failure.
- Diagnose supply, delivery, inserter throughput, machine speed, and machine count in that order.
- Repair existing capacity before duplicating it.
- Keep mall reserves distinct from sustained intermediate demand. Promote intermediates to full lines when measured demand justifies it.
- Treat fluids as type-safe networks; never mix fluids through an implicit shared pipe.
- Base capacity on rates and live game facts, not machine counts alone.

These are also useful RL priors and reward features. They are not a catalog the learned policy must copy.
