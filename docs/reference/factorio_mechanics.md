<!-- Path: docs/reference/factorio_mechanics.md | Purpose: Keep high-value Factorio facts used by planners, validators, and rewards. -->

# Factorio mechanics reference

Treat live prototype and force exports as authoritative for the running game and mod set. External guides are hints until represented in a verified observation or contract.

## Rates and capacity

- Compare demand and supply in items per Factorio tick or per second with an explicit conversion.
- Machine count alone is not capacity. Include recipe time, crafting or mining speed, productivity, inserter delivery, belt lanes, power, and actual working state.
- Belt and inserter nominal rates are ceilings; geometry, hand movement, lane use, and pickup/drop positions affect realized throughput.
- The measured inserter reference is kept in `docs/reference/inserter_throughput_factorio_2_0_26.txt`.

## Logistics

- Prefer a continuous belt when two endpoints can be connected without an operationally useful buffer.
- Splitters may merge, split, balance, prioritize an input/output, or filter one output. Their value should be measured against cost and throughput need.
- Logistic coverage and construction coverage are different. A powered roboport can still be outside one required radius.

## Fluids

- One connected pipe network carries one fluid type.
- Validate fluid-box type, connection position, direction, rotation, and existing contents before planning.
- Pumps are directional and can separate or control networks; they do not justify mixing incompatible fluids.
- Long or heavily branched paths can reduce practical throughput. Measure delivery at the consumer.

## Electricity

- Connectivity to a pole is not proof that its electric network has generation.
- Reward stable satisfaction and low infrastructure cost, but treat disconnected required entities as failure.

## Research and recipes

- Use the live player-force recipe and technology exports.
- Displayed repeatable research levels may not equal prototype identifiers; resolve exact names before ranged forms.
- A locked recipe may inform future structure but is not currently executable.

Detailed historical game notes remain in `docs/archive/legacy-canonical/21_external_game_knowledge.md` and `23_fluid_systems.md`.
