# Path: tests/test_intermediate_promotion.py
# Purpose: Prove a saturated intermediate cell promotes to a shared line on its own evidence, and grows on the same phase ladder the mining system uses.

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.extraction_capacity import EXTRACTION_DRILL_PHASES  # noqa: E402
from orchestrator.intermediate_scaling import (  # noqa: E402
    ELECTRONIC_CIRCUIT_MALL_MACHINE_LIMIT,
    MALL_INTERMEDIATE_RATE_LIMIT,
    PROMOTED_LINE_PHASES,
    promoted_companion_machine_count,
    promoted_line_belt_type,
    promoted_line_machine_count,
)
from orchestrator import autonomous_builder as builder  # noqa: E402
from planners.local_layout_planner import LocalLayoutPlanner  # noqa: E402

GEARS = "iron-gear-wheel"


def test_third_circuit_mall_machine_promotes_to_the_six_machine_block() -> None:
    """Circuits keep two bootstrap cells; higher sustained demand is a line."""
    assert ELECTRONIC_CIRCUIT_MALL_MACHINE_LIMIT == 2
    assert promoted_line_machine_count(
        "electronic-circuit", 3.01, 2,
    ) == 6
    assert promoted_line_machine_count(
        "electronic-circuit", 3.0, 2,
    ) is None


def test_queued_circuit_work_beyond_two_cells_promotes_without_live_consumers() -> None:
    assert promoted_line_machine_count(
        "electronic-circuit", 0.0, 2, backlog=121,
    ) == 6


def test_six_circuits_get_the_recipe_derived_nine_cable_companion() -> None:
    assert promoted_companion_machine_count("electronic-circuit", 6) == 9


def test_direct_circuit_block_requests_an_express_full_lane_bus_when_unstocked() -> None:
    assert promoted_line_belt_type(
        "electronic-circuit", 6, {}, full_lane_input=True,
    ) == "express-transport-belt"


def test_nine_cables_use_turbo_for_their_single_lane_output() -> None:
    assert promoted_line_belt_type(
        "copper-cable", 9, {}, full_lane_input=True,
    ) == "turbo-transport-belt"


def test_promoted_circuit_block_replaces_sideload_cheats_with_belt_endpoints() -> None:
    """The six-machine block must have two real belt inputs, not requesters."""
    plan = LocalLayoutPlanner().generate_line_layout(
        "electronic-circuit", 6, 100, 200,
        belt_type="express-transport-belt", inserter_type="fast-inserter",
        feed_style="sideload", direct_bus_ingredients={"copper-cable"},
        terminal_collector=True,
    )

    endpoints = builder._direct_sideload_feeds(
        plan, "electronic-circuit", 6, 100, 200, "fast-inserter",
        frozenset({"copper-cable", "iron-plate"}),
        frozenset({"copper-cable"}),
    )

    assert endpoints == {
        "copper-cable": ((97.5, 200.5), "east"),
        "iron-plate": ((97.5, 204.5), "north"),
    }
    remaining_cheats = [
        action for phase in plan["phases"] for action in phase["actions"]
        if action.get("entity") == "infinity-chest"
    ]
    assert remaining_cheats == []


def test_promotion_does_not_use_unexecutable_stocked_belts(monkeypatch) -> None:
    from planners.recipe_data import LINE_RECIPES

    for tier in ("fast-transport-belt", "express-transport-belt", "turbo-transport-belt"):
        monkeypatch.delitem(LINE_RECIPES, tier, raising=False)

    assert promoted_line_belt_type(
        GEARS, 6, {"transport-belt": 100, "express-transport-belt": 4000},
    ) == "transport-belt"

def test_a_saturated_cell_promotes_without_measurable_demand() -> None:
    """The chicken-and-egg: live_intermediate_demand only counts WORKING
    consumers, and the consumers starved of gears are not working -- so a gear
    cell running flat out reported 0.00/s and was never promoted."""
    assert promoted_line_machine_count(GEARS, 0.0, 1) is None
    assert promoted_line_machine_count(GEARS, 0.0, 1, saturated=True) == 6


