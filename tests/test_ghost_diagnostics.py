# Path: tests/test_ghost_diagnostics.py
# Purpose: Prove stalled construction ghosts report actionable live causes and material demand.

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import autonomous_builder as builder  # noqa: E402
from orchestrator import live_base  # noqa: E402
from orchestrator import stage_services  # noqa: E402
from orchestrator.parts_mall import MaterialShortage  # noqa: E402


class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.commands: list[str] = []

    def command(self, command: str) -> str:
        self.commands.append(command)
        return self.reply


@pytest.fixture(autouse=True)
def _clear_pending_power_bridges() -> None:
    stage_services._PENDING_POWER_BRIDGES.clear()
    builder._STAGE_DELIVERY_PROVIDERS.clear()
    yield
    stage_services._PENDING_POWER_BRIDGES.clear()
    builder._STAGE_DELIVERY_PROVIDERS.clear()


def test_construction_polling_spends_one_wait_budget_per_window(monkeypatch) -> None:
    readings = iter((3, 2, 1, 0))
    client = type("Client", (), {
        "command": lambda _self, _command: str(next(readings)),
    })()
    waits: list[str] = []
    monkeypatch.setattr(stage_services, "consume_wait", waits.append)
    monkeypatch.setattr(stage_services.time, "sleep", lambda _seconds: None)

    remaining = stage_services._wait_for_ghosts(
        client, "nauvis", "player", ((0.0, 0.0), (10.0, 10.0)),
        timeout_seconds=30.0,
    )

    assert remaining == 0
    assert waits == ["ghost_construction"]


def test_coherent_earmark_submits_ghosts_despite_queued_shortage(monkeypatch) -> None:
    plan = {
        "force": "player",
        "phases": [{"actions": [{
            "action_type": "place_ghost", "entity": "electric-mining-drill",
            "position": {"x": 10.5, "y": 20.5},
        }]}],
    }
    reports = []
    bridge = type("Bridge", (), {
        "build_layout": lambda _self, _authorization, _plan: (
            reports.append(_plan) or {
                "ok": True, "attempted_placements": 1,
                "succeeded_placements": 1, "placed_ghosts": 1,
                "placed_entities": 0,
            }
        ),
    })()
    monkeypatch.setattr(stage_services, "consume_plan_submission", lambda *_a: None)
    monkeypatch.setattr(stage_services, "clear_plan_clutter", lambda *_a: None)
    monkeypatch.setattr(stage_services, "load_json", lambda report: report)
    monkeypatch.setattr(
        stage_services, "assert_affordable",
        lambda *_a: (_ for _ in ()).throw(MaterialShortage(
            "iron-expansion", {"electric-mining-drill": 6}, {},
        )),
    )
    monkeypatch.setattr(
        stage_services, "_shortage_has_complete_supply_chains",
        lambda *_a: True,
    )

    stage_services._submit(
        object(), bridge, "nauvis", plan, "iron-expansion",
        lambda _message: None, allow_unfunded_ghosts=True,
    )

    assert reports == [plan]


def test_configuration_only_plan_is_not_logged_as_zero_placement_churn(
    monkeypatch,
) -> None:
    plan = {"force": "player", "phases": [{"actions": [{
        "action_type": "configure_entity", "entity": "requester-chest",
        "position": {"x": 10.5, "y": 20.5}, "clear_logistic_groups": ["old"],
    }]}]}
    bridge = type("Bridge", (), {
        "build_layout": lambda *_a: {
            "ok": True, "attempted_placements": 0,
            "succeeded_placements": 0, "placed_ghosts": 0,
            "placed_entities": 0,
        },
    })()
    monkeypatch.setattr(stage_services, "consume_plan_submission", lambda *_a: None)
    monkeypatch.setattr(stage_services, "clear_plan_clutter", lambda *_a: None)
    monkeypatch.setattr(stage_services, "assert_affordable", lambda *_a: None)
    monkeypatch.setattr(stage_services, "load_json", lambda report: report)
    messages = []

    stage_services._submit(
        object(), bridge, "nauvis", plan, "request_update", messages.append,
    )

    assert messages == [
        "request_update: applied 1 configuration action(s); no placements required"
    ]


