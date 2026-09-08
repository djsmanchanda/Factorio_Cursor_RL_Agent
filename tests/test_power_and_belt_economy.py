# Path: tests/test_power_and_belt_economy.py
# Purpose: User standards of 2026-08-22 -- belts go to the active blueprint
# before belt-consuming cells, stocked fast tiers substitute for scarce regular
# ones, a visibly-filling blueprint earns patience, and lean grids get solar.

from types import SimpleNamespace

import pytest

from orchestrator import autonomous_builder as builder


def _plan(belt: str = "transport-belt", count: int = 3) -> dict:
    return {"phases": [{"name": "p", "actions": [
        {"action_type": "place_ghost", "entity": belt, "position": {"x": i, "y": 0}}
        for i in range(count)
    ]}]}


# --- belt reserve floor ------------------------------------------------------

def test_underground_cell_defers_while_blueprint_holds_the_reserve(monkeypatch) -> None:
    # The live runner learns this recipe from the force catalog; mirror that
    # shape here.
    monkeypatch.setitem(
        builder.LINE_RECIPES, "underground-belt",
        {"ingredients": ["iron-plate", "transport-belt"],
         "amounts": [4, 2], "machine": "assembling-machine-1"},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"transport-belt": 10},
    )
    reason = builder._belt_starved_consumer(
        object(), "nauvis", "player", "underground-belt",
    )
    assert reason is not None and "transport-belt" in reason


def test_belt_consumer_resumes_once_stock_recovers(monkeypatch) -> None:
    monkeypatch.setitem(
        builder.LINE_RECIPES, "underground-belt",
        {"ingredients": ["iron-plate", "transport-belt"],
         "amounts": [4, 2], "machine": "assembling-machine-1"},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"transport-belt": 200},
    )
    assert builder._belt_starved_consumer(
        object(), "nauvis", "player", "underground-belt",
    ) is None


def test_belt_cell_itself_is_never_gated_by_the_reserve(monkeypatch) -> None:
    """The producer of belts must not be deferred on belts."""
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"transport-belt": 0},
    )
    assert builder._belt_starved_consumer(
        object(), "nauvis", "player", "transport-belt",
    ) is None


def test_stalled_belt_starter_releases_one_consumer_craft(monkeypatch) -> None:
    """A splitter must not wait for the 50-belt reserve before iron exists."""
    monkeypatch.setitem(
        builder.LINE_RECIPES, "splitter",
        {"ingredients": ["transport-belt"], "amounts": [4],
         "machine": "assembling-machine-1"},
    )
    monkeypatch.setitem(
        builder.LINE_RECIPES, "transport-belt",
        {"ingredients": ["iron-plate"], "amounts": [1],
         "machine": "assembling-machine-1"},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"transport-belt": 28},
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(working_count=0),
    )

    assert builder._belt_starved_consumer(
        object(), "nauvis", "player", "splitter",
    ) is None


def test_live_belt_output_keeps_the_blueprint_reserve(monkeypatch) -> None:
    monkeypatch.setitem(
        builder.LINE_RECIPES, "splitter",
        {"ingredients": ["transport-belt"], "amounts": [4],
         "machine": "assembling-machine-1"},
    )
    monkeypatch.setitem(
        builder.LINE_RECIPES, "transport-belt",
        {"ingredients": ["iron-plate"], "amounts": [1],
         "machine": "assembling-machine-1"},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"transport-belt": 28},
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(working_count=1),
    )

    assert builder._belt_starved_consumer(
        object(), "nauvis", "player", "splitter",
    ) is not None


def test_belt_batch_reserves_splitter_recipe_and_capped_machine_wip(
    monkeypatch,
) -> None:
    """Three splitters commit 12 belts plus two preloaded crafts (8 belts)."""
    monkeypatch.setitem(
        builder.LINE_RECIPES, "splitter",
        {
            "ingredients": ["iron-plate", "transport-belt"],
            "amounts": [5, 4],
            "product_amount": 1,
            "machine": "assembling-machine-1",
            "set_recipe": True,
        },
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )

    total, consumers = builder._queued_downstream_input_commitment(
        object(), "nauvis", "player", "transport-belt", {"splitter": 3},
    )

    assert total == 20
    assert consumers == (("splitter", 20),)


