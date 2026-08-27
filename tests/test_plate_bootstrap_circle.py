# Path: tests/test_plate_bootstrap_circle.py
# Purpose: Plate startup uses one removable direct stack, then exposes the full
# mine/refinery bill without requester transport or circular belt demand.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder
from orchestrator.parts_mall import MaterialShortage


def _extraction() -> SimpleNamespace:
    return SimpleNamespace(
        build_plan=None, drill_count=6, furnace_count=6, ore="iron-ore",
        ore_output=(12.5, -1.5), smelter_origin=(4.0, 29.0),
        mining_productivity_bonus=0.3, smelter_flow_direction="east",
    )


def _short_belts() -> MaterialShortage:
    return MaterialShortage(
        "initial_iron-plate_system", {"transport-belt": 53}, {"transport-belt": 13},
    )


def test_first_plate_refinery_bootstraps_when_inserters_are_circular(monkeypatch) -> None:
    shortage = MaterialShortage(
        "initial_iron-plate_system",
        {"transport-belt": 30, "inserter": 12},
        {"transport-belt": 0, "inserter": 0},
    )
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a: None)

    assert builder._cold_start_belt_shortage(
        object(), "nauvis", "player", "iron-plate", shortage,
    )


def _wire_cold_base(monkeypatch, extraction) -> list:
    calls = []
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(builder, "_cohesive_smelter_target", lambda *_a: (None, None))
    monkeypatch.setattr(
        builder, "_submit_mining_plan", lambda *_a, **_k: calls.append("mine"),
    )
    monkeypatch.setattr(builder, "retire_depleted_mines", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    return calls


def test_cold_belt_shortage_uses_the_direct_starter_without_an_intake(
    monkeypatch,
) -> None:
    extraction = _extraction()
    calls = _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    starter = builder.live_base.DirectPlateStarter(
        (61.5, 24.5), "north", 1,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter_site",
        lambda *_a, **_k: starter,
    )
    built = []
    monkeypatch.setattr(
        builder, "_serve_direct_plate_starter",
        lambda *_a, **_k: built.append(_a[6]) or (61.5, 18.5),
    )

    provider = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
        lambda _m: None,
    )

    assert provider == (61.5, 18.5)
    assert calls == ["mine"]
    assert built == [starter]


def test_one_furnace_starter_makes_the_full_belt_bill_visible(monkeypatch) -> None:
    """Once direct plate production exists, the proper system's missing belts
    are normal construction demand rather than another bootstrap trigger."""
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a, **_k: SimpleNamespace(
            machine_count=1, machine_positions=((61.5, 21.5),),
        ),
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_direct_plate_line",
        lambda *_a, **_k: pytest.fail("must not add a second starter"),
    )

    with pytest.raises(MaterialShortage, match="transport-belt"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
            lambda _m: None,
        )


def test_non_belt_shortages_are_never_absorbed(monkeypatch) -> None:
    """A drill shortfall has a producer that can grow; queue it as before."""
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    shortage = MaterialShortage(
        "initial_iron-plate_system", {"electric-mining-drill": 14}, {},
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(shortage),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_direct_plate_line",
        lambda *_a: pytest.fail("drills are not a belt-family shortage"),
    )

    with pytest.raises(MaterialShortage, match="electric-mining-drill"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
            lambda _m: None,
        )


def test_success_expires_the_stale_unbacked_draw(monkeypatch) -> None:
    """A saturated-but-full provider read as 'nothing is producing' in the
    stall report because the unbacked-draw note outlived its producer."""
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (94.5, 3.5),
    )
    builder.UNBACKED_DRAWS.add("iron-plate")
    try:
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
            lambda _m: None,
        )
        assert "iron-plate" not in builder.UNBACKED_DRAWS
    finally:
        builder.UNBACKED_DRAWS.discard("iron-plate")


def test_direct_starter_is_not_cached_over_its_replacement_survey(monkeypatch) -> None:
    builder.MANAGED_INTERMEDIATE_SOURCES.pop("iron-plate", None)
    starter = builder.live_base.DirectPlateStarter((61.5, 24.5), "north", 1)
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter_site", lambda *_a, **_k: starter,
    )
    monkeypatch.setattr(
        builder, "_serve_direct_plate_starter",
        lambda *_a, **_k: (61.5, 18.5),
    )

    builder._bootstrap_direct_plate_line(
        object(), object(), "nauvis", "player", "iron-plate",
        (3.0, -1.0), lambda _m: None,
    )

    assert "iron-plate" not in builder.MANAGED_INTERMEDIATE_SOURCES


