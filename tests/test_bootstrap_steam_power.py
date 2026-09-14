# Path: tests/test_bootstrap_steam_power.py
# Purpose: Verify the measured, direct-belt temporary steam bootstrap contract.

from __future__ import annotations

import pytest

from orchestrator.bootstrap_steam_power import (
    STEAM_UNIT_KW,
    build_bootstrap_steam_unit,
    measured_steam_need,
    solar_storage_is_night_sustainable,
)
from planners.plan_validation import validate_build_plan


def _site(direction: str) -> dict:
    return {
        "position": (10.5, 20.5), "direction": direction,
        "output": (0, 0), "resource": "water",
    }


def _entities(plan: dict) -> list[str]:
    return [
        action["entity"]
        for phase in plan["phases"] for action in phase["actions"]
    ]


def test_seed_grid_with_sub_seven_mw_load_never_requests_steam() -> None:
    need = measured_steam_need(10_000.0, 7_000.0)

    assert need.target_kw == 8_750.0
    assert need.deficit_kw == 0.0
    assert need.required_units == 0


def test_real_entrypoint_preserves_seed_supply_at_seven_mw(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from orchestrator import bootstrap_steam_power as steam, stage_chemical
    class Client:
        def command(self, _):
            raise AssertionError('unexpected query')
    monkeypatch.setattr(steam.live_base, 'network_firm_generation_kw', lambda *_: 10000)
    monkeypatch.setattr(steam, 'recent_consumption_kw', lambda *_: 7000)
    monkeypatch.setattr(stage_chemical, 'ensure_coal_mine', lambda *_a, **_k: pytest.fail('seed grid must not consume coal construction items'))
    assert not steam.maybe_ensure_bootstrap_steam_power(
        Client(), SimpleNamespace(script_output=tmp_path, episode_id='test'),
        'nauvis', 'player', (0, 0), lambda _: None,
    )


def test_unfunded_steam_coal_prerequisite_queues_instead_of_crashing(monkeypatch):
    from orchestrator import autonomous_builder as builder
    from orchestrator.parts_mall import MaterialShortage
    monkeypatch.setattr(builder, 'extend_power', lambda *_a, **_k: False)
    def shortage(*_):
        raise MaterialShortage('mining_coal', {'electric-mining-drill': 4, 'transport-belt': 10}, {'transport-belt': 2})
    monkeypatch.setattr(builder, 'maybe_ensure_bootstrap_steam_power', shortage)
    targets = {'inserter': 3}
    assert not builder._top_up_solar_generation(object(), object(), 'nauvis', 'player', (0, 0), lambda _: None, mall_targets=targets)
    assert targets == {'inserter': 3, 'electric-mining-drill': 4, 'transport-belt': 10}


def test_declared_steam_count_cannot_hide_extra_boiler():
    from copy import deepcopy
    plan, _ = build_bootstrap_steam_unit(_site('west'))
    extra = deepcopy(next(a for p in plan['phases'] for a in p['actions'] if a['entity'] == 'boiler'))
    extra['position']['x'] += 50
    plan['phases'][0]['actions'].append(extra)
    with pytest.raises(ValueError, match='Electric-only'):
        validate_build_plan(plan)


def test_measured_deficit_is_sized_in_complete_boiler_engine_units() -> None:
    need = measured_steam_need(10_000.0, 9_000.0)

    assert need.deficit_kw == 1_250.0
    assert need.required_units == 1
    assert need.required_units * STEAM_UNIT_KW >= need.deficit_kw


def test_seed_generation_cannot_license_steam_retirement() -> None:
    # 10 MW from the seed interface is intentionally absent from this check.
    assert not solar_storage_is_night_sustainable(0.0, 500.0, 7_000.0)
    assert solar_storage_is_night_sustainable(15_000.0, 400.0, 7_000.0)


def test_bootstrap_steam_unit_is_atomic_bot_ghosted_and_direct_belt_fed() -> None:
    plan, fuel_belt = build_bootstrap_steam_unit(_site("west"))

    assert plan["atomic"] is True
    assert plan["power_contract"] == {
        "kind": "bootstrap-steam-v1", "boilers": 1, "steam_engines": 2,
    }
    assert _entities(plan).count("boiler") == 1
    assert _entities(plan).count("steam-engine") == 2
    assert _entities(plan).count("offshore-pump") == 1
    assert _entities(plan).count("transport-belt") == 1
    assert _entities(plan).count("inserter") == 1
    assert _entities(plan).count("medium-electric-pole") == 3
    assert all(
        action["action_type"] == "place_ghost"
        for phase in plan["phases"] for action in phase["actions"]
    )
    assert fuel_belt != _site("west")["position"]
    validate_build_plan(plan)


def test_canonical_north_geometry_has_disjoint_bodies_and_exact_port_spacing() -> None:
    plan, _fuel_belt = build_bootstrap_steam_unit(_site("west"))
    positions = {
        action["entity"] + str(index): action["position"]
        for phase in plan["phases"]
        for index, action in enumerate(phase["actions"])
        if action["entity"] in {"pipe", "boiler", "steam-engine"}
    }
    boiler = next(position for key, position in positions.items() if key.startswith("boiler"))
    engines = [position for key, position in positions.items() if key.startswith("steam-engine")]
    water = next(position for key, position in positions.items() if key.startswith("pipe"))

    assert water == {"x": boiler["x"] - 2.0, "y": boiler["y"] + 0.5}
    assert engines[0] == {"x": boiler["x"], "y": boiler["y"] - 3.5}
    assert engines[1] == {"x": engines[0]["x"], "y": engines[0]["y"] - 5.0}
    validate_build_plan(plan)


def test_steam_geometry_uses_each_verified_inline_shoreline_direction() -> None:
    for direction in ("east", "west"):
        plan, _fuel_belt = build_bootstrap_steam_unit(_site(direction))
        validate_build_plan(plan)


def test_bootstrap_contract_cannot_authorize_other_burners() -> None:
    plan, _fuel_belt = build_bootstrap_steam_unit(_site("west"))
    plan["phases"][0]["actions"].append({
        "action_type": "place_ghost", "entity": "burner-inserter",
        "position": {"x": 99.5, "y": 99.5},
    })

    with pytest.raises(ValueError, match="Electric-only invariant"):
        validate_build_plan(plan)