def test_mall_service_adds_downstream_draw_before_belt_spares(monkeypatch) -> None:
    """The run's 128-belt foundation is produced against a 148-belt bill."""
    monkeypatch.setitem(
        builder.LINE_RECIPES, "splitter",
        {
            "ingredients": ["transport-belt"],
            "amounts": [4],
            "product_amount": 1,
            "machine": "assembling-machine-1",
            "set_recipe": True,
        },
    )
    monkeypatch.setattr(
        builder, "_transferable_or_available_stock", lambda *_a: {},
    )
    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    ensured: list[tuple[str, int]] = []
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda _client, _bridge, _surface, _force, item, target, *_a,
        **_k: ensured.append((item, target)) or (True, None),
    )
    completed: list[str] = []
    priorities = SimpleNamespace(
        describe=lambda _task, _tick: "belt task",
        complete=lambda item, _tick: completed.append(item),
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 1)
    targets = {"transport-belt": 128, "splitter": 3}
    messages: list[str] = []

    builder._serve_mall_task(
        object(), object(), "nauvis", "player",
        SimpleNamespace(item="transport-belt", target=128), 0,
        targets, priorities, (0.0, 0.0), messages.append,
    )

    assert ensured == [("transport-belt", 148)]
    assert completed == ["transport-belt"]
    assert any("bill 128 + splitter=20" in message for message in messages)


def test_mall_task_defers_a_belt_starved_consumer(monkeypatch) -> None:
    deferred = []

    class FakePriorities:
        def describe(self, task, tick):
            return task.item

        def defer(self, item, tick, reason):
            deferred.append((item, reason))

    monkeypatch.setattr(
        builder, "_belt_starved_consumer",
        lambda *_a: "its recipe consumes transport-belt and only 10 remain",
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 1)
    ensure_calls: list = []
    monkeypatch.setattr(
        builder, "_ensure_mall_item",
        lambda *_a, **_k: ensure_calls.append("served") or (True, None),
    )

    builder._serve_mall_task(
        object(), object(), "nauvis", "player",
        SimpleNamespace(item="underground-belt", target=20), 1,
        {}, FakePriorities(), (3.0, -1.0), lambda _m: None,
    )

    assert deferred and deferred[0][0] == "underground-belt"
    assert not ensure_calls


def test_mall_keeps_stalled_producer_demand_until_it_makes_output(monkeypatch) -> None:
    events: list[tuple[str, str]] = []

    class FakePriorities:
        def describe(self, task, tick):
            return task.item

        def complete(self, item, tick):
            events.append(("complete", item))

        def defer(self, item, tick, reason):
            events.append(("defer", reason))

    monkeypatch.setattr(builder, "_belt_starved_consumer", lambda *_a: None)
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 1)
    monkeypatch.setattr(
        builder, "_ensure_mall_item", lambda *_a, **_k: (True, (1.0, 1.0)),
    )
    monkeypatch.setattr(
        builder, "construction_supply_chain_is_scheduled", lambda *_a: True,
    )
    monkeypatch.setattr(
        builder.live_base, "find_line",
        lambda *_a: SimpleNamespace(working_count=0, produced_count=0),
    )
    monkeypatch.setattr(builder, "_deliver_cell_ingredients", lambda *_a: False)

    targets = {"transport-belt": 132}
    builder._serve_mall_task(
        object(), object(), "nauvis", "player",
        SimpleNamespace(item="transport-belt", target=132), 1,
        targets, FakePriorities(), (3.0, -1.0), lambda _m: None,
    )

    assert targets == {"transport-belt": 132}
    assert events == [("defer", "producer exists but has not produced yet")]


# --- fast tier substitution --------------------------------------------------

def test_regular_belts_substitute_to_stocked_fast_tiers() -> None:
    plan = _plan("transport-belt", 3)
    swapped = builder._prefer_stocked_belt_tiers(
        plan, {"fast-transport-belt": 100},
    )
    assert swapped == 3
    actions = plan["phases"][0]["actions"]
    assert all(a["entity"] == "fast-transport-belt" for a in actions)


def test_partial_fast_coverage_upgrades_what_it_can() -> None:
    """Bidirectional economy: cover actions with stocked surplus where it
    exists -- a buildable mixed-tier plan beats an unaffordable pure one."""
    plan = _plan("transport-belt", 5)
    swapped = builder._prefer_stocked_belt_tiers(
        plan, {"fast-transport-belt": 4},
    )
    assert swapped == 4
    tiers = [a["entity"] for a in plan["phases"][0]["actions"]]
    assert tiers.count("fast-transport-belt") == 4
    assert tiers.count("transport-belt") == 1