def test_explicit_earmark_cannot_bypass_a_missing_supply_chain(monkeypatch) -> None:
    plan = {"force": "player", "phases": [{"actions": [{
        "action_type": "place_ghost", "entity": "pipe",
        "position": {"x": 10.5, "y": 20.5},
    }]}]}
    monkeypatch.setattr(stage_services, "consume_plan_submission", lambda *_a: None)
    monkeypatch.setattr(stage_services, "clear_plan_clutter", lambda *_a: None)
    monkeypatch.setattr(
        stage_services, "assert_affordable",
        lambda *_a: (_ for _ in ()).throw(MaterialShortage(
            "oil-cell", {"pipe": 500}, {},
        )),
    )
    monkeypatch.setattr(
        stage_services, "_shortage_has_complete_supply_chains",
        lambda *_a: False,
    )

    with pytest.raises(MaterialShortage):
        stage_services._submit(
            object(), object(), "nauvis", plan, "oil-cell",
            lambda _message: None, allow_unfunded_ghosts=True,
        )


def test_unfunded_blueprint_requires_the_complete_solid_supply_chain(monkeypatch) -> None:
    lines = []
    monkeypatch.setattr(
        stage_services.live_base, "find_line",
        lambda _c, _s, _f, recipe, _machine: lines.append(recipe) or object(),
    )
    monkeypatch.setattr(
        stage_services.extraction_state, "resource_drill_count",
        lambda _c, _s, _f, resource: 6 if resource == "iron-ore" else 0,
    )

    assert stage_services._item_supply_chain_is_scheduled(
        object(), "nauvis", "player", "pipe",
    )
    assert lines == ["pipe", "iron-plate"]

    monkeypatch.setattr(
        stage_services.extraction_state, "resource_drill_count",
        lambda *_a: 0,
    )
    assert not stage_services._item_supply_chain_is_scheduled(
        object(), "nauvis", "player", "pipe",
    )


def test_material_remedy_reuses_one_local_provider_chest(monkeypatch) -> None:
    """A recurring ghost shortage must not build one chest per retry."""
    provider = (50.5, -12.5)
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"transport-belt": 20},
    )
    monkeypatch.setattr(
        builder.live_base, "network_item_count", lambda *_a: 0,
    )
    monkeypatch.setattr(
        builder.live_base, "nearest_container", lambda *_a, **_k: provider,
    )
    monkeypatch.setattr(
        builder.live_base, "transfer_stock", lambda *_a: 16,
    )
    monkeypatch.setattr(
        builder, "_submit", lambda *_a, **_k: pytest.fail("must reuse chest"),
    )
    coverage: list[tuple[float, float]] = []
    monkeypatch.setattr(
        builder, "ensure_logistic_coverage",
        lambda *_a, **_k: coverage.extend(_a[4]) or False,
    )

    acted = builder._apply_remedy(
        object(), object(), "nauvis", "player", "mine", "materials:transport-belt:1",
        "ghost belt needs one", (50.0, -12.0), (54.0, -17.0), [], [], None,
        lambda _message: None,
    )

    assert acted
    assert coverage == [provider]


def test_mine_material_delivery_stays_outside_its_growth_envelope(monkeypatch) -> None:
    monkeypatch.setattr(
        builder.live_base, "available_items", lambda *_a: {"transport-belt": 20},
    )
    monkeypatch.setattr(builder.live_base, "network_item_count", lambda *_a: 0)
    monkeypatch.setattr(builder.live_base, "nearest_container", lambda *_a, **_k: None)
    monkeypatch.setattr(
        builder.live_base, "chained_clear_spots",
        lambda _client, _surface, _placements, start: [
            ("passive-provider-chest", start[0], start[1]),
        ],
    )
    submitted: list[dict] = []
    monkeypatch.setattr(
        builder, "_submit", lambda *_a, **_k: submitted.append(_a[3]),
    )
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a, **_k: False)
    monkeypatch.setattr(builder.live_base, "transfer_stock", lambda *_a: 16)
    area = ((35.0, -27.0), (100.0, 3.0))

    builder._apply_remedy(
        object(), object(), "nauvis", "player", "existing mine for iron-ore",
        "materials:transport-belt:1", "ghost belt needs one", (50.0, -12.0),
        (54.0, -17.0), [], [], area, lambda _message: None,
    )

    position = submitted[0]["phases"][0]["actions"][0]["position"]
    assert (position["x"], position["y"]) == (32.0, -30.0)