def test_bootstrap_upgrade_uses_the_managed_direct_refinery(monkeypatch) -> None:
    origin = (144, -72)
    existing = SimpleNamespace(
        machine_positions=[(145.5, -71.5), (145.5, -65.5)],
    )
    plan = SimpleNamespace(existing=existing)
    monkeypatch.setattr(builder, "logistic_smelter_origin", lambda *_a: origin)
    monkeypatch.setattr(
        builder.live_base, "entity_at",
        lambda _c, _s, position: (
            {"name": "requester-chest"}
            if position == (origin[0] + 1.5, origin[1] + 3.5) else None
        ),
    )
    calls: list[tuple] = []
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *args, **kwargs: calls.append((args, kwargs)) or (160.5, -50.5),
    )
    monkeypatch.setattr(
        builder, "build_conversion_stage",
        lambda *_a, **_k: pytest.fail("plate migration used generic conversion layout"),
    )

    result = builder._serve_healthy_line(
        object(), object(), "nauvis", "player", "copper-plate",
        (3.0, -1.0), lambda _m: None, plan, None,
        upgrade_bootstrap=True,
    )

    assert result is None
    assert calls[0][0][4] == "copper-plate"
    assert calls[0][1]["require_direct"] is True


def test_required_direct_upgrade_queues_shortage_instead_of_reusing_bootstrap(
    monkeypatch,
) -> None:
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    monkeypatch.setattr(
        builder, "_bootstrap_direct_plate_line",
        lambda *_a, **_k: pytest.fail("direct migration added another starter"),
    )

    with pytest.raises(MaterialShortage):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (3.0, -1.0), lambda _m: None, require_direct=True,
        )


def test_reuses_the_exact_direct_starter_instead_of_opening_another(monkeypatch) -> None:
    starter = builder.live_base.DirectPlateStarter(
        (61.5, 24.5), "north", 1,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter",
        lambda *_a, **_k: starter,
    )
    monkeypatch.setattr(
        builder.live_base, "direct_plate_starter_site",
        lambda *_a, **_k: pytest.fail("must not search beside a standing starter"),
    )
    served = []
    monkeypatch.setattr(
        builder, "_serve_direct_plate_starter",
        lambda *_a, **kwargs: served.append((_a[6], kwargs["submit"]))
        or (61.5, 18.5),
    )

    provider = builder._bootstrap_direct_plate_line(
        object(), object(), "nauvis", "player", "copper-plate",
        (3.0, -1.0), lambda _m: None,
    )

    assert provider == (61.5, 18.5)
    assert served == [(starter, False)]


def test_own_power_scaffolding_never_blocks_the_refinery_survey(monkeypatch) -> None:
    """Known planned scaffolding is excused, but same-force service entities
    without an exact persisted identity remain unknown infrastructure."""
    owners = {
        # Unmanaged: same force and prototype are not ownership.
        (11, 5): ("substation", 10.0, 4.0),
        (30, 30): ("substation", 31.0, 31.0), # someone else's pole body
    }
    monkeypatch.setattr(builder.live_base, "occupied_tile_owners",
                        lambda *_a, **_k: owners)
    monkeypatch.setattr(builder.live_base, "water_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(builder, "_planned_removal_tiles", lambda *_a: set())
    monkeypatch.setattr(builder, "planned_footprint_tiles",
                        lambda *_a: {(11, 5), (30, 30)})
    monkeypatch.setattr(builder, "_tile_bounds",
                        lambda *_a: ((0.0, 0.0), (99.0, 99.0)))

    class FakeClient:
        def command(self, *_a):
            return ""

    with pytest.raises(builder.StuckError, match=r"\[\(11, 5\), \(30, 30\)\]"):
        builder._plate_expansion_foundation(
            FakeClient(), "nauvis", "player", "iron-plate",
            SimpleNamespace(phases=[]), own_action_positions=set(),
        )


def test_starved_standing_refinery_grows_its_own_mine(monkeypatch) -> None:
    """User standard: keep the proper module and grow ITS OWN mine -- one more
    drill row behind the line -- instead of adding furnace capacity or opening
    another site. Run 15 built 24 stone furnaces fed for three."""
    extraction = SimpleNamespace(
        build_plan=None, drill_count=6, furnace_count=6, ore="stone",
        ore_output=(46.5, -65.5), smelter_origin=(78.0, -86.0),
        mining_productivity_bonus=0.3, smelter_flow_direction="east",
    )
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction",
                        lambda *_a, **_k: extraction)
    row_positions = tuple((float(i), 0.0) for i in range(6))
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a, **_k: SimpleNamespace(
            machine_count=6, working_count=1, machine_positions=row_positions,
        ),
    )
    monkeypatch.setattr(
        builder.live_base, "find_idle_machine_row",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "entity_statuses",
        lambda *_a, **_k: {pos: "no_ingredients" for pos in row_positions},
    )

    captured = {}

    def fake_expand(
        _c, _b, _s, _f, recipe, ref, emit, *, expand=False, **_kwargs,
    ):
        captured["expand"] = expand
        return (10.0, 20.0)

    # Bind the REAL entry point before patching: the starved guard recurses
    # through the module attribute, which the fake intercepts.
    real_build = builder.build_mining_stage
    monkeypatch.setattr(builder, "build_mining_stage", fake_expand)
    monkeypatch.setattr(
        builder, "_submit_mining_plan",
        lambda *_a: pytest.fail("must route through the expansion path"),
    )

    real_build(
        object(), object(), "nauvis", "player", "stone-brick", (3.0, -1.0),
        lambda _m: None,
    )

    assert captured["expand"] is True