def test_fast_shortfall_downgrades_to_covering_regular() -> None:
    """Run 11: the landfill blueprint demanded 16 fast belts the gate would
    not produce, while regular belts sat plentiful -- the plan follows
    inventory, not the reverse."""
    plan = _plan("fast-transport-belt", 16)
    swapped = builder._prefer_stocked_belt_tiers(
        plan, {"transport-belt": 86, "fast-transport-belt": 9},
    )
    assert swapped == 7
    tiers = [a["entity"] for a in plan["phases"][0]["actions"]]
    assert tiers.count("transport-belt") == 7
    assert tiers.count("fast-transport-belt") == 9


def test_plans_without_belts_are_untouched() -> None:
    plan = {"phases": [{"name": "p", "actions": [
        {"action_type": "place_entity", "entity": "electric-mining-drill",
         "position": {"x": 0, "y": 0}},
    ]}]}
    assert builder._prefer_stocked_belt_tiers(plan, {}) == 0


# --- patience while a blueprint fills ----------------------------------------

def test_visibly_filling_blueprints_extend_remediation(monkeypatch) -> None:
    """The landfill run died at '6 rounds (180s)' while bots were mid-build;
    a falling ghost count is progress. Live run 30 (2026-08-22) then showed
    one extension is not enough for a ~250-tile pipeline built at cross-base
    flight speed, so extensions now repeat while local ghosts keep falling."""
    monkeypatch.setattr(
        builder, "extend_roboport_coverage", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "ensure_logistic_coverage", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "_apply_remedy", lambda *_a: True)
    ghosts = iter([5, 4])
    monkeypatch.setattr(
        builder, "_wait_for_ghosts",
        lambda *_a, **_k: next(ghosts, 4),
    )
    monkeypatch.setattr(
        builder, "_diagnose_blockage",
        lambda *_a, **_k: ("ghost needs material", "materials:x"),
    )

    with pytest.raises(builder.StuckError):
        builder.bring_stage_up(
            object(), object(), "nauvis", "player", "landfill system",
            (0.0, 0.0), ((-10, -10), (10, 10)), (0.0, 0.0),
            [], lambda _m: None,
            rounds=2, interval=0.0,
        )

# --- deterministic power district --------------------------------------------

def test_healthy_network_skips_the_top_up(monkeypatch, tmp_path) -> None:
    """A converged usable-capacity calculation is the gate."""
    monkeypatch.setattr(
        builder.live_base, "network_firm_generation_kw", lambda *_a: 5000.0,
    )
    monkeypatch.setattr(
        builder.live_base, "network_generation_kw", lambda *_a: 5000.0,
    )
    monkeypatch.setattr(
        builder.live_base, "network_accumulator_storage_mj", lambda *_a: 0.0,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"solar-panel": 20, "accumulator": 20, "substation": 5},
    )
    import orchestrator.power_district as power
    monkeypatch.setattr(
            power, "network_peak_consumption_kw", lambda *_a, **_k: 1000.0,
        )
    monkeypatch.setattr(power, "_has_built", lambda *_a: True)
    monkeypatch.setattr(builder.live_base, "area_entity_records", lambda *_a, **_k: [])
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a: pytest.fail("healthy grids must not be built upon"),
    )
    monkeypatch.setattr(builder, "extend_power", lambda *_a, **_k: False)
    assert builder._top_up_solar_generation(
        object(), SimpleNamespace(script_output=tmp_path), "nauvis",
        "player", (0.0, 0.0), lambda _m: None,
    ) is False


def test_missing_full_unit_materials_do_not_submit_a_partial_unit(
    monkeypatch, tmp_path,
) -> None:
    monkeypatch.setattr(
        builder.live_base, "network_firm_generation_kw", lambda *_a: 500.0,
    )
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {},
    )
    monkeypatch.setattr(
        builder.live_base, "network_generation_kw", lambda *_a: 650.0,
    )
    monkeypatch.setattr(
        builder.live_base, "network_accumulator_storage_mj", lambda *_a: 0.0,
    )
    monkeypatch.setattr(
        builder.live_base, "area_entity_records", lambda *_a, **_k: [],
    )
    monkeypatch.setattr(builder.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(builder.live_base, "deconstruction_tiles", lambda *_a: set())
    import orchestrator.power_district as power
    monkeypatch.setattr(
            power, "network_peak_consumption_kw", lambda *_a, **_k: 1000.0,
        )
    monkeypatch.setattr(power, "_has_built", lambda *_a: False)
    messages: list[str] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda *_a: pytest.fail("partial or unfunded power units are forbidden"),
    )
    monkeypatch.setattr(builder, "extend_power", lambda *_a, **_k: False)
    assert builder._top_up_solar_generation(
        object(), SimpleNamespace(script_output=tmp_path), "nauvis", "player",
        (0.0, 0.0),
        messages.append,
    ) is False
    assert any("full unit materials" in message for message in messages)