def test_producer_backed_shortage_places_blueprint_without_explicit_override(
    monkeypatch,
) -> None:
    plan = {
        "force": "player",
        "phases": [{"actions": [{
            "action_type": "place_ghost", "entity": "pipe",
            "position": {"x": 10.5, "y": 20.5},
        }]}],
    }
    reports = []
    bridge = type("Bridge", (), {
        "build_layout": lambda _self, _authorization, _plan: (
            reports.append(_plan) or {
                "ok": True, "attempted_placements": 1,
                "succeeded_placements": 1, "placed_ghosts": 1,
                "placed_entities": 0,
            }
        ),
    })()
    monkeypatch.setattr(stage_services, "consume_plan_submission", lambda *_a: None)
    monkeypatch.setattr(stage_services, "clear_plan_clutter", lambda *_a: None)
    monkeypatch.setattr(stage_services, "load_json", lambda report: report)
    monkeypatch.setattr(
        stage_services, "assert_affordable",
        lambda *_a: (_ for _ in ()).throw(MaterialShortage(
            "oil-cell", {"pipe": 500}, {},
        )),
    )
    monkeypatch.setattr(
        stage_services, "_shortage_has_complete_supply_chains",
        lambda *_a: True,
    )

    stage_services._submit(
        object(), bridge, "nauvis", plan, "oil-cell", lambda _message: None,
    )

    assert reports == [plan]


def test_ghost_blockages_decodes_missing_material() -> None:
    client = _Client("77.5|-18.5|transport-belt|missing_material:transport-belt:4:0")

    result = live_base.ghost_blockages(
        client, "nauvis", "player", ((70.0, -25.0), (80.0, -10.0)),
    )

    assert result == [{
        "position": (77.5, -18.5), "entity": "transport-belt",
        "reason": "missing_material:transport-belt:4:0",
        "item": "transport-belt", "required": 4, "available": 0,
    }]
    assert "entity-ghost" in client.commands[0]
    assert "area={{70.0,-25.0},{80.0,-10.0}}" in client.commands[0]


def test_diagnosis_promotes_missing_ghost_material_to_mall_demand(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "nearest_roboport", lambda *_a: (0.0, 0.0))
    monkeypatch.setattr(live_base, "entity_status_name", lambda *_a: "working")
    monkeypatch.setattr(
        live_base, "ghost_blockages", lambda *_a: [{
            "position": (77.5, -18.5), "entity": "transport-belt",
            "reason": "missing_material:transport-belt:4:0",
            "item": "transport-belt", "required": 4, "available": 0,
        }],
    )

    issue = builder._diagnose_blockage(
        None, "nauvis", "player", (10.0, 10.0), (10.0, 12.0),
        [(11.0, 10.0)], area=((0.0, 0.0), (100.0, 100.0)),
    )

    assert issue == (
        "ghost transport-belt at (77.5, -18.5) needs 4 transport-belt, "
        "but its network has 0",
        "materials:transport-belt:4",
    )


def test_material_remedy_raises_shortage_when_base_lacks_item(monkeypatch) -> None:
    monkeypatch.setattr(live_base, "available_items", lambda *_a: {})

    with pytest.raises(builder.MaterialShortage) as raised:
        builder._apply_remedy(
            None, None, "nauvis", "player", "conversion_stone-brick",
            "materials:stone-brick:8", "missing bricks", (0.0, 0.0),
            (0.0, 0.0), [], [], None, lambda _message: None,
        )

    assert raised.value.required == {"stone-brick": 8}