def test_saturation_climbs_the_ladder_one_phase_at_a_time() -> None:
    """A machine running flat out cannot go faster; the only move is more of
    them, and the base grows in complete doubled six-machine sets."""
    sizes, have = [], 1
    for _ in range(len(PROMOTED_LINE_PHASES)):
        have = promoted_line_machine_count(GEARS, 0.0, have, saturated=True)
        sizes.append(have)

    assert sizes == [6, 12, 24, 48, 96]


def test_the_top_phase_is_a_ceiling_not_a_loop() -> None:
    top = PROMOTED_LINE_PHASES[-1]

    assert promoted_line_machine_count(GEARS, 0.0, top, saturated=True) == top


def test_the_ladder_is_the_one_the_mining_system_uses() -> None:
    """Extraction and the assembly it feeds must step up together; a parallel
    copy of the ladder could drift."""
    assert PROMOTED_LINE_PHASES == EXTRACTION_DRILL_PHASES == (6, 12, 24, 48, 96)


@pytest.mark.parametrize("demand,expected", [(4.0, 6), (20.0, 24), (60.0, 96), (200.0, 96)])
def test_demand_driven_sizing_also_snaps_to_the_ladder(demand: float, expected: int) -> None:
    assert promoted_line_machine_count(GEARS, demand, 0) == expected


def test_an_idle_cell_under_the_rate_limit_is_left_alone() -> None:
    """Saturation is the new trigger; it must not promote everything."""
    assert MALL_INTERMEDIATE_RATE_LIMIT == 3.0
    assert promoted_line_machine_count(GEARS, 1.0, 1) is None


def test_post_plastic_demand_above_five_forces_first_six_machine_line(
    monkeypatch,
) -> None:
    existing = SimpleNamespace(
        machine_count=1, working_count=1, produced_count=10,
    )
    monkeypatch.setattr(builder, "_independent_mall_ready", lambda *_a: True)
    monkeypatch.setattr(builder, "_startup_mall_item_cap", lambda *_a: None)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder.live_base, "logistic_request_total", lambda *_a: 0,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line", lambda *_a, **_k: existing,
    )
    monkeypatch.setattr(builder, "_mall_request_multiplier", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "live_intermediate_demand", lambda *_a: 5.01)
    monkeypatch.setattr(builder, "backlog_seconds", lambda *_a, **_k: 0.0)
    monkeypatch.setattr(
        builder, "promoted_line_machine_count", lambda *_a, **_k: None,
    )

    plan = builder._plan_line(
        object(), "nauvis", "player", GEARS, lambda _message: None,
        upgrade_bootstrap=False, stock_target=100, minimum_machines=1,
        allow_promotion=True,
    )

    assert plan.promote_to_line
    assert plan.promoted_count == 6


def test_post_plastic_six_machine_promotion_has_no_logistic_inputs(
    monkeypatch,
) -> None:
    plan = SimpleNamespace(
        existing=None,
        spec=builder.LINE_RECIPES[GEARS],
        promote_to_line=True,
        promoted_count=6,
        mall_storage_limit=100,
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources",
        lambda *_a, **_k: {"iron-plate": (1.5, 1.5)},
    )
    monkeypatch.setattr(
        builder, "_promotion_upstream_shortfall", lambda *_a: None,
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    built: list[dict] = []
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *_a, **kwargs: built.append(kwargs) or (20.5, 20.5),
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", GEARS, (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert built[0]["machine_count"] == 6
    assert built[0]["allow_logistic_inputs"] is False


def test_only_promotable_intermediates_are_scaled_this_way() -> None:
    """iron-plate scales by opening mines, not by promoting a mall cell."""
    assert promoted_line_machine_count("iron-plate", 99.0, 1, saturated=True) is None


def test_saturation_never_proposes_a_line_it_already_has() -> None:
    """Re-proposing the current size would rebuild instead of expanding."""
    for have in PROMOTED_LINE_PHASES[:-1]:
        assert promoted_line_machine_count(GEARS, 0.0, have, saturated=True) > have


def test_promoted_pipe_defers_to_its_starved_iron_extraction(monkeypatch) -> None:
    """A large pipe backlog is not evidence that six pipe assemblers have iron.

    The live failure created a 9/s pipe line while the six-furnace iron system
    was still ore-starved, then tried to belt from a provider chest enclosed by
    its own inserters.  The next action must be iron extraction, not the line.
    """
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_args: {"iron-plate": 0},
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_args: SimpleNamespace(working_count=6),
    )

    shortfall = builder._promotion_upstream_shortfall(
        object(), "nauvis", "player", "pipe", 6,
    )

    assert shortfall is not None
    extraction, available_rate, required_rate = shortfall
    assert extraction == "iron-plate"
    assert available_rate < required_rate


