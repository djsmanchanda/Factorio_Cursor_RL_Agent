# Path: tests/test_plate_bootstrap_circle.py
# Purpose: The first plate system must bootstrap plates without belts instead of
# demanding the belts its own plates would have to build (live livelock
# 2026-08-21), and every later refinement stays honest to live evidence.

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
    monkeypatch.setattr(
        builder.live_base, "bootstrap_cell_origins", lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        builder.live_base, "blocked_drill_drop_tile", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "intake_candidate_tiles",
        lambda *_a, **_k: [(78.0, -41.5)],
    )
    monkeypatch.setattr(
        builder, "_mine_logistic_intake",
        lambda *_a, **_k: calls.append("intake") or (78.0, -41.5),
    )
    return calls


def test_cold_belt_shortage_bootstraps_a_logistic_smelter(monkeypatch) -> None:
    extraction = _extraction()
    calls = _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    built = []

    def fake_smelter(*_a, **_k):
        built.append(True)
        return (7.0, 31.0)

    monkeypatch.setattr(builder, "build_logistic_smelter", fake_smelter)

    provider = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
        lambda _m: None,
    )

    assert provider == (7.0, 31.0)
    # The mine still builds this pass; the intake then the cell follow it.
    assert calls == ["mine", "intake"]
    assert built == [True]


def test_the_same_shortage_never_reaches_the_mall_as_belt_demand(monkeypatch) -> None:
    """THE BUG: MaterialShortage(initial_iron-plate_system, transport-belt=53)
    escaped into _ensure_mall_item and re-queued 53 belts every pass while the
    belt recipe itself waited on this very plate."""
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    monkeypatch.setattr(
        builder, "build_logistic_smelter", lambda *_a, **_k: (7.0, 31.0),
    )
    queued = []
    monkeypatch.setattr(
        builder, "add_demands",
        lambda t, s: queued.append(dict(s.required)), raising=False,
    )

    builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
        lambda _m: None,
    )

    assert queued == []


def test_intake_is_built_before_the_cell_and_feeds_it(monkeypatch) -> None:
    """THE LIVE FAULT 2026-08-22: the cell's chest held a correct copper-ore
    request while the mine belt ended in open air -- bots had nothing to draw
    from, so both furnaces sat at no_ingredients. The intake must exist first,
    and the cell's coverage check must name the intake CHEST, never the belt
    tile."""
    extraction = _extraction()
    calls = _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    seen = {}

    def fake_smelter(_c, _b, _s, _f, recipe, ore, origin, ore_output, emit,
                     *, ore_pickup=None):
        seen["pickup"] = ore_pickup
        return (7.0, 31.0)

    monkeypatch.setattr(builder, "build_logistic_smelter", fake_smelter)

    builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
        lambda _m: None,
    )

    assert calls == ["mine", "intake"]
    assert seen["pickup"] == (78.0, -41.5)