def test_coverage_remedy_targets_the_stranded_ghost_not_stage_origin(monkeypatch) -> None:
    targets = []
    monkeypatch.setattr(
        live_base, "ghost_blockages", lambda *_a: [{
            "position": (25.5, -49.5), "entity": "transport-belt",
            "reason": "out_of_construction_range",
        }],
    )
    monkeypatch.setattr(
        builder, "extend_roboport_coverage",
        lambda *_args, **_kwargs: targets.append(_args[4]) or True,
    )

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "mining stage for coal",
        "coverage", "ghost transport-belt outside coverage", (10.0, -40.0),
        (10.0, -40.0), [], [], ((0.0, -60.0), (40.0, -20.0)),
        lambda _message: None,
    )

    assert acted
    assert targets == [(25.5, -49.5)]

def test_stage_power_repairs_stranded_machine_when_substation_is_powered(monkeypatch) -> None:
    calls = []

    def fake_extend(_client, _bridge, _surface, _force, position, _emit):
        calls.append(position)
        return position == (59.5, -63.5)

    monkeypatch.setattr(builder, "extend_power", fake_extend)
    monkeypatch.setattr(
        live_base, "entity_statuses",
        lambda *_a: {(53.5, -67.5): "working", (59.5, -63.5): "no_power"},
    )

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "mining stage for stone",
        "stage_power", "1 machine(s) unpowered", (48.0, -70.0),
        (48.0, -70.0), [(53.5, -67.5), (59.5, -63.5)], [], None,
        lambda _message: None,
    )

    assert acted
    assert calls == [(48.0, -70.0), (59.5, -63.5)]

def test_material_remedy_waits_when_network_reserves_stock(monkeypatch) -> None:
    """Global stock can exist while another construction job reserves it."""
    messages: list[str] = []
    monkeypatch.setattr(
        live_base, "available_items", lambda *_a: {"transport-belt": 10},
    )

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "conversion_automation-science-pack",
        "materials:transport-belt:1", "ghost belt has no network stock",
        (0.0, 0.0), (0.0, 0.0), [], [], None, messages.append,
    )

    assert acted
    assert any("temporarily unavailable" in message for message in messages)

def test_logistic_coverage_remedy_waits_out_the_roboport_charge(monkeypatch) -> None:
    """A just-connected roboport needs tens of seconds to charge before its
    logistic area exists; rounds that re-check instantly are six ways of doing
    nothing (live run of 2026-08-22 00:52 burned all six in ~2s and died)."""
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a: False)
    polls = iter([None, None, 4])
    slept: list[float] = []
    monkeypatch.setattr(
        builder.time, "sleep", lambda seconds: slept.append(seconds),
    )
    monkeypatch.setattr(
        live_base, "logistic_network_ids",
        lambda _c, _s, positions: {positions[0]: next(polls)},
    )
    # Keep the bound effectively unbounded for this scenario.
    monkeypatch.setattr(builder, "_LOGISTIC_CHARGE_WAIT_SECONDS", 90.0)
    monkeypatch.setattr(builder.time, "monotonic", lambda: 0.0)

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "logistic bootstrap for copper-plate",
        "logistic_coverage", "1 chest(s) outside every logistic area",
        (77.5, -39.5), (77.5, -39.5), [], [(77.5, -39.5)], None,
        lambda _message: None,
    )

    assert acted
    assert slept == [3.0, 3.0]

def test_logistic_coverage_remedy_reports_an_honest_noop_at_the_bound(monkeypatch) -> None:
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a: False)
    monkeypatch.setattr(
        live_base, "logistic_network_ids",
        lambda _c, _s, positions: {positions[0]: None},
    )
    ticks = iter(range(0, 400, 3))

    class FakeTime:
        @staticmethod
        def monotonic() -> float:
            return float(next(ticks))

        @staticmethod
        def sleep(_seconds: float) -> None:
            pass

    monkeypatch.setattr(builder, "time", FakeTime)

    acted = builder._apply_remedy(
        None, None, "nauvis", "player", "logistic bootstrap for copper-plate",
        "logistic_coverage", "1 chest(s) outside every logistic area",
        (77.5, -39.5), (77.5, -39.5), [], [(77.5, -39.5)], None,
        lambda _message: None,
    )

    assert not acted