def test_unproven_refinery_does_not_expand_its_mine(monkeypatch) -> None:
    """A 0/10 line that never made plates is still construction, not starvation."""
    extraction = SimpleNamespace(
        build_plan=None, drill_count=6, furnace_count=6, ore="copper-ore",
        ore_output=(113.5, -39.5), smelter_origin=(144.0, -72.0),
        mining_productivity_bonus=0.0, smelter_flow_direction="east",
    )
    positions = tuple((float(index), 0.0) for index in range(6))
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a, **_k: SimpleNamespace(
            machine_count=6, working_count=0, machine_positions=positions,
            produced_count=0,
        ),
    )
    monkeypatch.setattr(builder.live_base, "find_idle_machine_row", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder.live_base, "entity_statuses",
        lambda *_a, **_k: {position: "no_ingredients" for position in positions},
    )
    monkeypatch.setattr(
        builder, "_cohesive_smelter_target",
        lambda *_a: pytest.fail("startup must not become a mine-expansion request"),
    )

    with pytest.raises(builder.ProductionPrerequisiteDeferred, match="has not produced yet"):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "copper-plate", (3.0, -1.0),
            lambda _message: None,
        )


def test_unpowered_existing_mine_is_repaired_before_starvation_expansion(
    monkeypatch,
) -> None:
    extraction = SimpleNamespace(
        build_plan=None, expansion_positions=(), drill_count=6,
        row_drill_count=3, expansion_step=-1, shared_belt_y=-11.5,
        first_column_x=47.5, furnace_count=6, ore="iron-ore",
        ore_output=(83.5, -11.5), smelter_origin=(95.0, 15.0),
        mining_productivity_bonus=0.3, smelter_flow_direction="east",
    )
    furnace_positions = tuple((float(index), 0.0) for index in range(6))
    monkeypatch.setattr(builder.live_base, "available_items", lambda *_a: {})
    monkeypatch.setattr(builder, "plan_local_extraction", lambda *_a, **_k: extraction)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a, **_k: SimpleNamespace(
            machine_count=6, working_count=1, machine_positions=furnace_positions,
            produced_count=40,
        ),
    )
    monkeypatch.setattr(builder.live_base, "find_idle_machine_row", lambda *_a, **_k: None)

    def statuses(_client, _surface, positions):
        if tuple(positions) == furnace_positions:
            return {position: "no_ingredients" for position in furnace_positions}
        return {tuple(position): "no_power" for position in positions}

    monkeypatch.setattr(builder.live_base, "entity_statuses", statuses)
    serviced = []
    monkeypatch.setattr(
        builder, "_submit_mining_plan",
        lambda *_a, **_k: serviced.append(True),
    )
    real_build = builder.build_mining_stage
    monkeypatch.setattr(
        builder, "build_mining_stage",
        lambda *_a, **_k: pytest.fail("unpowered mine must not expand"),
    )

    with pytest.raises(
        builder.ProductionPrerequisiteDeferred,
        match="mine power was repaired",
    ):
        real_build(
            object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
            lambda _message: None,
        )

    assert serviced == [True]