def test_power_sizing_joins_the_primary_grid_before_building_more_panels(monkeypatch, tmp_path) -> None:
    """A 167 kW local island must not hide the supplied 10 MW base grid."""
    calls = []
    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: calls.append(_a[4]) or True,
    )
    monkeypatch.setattr(
        builder, "ensure_power_capacity",
        lambda *_a, **_k: pytest.fail("do not size a new district before joining the grid"),
    )

    assert builder._top_up_solar_generation(
        object(), SimpleNamespace(script_output=tmp_path), "nauvis", "player",
        (3.0, -1.0), lambda _message: None,
    )
    assert calls == [(3.0, -1.0)]


def test_power_sizing_does_not_treat_existing_coverage_as_a_grid_join(
    monkeypatch, tmp_path,
) -> None:
    """Coverage by a pole is not proof that the remote grid was bridged."""
    from orchestrator.stage_services import PowerExtensionResult

    monkeypatch.setattr(
        builder, "extend_power",
        lambda *_a, **_k: PowerExtensionResult(ready=True, changed=False),
    )
    monkeypatch.setattr(
        builder.live_base, "nearest_powered_pole",
        lambda *_a, **_k: ((90.0, 40.0), "substation"),
    )
    sized = []
    monkeypatch.setattr(
        builder, "ensure_power_capacity",
        lambda **_k: sized.append(_k["near"]) or False,
    )

    assert builder._top_up_solar_generation(
        object(), SimpleNamespace(script_output=tmp_path), "nauvis", "player",
        (3.0, -1.0), lambda _message: None,
    ) is False
    assert sized == [(90.0, 40.0)]


def test_submit_does_not_adopt_an_unowned_same_force_belt(monkeypatch) -> None:
    """Force and prototype family do not establish planner ownership."""
    from types import SimpleNamespace

    import orchestrator.stage_services as ss

    plan = {"phases": [{
        "actions": [
            {"action_type": "place_ghost", "entity": "underground-belt",
             "position": {"x": 51.5, "y": -104.5}},
        ],
    }], "force": "player"}
    reports = iter([{
        "ok": False,
        "failed_placements": 1,
        "placement_failures": [{
            "reason": "exact_position_occupied_by_different_entity",
            "position": {"x": 51.5, "y": -104.5},
        }],
    }])
    built = []

    class FakeBridge:
        def build_layout(self, authorization, plan_dict):
            built.append(plan_dict)
            return next(reports)

    monkeypatch.setattr(ss, "load_json", lambda r: r)
    monkeypatch.setattr(ss, "clear_plan_clutter", lambda *_a: None)
    monkeypatch.setattr(ss, "assert_affordable", lambda *_a: None)
    monkeypatch.setattr(ss, "build_layout_authorization", lambda *_a: object())
    monkeypatch.setattr(
        ss.live_base, "entity_at",
        lambda _c, _s, pos: {
            "name": "transport-belt", "type": "transport-belt", "force": "player",
        }
        if (pos[0], pos[1]) == (51.5, -104.5) else None,
    )

    with pytest.raises(ss.StuckError, match="blocked by real infrastructure"):
        ss._submit(
            object(), FakeBridge(), "nauvis", plan, "landfill bridge",
            lambda _m: None,
        )

    assert len(built) == 1
    assert plan["phases"][0]["actions"]