def test_power_bridge_racing_a_concurrent_build_replans_once(monkeypatch) -> None:
    """A substation built by another stage between our survey and our bots'
    arrival is construction in flight, not a wall -- replan once on fresh
    ground (live run of 2026-08-22 02:46 ended the mission on this)."""
    from orchestrator import stage_services as ss

    surveys = {"n": 0}

    def fake_occupied(*_a, **_k):
        surveys["n"] += 1
        return set() if surveys["n"] == 1 else {(48, -70)}

    submits: list[str] = []

    def fake_submit(_c, _b, _s, plan, name, _emit):
        submits.append(name)
        if len(submits) == 1:
            raise ss.StuckError(
                "power_bridge: blocked by real infrastructure "
                "[{'position': {'x': 48, 'y': -70}, "
                "'reason': 'exact_position_occupied_by_different_entity'}]"
            )

    monkeypatch.setattr(ss, "_submit", fake_submit)
    monkeypatch.setattr(ss.live_base, "pole_network_id", lambda *_a: None)
    monkeypatch.setattr(
        ss.live_base, "nearest_powered_pole",
        lambda *_a, **_k: ((50.0, -5.0), "substation"),
    )
    monkeypatch.setattr(ss.live_base, "entity_at", lambda *_a: None)
    monkeypatch.setattr(ss.live_base, "occupied_tiles", fake_occupied)
    monkeypatch.setattr(
        ss.live_base, "network_generation_kw", lambda *_a: 167.0,
    )

    acted = ss.extend_power(
        object(), object(), "nauvis", "player", (45.0, -66.0),
        lambda _m: None,
    )

    assert acted
    assert submits.count("power_bridge") == 2


def test_power_bridge_race_accepts_a_network_that_merged_mid_retry(monkeypatch) -> None:
    """A partial first submission can join power before its retry re-surveys."""
    from orchestrator import stage_services as ss

    network_ids = iter((3, 1))
    monkeypatch.setattr(
        ss.live_base, "pole_network_id", lambda *_a: next(network_ids),
    )
    monkeypatch.setattr(ss.live_base, "nearest_powered_pole", lambda *_a, **_k: None)
    monkeypatch.setattr(ss.live_base, "network_generation_kw", lambda *_a: 166.7)

    assert ss.extend_power(
        object(), object(), "nauvis", "player", (40.0, -6.0),
        lambda _m: None, _retried=True,
    )


def test_power_bridge_routes_around_a_reserved_refinery_footprint(monkeypatch) -> None:
    """Emergency power may not consume a belt tile planned by an expansion."""
    from orchestrator import autonomous_builder as builder_module
    from orchestrator import stage_services as ss

    submitted = []
    monkeypatch.setattr(ss.live_base, "pole_network_id", lambda *_a: None)
    powered_options = []
    monkeypatch.setattr(
        ss.live_base, "nearest_powered_pole",
        lambda *_a, **kwargs:
            powered_options.append(kwargs) or ((0.0, 0.0), "medium-electric-pole"),
    )
    monkeypatch.setattr(ss.live_base, "entity_at", lambda *_a: None)
    occupied_options = []
    monkeypatch.setattr(
        ss.live_base, "occupied_tiles",
        lambda *_a, **kwargs: occupied_options.append(kwargs) or set(),
    )
    monkeypatch.setattr(
        ss.live_base, "network_generation_kw", lambda *_a: 167.0,
    )
    monkeypatch.setattr(
        ss, "_submit",
        lambda _c, _b, _s, plan, _name, _emit: submitted.append(plan),
    )
    monkeypatch.setattr(
        builder_module, "_top_up_solar_generation", lambda *_a, **_k: False,
    )

    reserved = {(x, 0) for x in range(4, 21)}
    assert ss.extend_power(
        object(), object(), "nauvis", "player", (24.0, 0.0),
        lambda _message: None, reserved_tiles=reserved,
    )

    poles = {
        (math.floor(action["position"]["x"]), math.floor(action["position"]["y"]))
        for action in submitted[0]["phases"][0]["actions"]
    }
    assert not poles & reserved
    assert occupied_options == [{"include_resources": True}]
    assert powered_options == [{
        "exclude_network_id": None,
        "avoid_resources": True,
    }]