def test_promoted_line_expands_raw_input_before_building_downstream(monkeypatch) -> None:
    plan = SimpleNamespace(
        spec=builder.LINE_RECIPES["pipe"],
        promote_to_line=True,
        promoted_count=6,
        existing=None,
        mall_storage_limit=1,
        fill_provider=False,
    )
    monkeypatch.setattr(
        builder, "_ingredient_sources", lambda *_args, **_kwargs: {"iron-plate": (1.5, 1.5)},
    )
    monkeypatch.setattr(
        builder, "_promotion_upstream_shortfall",
        lambda *_args: ("iron-plate", 3.75, 9.0),
    )
    expanded: list[str] = []
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *_args, **_kwargs: expanded.append("iron-plate"),
    )
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *_args, **_kwargs: pytest.fail("pipe line must not be built first"),
    )

    builder._build_assembled_stage(
        object(), object(), "nauvis", "player", "pipe", (0.0, 0.0),
        lambda _message: None, plan, None, upgrade_bootstrap=False,
    )

    assert expanded == ["iron-plate"]


def test_large_belt_backlog_borrows_duplicate_cable_before_shared_line(
    monkeypatch,
) -> None:
    existing = SimpleNamespace(machine_count=1)
    plan = SimpleNamespace(
        existing=existing, promote_to_line=True,
        production_target=200, mall_storage_limit=400,
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())

    def line(_client, _surface, _force, recipe, _machine):
        if recipe in {"iron-gear-wheel", "copper-cable"}:
            return SimpleNamespace(machine_count=2)
        return None

    monkeypatch.setattr(builder.live_base, "find_line", line)
    started: list[tuple[str, int, dict]] = []
    monkeypatch.setattr(
        builder, "_start_bootstrap_loan",
        lambda *_a, **kwargs: started.append((_a[4], _a[5], kwargs))
        or "borrowed duplicate cable cell",
    )
    messages: list[str] = []

    assert builder._allocate_dynamic_belt_capacity(
        object(), object(), "nauvis", "player", "transport-belt",
        (0.0, 0.0), messages.append, plan, 120,
    )
    assert started == [(
        "transport-belt", 120, {
            "spare_target_count": 400,
            "allowed_original_recipes": frozenset({"copper-cable"}),
        },
    )]
    assert any("two gear assemblers" in message for message in messages)


def test_dynamic_belt_capacity_builds_missing_anchor_instead_of_six_line(
    monkeypatch,
) -> None:
    plan = SimpleNamespace(
        existing=SimpleNamespace(machine_count=1), promote_to_line=True,
        production_target=200, mall_storage_limit=400,
    )
    monkeypatch.setattr(builder, "active_bootstrap_loans", lambda *_a: ())

    def line(_client, _surface, _force, recipe, _machine):
        count = {"iron-gear-wheel": 1, "copper-cable": 2}.get(recipe)
        return SimpleNamespace(machine_count=count) if count else None

    monkeypatch.setattr(builder.live_base, "find_line", line)
    ensured: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        builder, "ensure_produced",
        lambda *_a, **kwargs: ensured.append((_a[4], kwargs)),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred) as raised:
        builder._allocate_dynamic_belt_capacity(
            object(), object(), "nauvis", "player", "transport-belt",
            (0.0, 0.0), lambda _message: None, plan, 120,
        )

    assert raised.value.code == "dynamic_mall_anchor_wait"
    assert ensured[0][0] == "iron-gear-wheel"
    assert ensured[0][1]["minimum_machines"] == 2
    assert ensured[0][1]["allow_promotion"] is False