def test_lone_undiagnosed_ghost_gets_one_rebuild_cycle(monkeypatch) -> None:
    """Run 13's end: a single pole ghost sat unbuilt for 360s while every
    check looked healthy -- diagnosis had no name for it, so the stage died.
    One remove-and-resubmit cycle is the honest remedy."""
    waits = iter([1, 1, 0])
    monkeypatch.setattr(
        builder, "_wait_for_ghosts", lambda *_a, **_k: next(waits),
    )
    monkeypatch.setattr(builder, "extend_roboport_coverage",
                        lambda *_a, **_k: False)
    monkeypatch.setattr(builder, "ensure_logistic_coverage",
                        lambda *_a, **_k: None)
    monkeypatch.setattr(builder.live_base, "pending_ghost_count",
                        lambda *_a, **_k: 1)

    def fake_diagnose(*_a, **_k):
        # None means 'no blockage found -- bots still working'
        return None

    monkeypatch.setattr(builder, "_diagnose_blockage", fake_diagnose)
    monkeypatch.setattr(
        builder.live_base, "ghost_blockages",
        lambda *_a, **_k: [{
            "position": (5.5, 22.5), "entity": "medium-electric-pole",
            "reason": "pending",
        }],
    )
    removed: list = []
    monkeypatch.setattr(
        builder.live_base, "remove_ghost_at",
        lambda _c, _s, _f, _e, pos: (
            removed.append(pos)
            or builder.live_base.GhostRemovalResult(found=True, cleared=True)
        ),
    )
    submitted: list[str] = []
    monkeypatch.setattr(
        builder, "_submit",
        lambda _c, _b, _s, plan, name, _e: (
            submitted.append(name) or {"succeeded_placements": 1}
        ),
    )

    builder.bring_stage_up(
        object(), object(), "nauvis", "player", "steel-plate conversion",
        (5.5, 29.5), ((-10, -10), (20, 40)), (0.0, 0.0),
        [(5.5, 30.5)], lambda _m: None,
        rounds=2, interval=0.0,
    )

    assert removed == [(5.5, 22.5)]
    assert submitted == ["rebuild_stale_ghost"]


def test_lone_ghost_rebuild_reports_a_typed_zero_placement_failure(
    monkeypatch,
) -> None:
    ghost = {
        "position": (39.0, 36.0),
        "entity": "medium-electric-pole",
        "reason": "pending",
    }
    monkeypatch.setattr(
        builder.live_base, "ghost_blockages", lambda *_a, **_k: [ghost],
    )
    monkeypatch.setattr(
        builder.live_base, "remove_ghost_at", lambda *_a, **_k: (
            builder.live_base.GhostRemovalResult(found=True, cleared=True)
        ),
    )
    monkeypatch.setattr(
        builder, "_submit", lambda *_a, **_k: {
            "attempted_placements": 1,
            "succeeded_placements": 0,
            "already_present_placements": 1,
            "failed_placements": 0,
            "placement_failures": [],
        },
    )
    messages: list[str] = []

    with pytest.raises(builder.StuckError) as raised:
        builder._rebuild_stale_ghost(
            object(), object(), "nauvis", "player",
            ((33.0, 30.0), (47.0, 38.0)), messages.append,
        )

    assert raised.value.code == "stale_ghost_rebuild_failed"
    assert raised.value.details == {
        "ghosts": [{
            "entity": "medium-electric-pole",
            "position": [39.0, 36.0],
            "reason": "pending",
        }],
        "attempted_placements": 1,
        "succeeded_placements": 0,
        "already_present_placements": 1,
        "failed_placements": 0,
        "placement_failures": [],
    }
    assert not any("STALE GHOST: rebuilt" in message for message in messages)


def test_stale_rebuild_stops_before_submit_when_exact_removal_does_not_clear(
    monkeypatch,
) -> None:
    ghost = {
        "position": (36.5, 32.5),
        "entity": "assembling-machine-1",
        "reason": "pending",
    }
    monkeypatch.setattr(
        builder.live_base, "ghost_blockages", lambda *_a, **_k: [ghost],
    )
    monkeypatch.setattr(
        builder.live_base, "remove_ghost_at", lambda *_a, **_k: (
            builder.live_base.GhostRemovalResult(found=True, cleared=False)
        ),
    )
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_submit", lambda *_a, **_k: submitted.append({}),
    )

    with pytest.raises(builder.StuckError) as raised:
        builder._rebuild_stale_ghost(
            object(), object(), "nauvis", "player",
            ((33.0, 30.0), (47.0, 38.0)), lambda _message: None,
        )

    assert raised.value.code == "stale_ghost_removal_failed"
    assert raised.value.details == {
        "entity": "assembling-machine-1",
        "position": [36.5, 32.5],
        "reason": "pending",
        "found": True,
        "cleared": False,
    }
    assert submitted == []