def test_power_bridge_retries_when_placed_chain_is_still_disconnected(
    monkeypatch,
) -> None:
    """A successful placement report is not proof of electrical continuity."""
    from orchestrator import autonomous_builder as builder_module
    from orchestrator import stage_services as ss

    submissions: list[dict] = []
    messages: list[str] = []
    monkeypatch.setattr(ss.live_base, "pole_network_id", lambda *_a: None)
    monkeypatch.setattr(
        ss.live_base, "nearest_powered_pole",
        lambda *_a, **_k: ((89.5, -68.5), "medium-electric-pole"),
    )
    monkeypatch.setattr(
        ss.live_base, "entity_at",
        lambda *_a: {"name": "roboport"},
    )
    monkeypatch.setattr(ss.live_base, "occupied_tiles", lambda *_a, **_k: set())
    observed_generation = iter((0.0, 167.0))
    monkeypatch.setattr(
        ss.live_base, "network_generation_kw",
        lambda *_a: next(observed_generation),
    )
    monkeypatch.setattr(
        ss, "_submit",
        lambda _c, _b, _s, plan, _name, _emit: submissions.append(plan),
    )
    monkeypatch.setattr(
        builder_module, "_top_up_solar_generation", lambda *_a, **_k: False,
    )

    assert ss.extend_power(
        object(), object(), "nauvis", "player", (136.0, -67.0),
        messages.append,
    )

    assert len(submissions) == 2
    assert any("remained electrically disconnected" in message for message in messages)


def test_disconnected_roboport_is_repaired_again_on_a_later_survey(
    monkeypatch,
) -> None:
    """An attempted repair must not suppress a still-live power fault."""
    from orchestrator import stage_services as ss

    repairs: list[tuple[float, float]] = []
    monkeypatch.setattr(
        ss.live_base, "roboports_needing_power",
        lambda *_a: [((136.0, -67.0), "low_power")],
    )
    monkeypatch.setattr(
        ss, "extend_power",
        lambda _c, _b, _s, _f, position, _emit: repairs.append(position) or True,
    )

    for _ in range(2):
        ss._repair_existing_roboport_power(
            object(), object(), "nauvis", "player", lambda _message: None,
        )

    assert repairs == [(136.0, -67.0), (136.0, -67.0)]


def test_neighboring_power_remedy_waits_for_the_first_bridge_to_settle(
    monkeypatch,
) -> None:
    from orchestrator import autonomous_builder as builder_module
    from orchestrator import stage_services as ss

    ss._PENDING_POWER_BRIDGES.clear()
    network_ids = iter((None, 7))
    powered_poles = iter((((0.0, 0.0), "medium-electric-pole"), None))
    submitted = []
    clock = [100.0]

    monkeypatch.setattr(ss.live_base, "pole_network_id", lambda *_a: next(network_ids))
    monkeypatch.setattr(
        ss.live_base, "nearest_powered_pole",
        lambda *_a, **_k: next(powered_poles),
    )
    monkeypatch.setattr(ss.live_base, "network_generation_kw", lambda *_a: 167.0)
    monkeypatch.setattr(ss.live_base, "entity_at", lambda *_a: None)
    monkeypatch.setattr(ss.live_base, "occupied_tiles", lambda *_a, **_k: set())
    monkeypatch.setattr(
        ss, "_submit",
        lambda _c, _b, _s, plan, _name, _emit: submitted.append(plan),
    )
    monkeypatch.setattr(builder_module, "_top_up_solar_generation", lambda *_a, **_k: False)
    monkeypatch.setattr(ss.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        ss.time, "sleep",
        lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )

    assert ss.extend_power(
        object(), object(), "nauvis", "player", (30.0, 0.0),
        lambda _message: None,
    )
    assert ss.extend_power(
        object(), object(), "nauvis", "player", (24.0, 0.0),
        lambda _message: None,
    )

    assert len(submitted) == 1
    assert clock[0] == 103.0
    ss._PENDING_POWER_BRIDGES.clear()