def test_an_existing_plate_line_still_propagates_the_shortage(monkeypatch) -> None:
    """The fallback is for a COLD base only; a growing base keeps its normal
    shortage handling so expansion demand stays visible to the mall."""
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a, **_k: SimpleNamespace(machine_count=4),
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    monkeypatch.setattr(
        builder, "build_logistic_smelter",
        lambda *_a, **_k: pytest.fail("must not bootstrap over a live line"),
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
        builder, "build_logistic_smelter",
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


def test_bootstrap_cell_is_not_cached_over_its_upgrade_survey(monkeypatch) -> None:
    """Caching the temp cell in MANAGED_INTERMEDIATE_SOURCES would hide it from
    the BOOTSTRAP UPGRADE that replaces it with a belt-fed refinery."""
    extraction = _extraction()
    builder.MANAGED_INTERMEDIATE_SOURCES.pop("iron-plate", None)
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    monkeypatch.setattr(
        builder, "build_logistic_smelter", lambda *_a, **_k: (7.0, 31.0),
    )

    builder._bootstrap_logistic_plate_line(
        object(), object(), "nauvis", "player", "iron-plate", extraction,
        lambda _m: None,
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
        builder, "_bootstrap_logistic_plate_line",
        lambda *_a, **_k: pytest.fail("direct migration fell back to requester cell"),
    )

    with pytest.raises(MaterialShortage):
        builder.build_mining_stage(
            object(), object(), "nauvis", "player", "iron-plate",
            (3.0, -1.0), lambda _m: None, require_direct=True,
        )


def test_reuses_a_standing_bootstrap_cell_instead_of_opening_another(monkeypatch) -> None:
    """Run 4 pass 2 re-surveyed a different origin, orphaned the first cell,
    and the second cell's health failure escaped and ended the run. Run 8
    rebuilt beside a starving twin because recipe-less furnaces are invisible
    to machine surveys -- the cell's REQUESTER is its identity."""
    extraction = _extraction()
    _wire_cold_base(monkeypatch, extraction)
    monkeypatch.setattr(
        builder.live_base, "bootstrap_cell_origins",
        lambda *_a, **_k: [(11.5, 52.5)],
    )
    monkeypatch.setattr(
        builder, "_build_initial_plate_smelter",
        lambda *_a, **_k: (_ for _ in ()).throw(_short_belts()),
    )
    monkeypatch.setattr(
        builder, "build_logistic_smelter",
        lambda *_a, **_k: pytest.fail("must not rebuild beside a standing cell"),
    )
    served = {}

    def fake_serve(_c, _b, _s, _f, recipe, ore, origin, pickup, emit):
        served["origin"] = origin
        return (12.0, 58.0)

    monkeypatch.setattr(builder, "_serve_bootstrap_cell", fake_serve)

    provider = builder.build_mining_stage(
        object(), object(), "nauvis", "player", "iron-plate", (3.0, -1.0),
        lambda _m: None,
    )

    assert provider == (12.0, 58.0)
    assert served["origin"] == (10, 49)


def test_intake_recognizes_an_existing_pair_without_submitting(monkeypatch) -> None:
    """A cell from an earlier pass already has its intake: return its chest
    instead of submitting a duplicate beside the belt end."""
    monkeypatch.setattr(
        builder.live_base, "blocked_drill_drop_tile", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "intake_candidate_tiles",
        lambda *_a, **_k: [(77.5, -39.5)],
    )
    monkeypatch.setattr(
        builder.live_base, "entity_status_name",
        lambda *_a, **_k: "working",
    )
    monkeypatch.setattr(
        builder.live_base, "chest_has_items", lambda *_a: True,
    )

    def entity_at(_c, _s, position):
        if position == (77.5, -40.5):
            return {"name": "fast-inserter", "type": "inserter"}
        if position == (77.5, -41.5):
            return {"name": "passive-provider-chest"}
        return None

    monkeypatch.setattr(builder.live_base, "entity_at", entity_at)
    submits: list = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: submits.append(name),
    )

    chest = builder._mine_logistic_intake(
        object(), object(), "nauvis", "player", "iron-ore",
        (77.5, -39.5), lambda _m: None,
    )

    assert chest == (77.5, -41.5)
    assert submits == []


def test_blocked_drill_drop_tile_becomes_the_intake_chest(monkeypatch) -> None:
    """Run 9: modular rows wedge every belt-side tile between drill bodies --
    no inserter placement exists. A blocked drill's bare drop tile takes a
    provider chest with zero demolition and becomes the ore interface."""
    monkeypatch.setattr(
        builder.live_base, "blocked_drill_drop_tile",
        lambda *_a, **_k: (22.5, -1.5),
    )
    monkeypatch.setattr(builder.live_base, "entity_at", lambda *_a: None)
    submitted: list[tuple[str, list]] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e:
            submitted.append((name, plan["phases"][0]["actions"])),
    )

    chest = builder._mine_logistic_intake(
        object(), object(), "nauvis", "player", "iron-ore",
        (12.5, -1.5), lambda _m: None,
    )

    assert chest == (22.5, -1.5)
    assert submitted[0][0] == "mine_iron-ore_drop_chest"
    assert submitted[0][1] == [{
        "action_type": "place_entity", "entity": "passive-provider-chest",
        "position": {"x": 22.5, "y": -1.5},
    }]


