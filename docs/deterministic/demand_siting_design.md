<!-- Path: docs/deterministic/demand_siting_design.md | Purpose: Design for siting furnaces, refineries and mall cells near their consumers instead of a fixed reference point. Research only; no code changed. -->

# Demand-based siting: design

## 1. Current siting

- **Refinery and smelter sites.** `plan_local_extraction` (`orchestrator/stage_extraction.py:870`) is called from `orchestrator/autonomous_builder.py:2942` with the static `reference_point`, whose default is `(3.0, -1.0)` (`autonomous_builder.py:4955`).
- **Anchor ordering.** `smelter_search_anchors` (`stage_extraction.py:718`) sorts by Manhattan belt length to the ore output or `reference_point`. Its docstring at `:730` says "demand proximity", but no demand value is read. This mismatch should be fixed in the docstring.
- **Site score.** `_refinery_site_score` (`stage_extraction.py:774`) returns `(input_wrong_way, output_wrong_way, input_tiles + output_tiles, output_tiles, input_tiles)`. `output_tiles` is the L1 distance to `reference_point`. Consumers are not in the score. The winner is `min(candidates)`.
- **Mall cells.** Cells are a fixed row-major grid from `reference_point`: `_DEMAND_CELL_COUNT` (`orchestrator/mall_builder.py:34`), `_QUAD_MALL_CENTER_OFFSET=(4.5,1.5)` (`:38`). `_choose_slot` (`:632`) picks a preferred, related or other pairing, then the first empty origin. It has no distance term.
- **Bills.** `compact_mall_project_bill` is called with `reference_point` only to decide `include_substation` (`autonomous_builder.py:~7300-7326`).

## 2. Existing demand data

- **Item-keyed only.** `MaterialProject` (`orchestrator/material_reservations.py:45-58`) holds `required`, `reserved`, `source_producers` (names, not positions), `expected_rates` and `eta_seconds`. It has no coordinates. The premise that bills have sites is therefore false today.
- **Positions are available live.** `requester_logistic_groups` (`orchestrator/live_base.py:2661`) reads the request sections of a requester chest at a given position. `logistic_network_ids` (`live_base.py:2222`) maps positions to networks in one RCON call. Both can locate consumers from the world, without new state.
- **Clear-area search** (`find_clear_areas`, `live_base.py:510`) already surveys several anchors in one pass. It can take consumer-derived anchors with no change to its legality rules.
- **Refinery candidates** are already legal-flow-checked before scoring (`plan_local_extraction`, route_failure filter, `_keeps_refinery_clearance` at `stage_extraction.py:851`). Demand should enter only as a soft term after these hard checks.

## 3. Laya facts

- **Option features.** `option_features` (`training/laya_guided_policy.py:37`) exposes per-option facts to rules. `next_slot` (`:~107`) is the only location fact, and it comes from `training/laya_lines.py`.
- **Line site distance is anchored to ore, not demand.** `_site_distance` (`laya_lines.py:178-184`) measures from the nearest ore zone of the block. Lines grow from that anchor (`:242-247`). `next_slot` (`:320-325`) selects the nearest sites to that anchor.
- **Mall slot facts** (`training/laya_mall_slots.py:145`) carry stock and idle state, not position.
- **Compile step.** `compile_options` (`training/laya_ops_runner.py:345`) receives no positions, only the board, results and stock. Its `bootstrap_districts` argument carries district state.
- **Laya gets `reference_point`** from `training/laya_ops.py:65-94`.

Laya therefore cannot choose a site by consumer distance today. It can only pick among options that `next_slot` already ranks by ore anchor.

## 4. Phased design

**Phase 0: measure (no behaviour change).**
- Files: a read-only probe that, for each built line, records the distance from its output to its nearest consumer. Consumers come from requester chests (`requester_logistic_groups`) and assembler inputs.
- Effect: a baseline of belt tiles and bot trips per item.
- Risk: consumer discovery misses chests outside the logistic network. Use `logistic_network_ids` to filter.
- Measurement: median and p90 consumer distance per item, recorded per run.

**Phase 1: demand anchor as a soft score term.**
- Files: `stage_extraction.py` (`_refinery_site_score`, `plan_local_extraction`). Pass a `demand_anchor` into `smelter_search_anchors` alongside `reference_point`. Keep route and clearance checks as hard filters.
- Effect: the tuple gains a demand-distance term after the wrong-way flags and before `input_tiles`, so legality and belt length still rank first.
- Risk: moves refinery sites on existing bases. Fall back to `reference_point` when no consumer exists.
- Measurement: unit test that a legal candidate closer to a consumer wins when belt length is equal, and that illegal ones never win. Then a disposable-world A/B with the same seed: total belt tiles and median consumer distance, one factor per arm.

**Phase 2: mall slot distance.**
- Files: `mall_builder.py` (`_choose_slot`, `:632`). Add the consumer distance as a tie-breaker after the preferred and related pairing.
- Effect: mall cells sit nearer the lines they feed.
- Risk: pairing order is the existing behaviour; a distance term must not override it.
- Measurement: fraction of cells whose nearest consumer is inside a fixed radius, before and after, on the same saved world.

**Phase 3: Laya option fact.**
- Files: `training/laya_lines.py` (add a `consumer_distance` to each candidate) and `training/laya_guided_policy.py` (`option_features`). Do not hand-code a preference branch. Add the fact, then test the weight as a variant under `docs/rl/laya_variants/README.md`.
- Effect: Laya sees site distance to demand and can learn whether to prefer it.
- Risk: a fact the policy learns to ignore adds no value. Adoption needs decision volume at real choice points.
- Measurement: matched cohort (n/N, frozen commit) comparing belt tiles and time to the milestone in `orchestrator/checkpoint_milestones.py`.

## 5. Open questions

- Should demand be the centroid of consumers, or the nearest consumer of the item being made?
- Does the consumer set include ghosts (`find_line(include_ghosts=True)`, `live_base.py:64`)?

## 6. Smallest first step

Phase 0: a read-only probe that reports consumer distance per line. It changes no siting code and gives the baseline that the phase 1 A/B needs.