def test_remediation_extends_repeatedly_while_local_ghosts_fall(monkeypatch) -> None:
    """Live run 30 (2026-08-22): a ~250-tile oil pipeline built at cross-base
    robot-flight speed fell 15 -> 10 ghosts across two extensions and was
    killed anyway -- the old loop granted exactly one extension and judged
    progress by the WHOLE-SURFACE ghost count. Extensions must repeat while
    THIS stage's pending ghosts keep falling."""
    monkeypatch.setattr(builder, "extend_roboport_coverage", lambda *_a, **_k: True)
    monkeypatch.setattr(builder, "ensure_logistic_coverage", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_diagnose_blockage", lambda *_a, **_k: None)
    monkeypatch.setattr(builder, "_apply_remedy", lambda *_a, **_k: False)
    readings = iter([4, 3, 3, 2, 2, 2])
    monkeypatch.setattr(
        builder, "_wait_for_ghosts",
        lambda *_a, **_k: next(readings),
    )
    emitted: list[str] = []

    with pytest.raises(builder.StuckError) as stuck:
        builder.bring_stage_up(
            None, None, "nauvis", "player", "pipeline",
            (0.0, 0.0), ((0.0, 0.0), (9.0, 9.0)), (0.0, 0.0), [],
            emitted.append, rounds=2, interval=0.0,
        )

    extensions = [e for e in emitted if "extending remediation" in e]
    assert len(extensions) == 2
    # The verdict must tell the truth about bot progress instead of claiming
    # nothing was built.
    assert "area ghosts 2 -> 2" in str(stuck.value)


def test_missing_feed_on_a_mall_cell_rebuilds_instead_of_dying(monkeypatch) -> None:
    """Live run 32 (2026-08-22): the strict transport repair raised
    'expected requester-chest ... found nothing' for a half-built paired cell
    and killed the mission. When the machine belongs to a declared mall half,
    the repair path must regenerate that cell in place and re-survey."""
    from orchestrator.mall_builder import _slot_position

    origin, side = (67, 53), "left"
    machine = _slot_position(origin, side)
    monkeypatch.setattr(
        builder, "extend_roboport_coverage", lambda *_a, **_k: False,
    )
    monkeypatch.setattr(
        builder, "ensure_logistic_coverage", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "nearest_pole_on_other_network",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder.live_base, "entity_statuses",
        lambda *_a, **_k: {tuple(machine)[:2]: "item_ingredient_shortage"},
    )
    monkeypatch.setattr(
        builder, "_existing_stage_chests", lambda *_a, **_k: [],
    )
    monkeypatch.setattr(
        builder, "bring_stage_up", lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        builder, "mall_cell_needs_rebuild", lambda *_a, **_k: False,
    )
    rebuilt: list[str] = []
    monkeypatch.setattr(
        builder, "rebuild_incomplete_mall_cell",
        lambda _c, _b, _s, _f, recipe, position, _ref, emit, **_k:
            rebuilt.append((recipe, position)) or True,
    )
    raised = False

    def raising_repair(*_a, **_k):
        nonlocal raised
        raised = True
        raise builder.StuckError(
            "existing advanced-circuit feed for copper-cable expected "
            "requester-chest at (44.5, 50.5), found nothing"
        )

    monkeypatch.setattr(builder, "repair_existing_ingredient_transport", raising_repair)

    class _Existing:
        machine_count = 1
        working_count = 0
        machine_positions = [machine]
        output_position = machine

    plan = builder._LinePlan(
        existing=_Existing(),
        spec={"ingredients": [], "amounts": []},
        production_target=1,
        mall_storage_limit=0,
        fill_provider=False,
        demand=0.0,
        saturated=False,
        promoted_count=None,
        promote_to_line=False,
        at_size=True,
        mall_request_multiplier=1,
    )

    result = builder._repair_stalled_line(
        None, None, "nauvis", "player", "advanced-circuit",
        (35.0, 21.0), lambda _m: None, plan,
        mall_provider=None, upgrade_bootstrap=True,
    )

    assert raised, "the strict repair must have run before the fallback"
    assert rebuilt == [("advanced-circuit", machine)]
    assert result is None