def test_intake_places_past_ore_ground_at_the_haul_head(monkeypatch) -> None:
    """Live run of 2026-08-24 06:56: the copper patch continues east past the
    collector head, so the intake spots east of the head are resource tiles.
    entity_at reports them, but ore ground is buildable -- the intake must
    place there instead of declaring the row wedged."""
    monkeypatch.setattr(
        builder.live_base, "blocked_drill_drop_tile", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "intake_candidate_tiles",
        lambda *_a, **_k: [(87.5, -39.5), (89.5, -39.5)],
    )

    def entity_at(_c, _s, position):
        x, y = position
        if abs(y - (-40.5)) < 0.6:  # north of the row: upper drill bodies
            return {"name": "electric-mining-drill", "type": "mining-drill"}
        if abs(y - (-38.5)) < 0.6:  # south of the row: lower drill bodies
            return {"name": "electric-mining-drill", "type": "mining-drill"}
        if x in (86.5, 87.5, 88.5):
            return {"name": "fast-transport-belt", "type": "transport-belt"}
        if x in (90.5, 91.5) and abs(y - (-39.5)) < 0.6:
            return {"name": "copper-ore", "type": "resource"}
        return None

    monkeypatch.setattr(builder.live_base, "entity_at", entity_at)
    powered: list = []
    monkeypatch.setattr(
        builder, "extend_power", lambda _c, _b, _s, _f, spot, _e: powered.append(spot),
    )
    submits: list = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: submits.append(name),
    )

    chest = builder._mine_logistic_intake(
        object(), object(), "nauvis", "player", "copper-ore",
        (89.5, -39.5), lambda _m: None,
    )

    assert chest == (91.5, -39.5)
    assert submits == ["mine_copper-ore_intake"]
    assert powered == [(90.5, -39.5)]


def test_cell_health_window_scales_with_bot_flight(monkeypatch) -> None:
    """The default 20 s condemned two correctly-built furnaces while bots were
    still flying ore across ~40 tiles; the window now follows the distance."""
    assert builder._bot_delivery_grace((90.0, -40.0), (80.0, -10.0)) > 90
    assert builder._bot_delivery_grace((82.0, -12.0), (80.0, -11.0)) <= 300

    captured = {}
    monkeypatch.setattr(builder, "bring_stage_up", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_diagnose_machines",
        lambda *_a, **kwargs: captured.update(kwargs) or [],
    )

    builder._serve_bootstrap_cell(
        object(), object(), "nauvis", "player", "iron-plate", "iron-ore",
        (80, -11), (90.0, -40.0), lambda _m: None,
    )

    assert captured["grace_seconds"] == builder._bot_delivery_grace(
        (90.0, -40.0), (80, -11),
    )


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

    def fake_expand(_c, _b, _s, _f, recipe, ref, emit, *, expand=False):
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


def test_intake_inserter_gets_power_extended(monkeypatch) -> None:
    """Live run of 2026-08-24 14:05: the intake landed past the row's power
    scaffold, so its inserter never ran, the chest stayed empty, and both
    temporary furnaces starved on ingredients for 300s. The intake must chain
    itself onto a powered network when it is placed."""
    monkeypatch.setattr(
        builder.live_base, "blocked_drill_drop_tile", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "intake_candidate_tiles",
        lambda *_a, **_k: [(89.5, -39.5)],
    )

    def entity_at(_c, _s, position):
        x, y = position
        if abs(y - (-40.5)) < 0.6 or abs(y - (-38.5)) < 0.6:
            return {"name": "electric-mining-drill", "type": "mining-drill"}
        if position == (88.5, -39.5):
            return {"name": "fast-transport-belt", "type": "transport-belt"}
        return None

    monkeypatch.setattr(builder.live_base, "entity_at", entity_at)
    powered: list = []
    monkeypatch.setattr(
        builder, "extend_power", lambda _c, _b, _s, _f, spot, _e: powered.append(spot),
    )
    submits: list = []
    monkeypatch.setattr(
        builder, "_submit", lambda _c, _b, _s, _plan, name, _e: submits.append(name),
    )

    chest = builder._mine_logistic_intake(
        object(), object(), "nauvis", "player", "copper-ore",
        (89.5, -39.5), lambda _m: None,
    )

    assert chest == (91.5, -39.5)
    assert submits == ["mine_copper-ore_intake"]
    assert powered == [(90.5, -39.5)]