def test_exact_ghost_removal_is_entity_and_force_scoped() -> None:
    class Client:
        def __init__(self) -> None:
            self.commands: list[str] = []

        def command(self, command: str) -> str:
            self.commands.append(command)
            return "REMOVED"

    client = Client()

    result = builder.live_base.remove_ghost_at(
        client, "nauvis", "player", "assembling-machine-1", (36.5, 32.5),
    )

    assert result == builder.live_base.GhostRemovalResult(
        found=True, cleared=True,
    )
    lua = client.commands[0]
    assert "type='entity-ghost'" in lua
    assert "ghost_name='assembling-machine-1'" in lua
    assert "force=f" in lua
    assert "if target() then rcon.print('STILL_PRESENT')" in lua


def test_rebuilt_ghost_that_remains_pending_gets_a_typed_construction_failure(
    monkeypatch,
) -> None:
    ghost = {
        "position": (36.5, 32.5),
        "entity": "assembling-machine-1",
        "reason": "no_available_construction_robots",
        "network_id": 2,
        "construction_robots": 50,
        "available_construction_robots": 0,
        "item": "assembling-machine-1",
        "required": 1,
        "network_item_count": 4,
    }
    waits = iter([1, 1])
    monkeypatch.setattr(
        builder, "_wait_for_ghosts", lambda *_a, **_k: next(waits),
    )
    monkeypatch.setattr(
        builder, "extend_roboport_coverage", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "ensure_logistic_coverage", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(builder, "_diagnose_blockage", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder, "_rebuild_stale_ghost", lambda *_a, **_k: True,
    )
    monkeypatch.setattr(
        builder.live_base, "ghost_blockages", lambda *_a, **_k: [ghost],
    )

    with pytest.raises(builder.StuckError) as raised:
        builder.bring_stage_up(
            object(), object(), "nauvis", "player",
            "compact mall for iron-gear-wheel", (35.0, 31.0),
            ((33.0, 30.0), (47.0, 38.0)), (39.0, 36.0),
            [(36.5, 32.5)], lambda _message: None,
            rounds=1, interval=30.0,
        )

    assert raised.value.code == "stale_ghost_construction_failed"
    assert raised.value.state == "constructing"
    assert raised.value.details == {
        "ghosts": [{
            "entity": "assembling-machine-1",
            "position": [36.5, 32.5],
            "reason": "no_available_construction_robots",
            "network_id": 2,
            "construction_robots": 50,
            "available_construction_robots": 0,
            "item": "assembling-machine-1",
            "required": 1,
            "network_item_count": 4,
        }],
        "remaining": 1,
        "rounds": 2,
        "seconds": 60.0,
    }


def test_run_loop_checks_generation_proactively(monkeypatch) -> None:
    """Live run 36 (2026-08-23): every solar top-up trigger was a power-bridge
    event, and no bridge came while the grid browned out -- the run burned its
    iteration budget in the dark. The main loop must re-check the grid on a
    bounded interval instead of only after bridges."""
    import inspect

    source = inspect.getsource(builder.run)
    assert "_top_up_solar_generation" in source
    assert "_GENERATION_CHECK_INTERVAL_TICKS" in source


def test_belt_recovery_is_queued_with_the_deferral(monkeypatch) -> None:
    """2026-09-04: splitter waited 243s on belts at 26 while nothing was
    tasked with making belts. The deferral now queues belt recovery."""
    from orchestrator import priority_list
    monkeypatch.setitem(
        builder.LINE_RECIPES, "splitter",
        {"ingredients": ["transport-belt"], "amounts": [4],
         "machine": "assembling-machine-1"},
    )
    monkeypatch.setattr(
        builder.live_base, "available_items",
        lambda *_a: {"transport-belt": 26},
    )
    monkeypatch.setattr(builder.live_base, "game_tick", lambda *_a: 1000)
    monkeypatch.setattr(builder.live_base, "find_line", lambda *_a, **_k: None)
    priorities = priority_list.PriorityList.__new__(priority_list.PriorityList)
    priorities.items = {}
    promoted: list[str] = []
    monkeypatch.setattr(
        priorities, "promote",
        lambda item, target, tick: promoted.append(item),
    )
    monkeypatch.setattr(
        priorities, "defer", lambda item, tick, reason: None,
    )
    mall_targets: dict[str, int] = {"splitter": 50}
    messages: list[str] = []
    task = type("Task", (), {"item": "splitter", "target": 50})()

    builder._serve_mall_task(
        object(), object(), "nauvis", "player", task, 1000, mall_targets,
        priorities, (0.0, 0.0), messages.append,
    )

    assert mall_targets.get("transport-belt", 0) >= 50
    assert promoted == ["transport-belt"]
    assert any("BELT RECOVERY" in message for message in messages)
