# Path: tests/test_training_lab_validation_lua.py
# Purpose: Exercise pure training scenario validation and tick measurement in Lua.

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from training.scenarios.mining_delivery import generate_mining_delivery_scenario

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "factorio_training_lab"

try:
    import lupa
except ImportError:  # pragma: no cover - the static contract tests still run
    lupa = None


def _to_lua(runtime, value):
    if isinstance(value, dict):
        table = runtime.table()
        for key, item in value.items():
            table[key] = _to_lua(runtime, item)
        return table
    if isinstance(value, list):
        table = runtime.table()
        for index, item in enumerate(value, 1):
            table[index] = _to_lua(runtime, item)
        return table
    return value


@pytest.fixture
def lua():
    if lupa is None:
        pytest.skip("pip install lupa for behavioural Lua validation")
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    runtime.execute(f'package.path = "{LAB.as_posix()}/?.lua;" .. package.path')
    return runtime


def _scenario() -> dict:
    scenario = generate_mining_delivery_scenario(7)
    return scenario


def test_validation_accepts_the_corrected_generated_contract(lua) -> None:
    module = lua.eval('require("scenario_validation")')[0]

    assert module.validate_scenario(_to_lua(lua, _scenario())) is not None


def test_validation_rejects_half_tile_power_fixture(lua) -> None:
    module = lua.eval('require("scenario_validation")')[0]
    scenario = _scenario()
    scenario["fixtures"][0]["position"] = [0.5, 0.5]

    lua.globals().candidate = _to_lua(lua, scenario)
    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "integral centre" in message


def test_validation_rejects_real_base_identity(lua) -> None:
    scenario = _scenario()
    scenario["environment"]["surface_name"] = "nauvis"
    scenario["environment"]["force_name"] = "player"
    lua.globals().candidate = _to_lua(lua, scenario)

    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "training-only name" in message


def test_validation_rejects_budget_and_allowed_entity_drift(lua) -> None:
    scenario = _scenario()
    scenario["constraints"]["allowed_entities"].remove("splitter")
    lua.globals().candidate = _to_lua(lua, scenario)

    ok, message = lua.eval(
        "(function() return pcall(function() return require('scenario_validation').validate_scenario(candidate) end) end)()"
    )
    assert ok is False
    assert "differs from budget" in message


def test_tick_sampler_requires_consecutive_target_windows(lua) -> None:
    lua.execute("storage = {training_lab={version='1.0.0', report_sequence=0, episodes={}, pending_force_merges={}}}")
    module = lua.eval('require("episode_measurement")')[0]
    state = _to_lua(lua, {
        "last_sample_tick": 0, "started_tick": 0, "sample_ticks": 0,
        "sample_items": 0, "delivered_items": 0, "rate_per_tick": 0,
        "sustained_ticks": 0, "status": "ready", "failure_kind": "none",
        "failure_reason": "", "scenario": {
            "objective": {"target_rate_per_tick": 2 / 60, "sustain_ticks": 120},
            "constraints": {"max_episode_ticks": 600},
        },
    })

    module.advance_sample(state, 2, 60)
    assert state.sustained_ticks == 60
    module.advance_sample(state, 0, 120)
    assert state.sustained_ticks == 0
    module.advance_sample(state, 2, 180)
    module.advance_sample(state, 2, 240)
    assert state.status == "completed"


def test_tick_sampler_records_timeout_in_ticks(lua) -> None:
    lua.execute("storage = {training_lab={version='1.0.0', report_sequence=0, episodes={}, pending_force_merges={}}}")
    module = lua.eval('require("episode_measurement")')[0]
    state = _to_lua(lua, {
        "last_sample_tick": 540, "started_tick": 0, "sample_ticks": 0,
        "sample_items": 0, "delivered_items": 0, "rate_per_tick": 0,
        "sustained_ticks": 0, "status": "running", "failure_kind": "none",
        "failure_reason": "", "scenario": {
            "objective": {"target_rate_per_tick": 2 / 60, "sustain_ticks": 120},
            "constraints": {"max_episode_ticks": 600},
        },
    })

    module.advance_sample(state, 0, 600)
    assert state.status == "timed_out"
    assert state.failure_kind == "timeout"


def test_fixture_check_uses_surface_lookup_even_when_global_lookup_is_empty(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.0.0', report_sequence=0, episodes={}, pending_force_merges={}}}
        game = {get_entity_by_unit_number=function() return nil end}
        training_force = {name='training-mining-delivery-00000001'}
        fixture_entity = {
          valid=true, name='infinity-chest', unit_number=7,
          position={x=8.5, y=-8.5},
          surface={name='training/mining-delivery-00000001'}, force=training_force
        }
        fixture_surface = {find_entities_filtered=function(_) return {fixture_entity} end}
        fixture_episode = {
          surface_name='training/mining-delivery-00000001',
          fixtures={sink={name='infinity-chest', unit_number=7, position={x=8.5, y=-8.5}}}
        }
    """)
    module = lua.eval('require("episode_measurement")')[0]

    assert module.check_fixtures(
        lua.globals().fixture_surface, lua.globals().training_force, lua.globals().fixture_episode,
    ) is True

def test_chunked_plan_execution_module_loads(lua) -> None:
    module = lua.eval('require("plan_execution")')[0]

    assert module.MAX_CHUNK_BYTES == 1800

def test_plan_execution_rejects_cumulative_budget_before_placement(lua) -> None:
    lua.execute("""
        storage = {training_lab={version='1.0.0', report_sequence=0, episodes={}, pending_force_merges={}, uploads={}}}
        game = {surfaces={}, forces={}}
        prototypes = {entity={['transport-belt']={collision_box={left_top={x=-0.4,y=-0.4},right_bottom={x=0.4,y=0.4}}}}}
        existing = {type='transport-belt', name='transport-belt', unit_number=11, position={x=2,y=2}}
        surface = {find_entities_filtered=function(filter)
          if filter.force then return {existing} end
          return {}
        end}
        force = {name='training-mining-delivery-00000001'}
        episode = {
          fixtures={},
          scenario={constraints={allowed_entities={'transport-belt'},allowed_build_area={x_min=0,x_max_exclusive=10,y_min=0,y_max_exclusive=10}},construction_budget={['transport-belt']=1}}
        }
        plan = {phases={{name='placement',actions={{action_type='place_entity',entity='transport-belt',position={x=4,y=4}}}}}}
    """)
    module = lua.eval('require("plan_execution")')[0]

    ok, message = lua.eval("(function() return pcall(function() return require('plan_execution').plan_actions(plan, episode, surface, force) end) end)()")

    assert ok is False
    assert "construction budget" in message


def test_training_footprints_cannot_cross_the_build_boundary(lua) -> None:
    lua.execute("""
        prototypes = {entity={
          ['electric-mining-drill']={collision_box={left_top={x=-1.5,y=-1.5},right_bottom={x=1.5,y=1.5}}},
          ['splitter']={collision_box={left_top={x=-0.9,y=-0.9},right_bottom={x=0.9,y=0.9}}}
        }}
        bounds = {x_min=0,x_max_exclusive=10,y_min=0,y_max_exclusive=10}
        drill_inside = {x=1.5,y=1.5}
        drill_outside = {x=1.0,y=1.5}
        splitter_inside = {x=9.0,y=5.0}
        splitter_outside = {x=9.5,y=5.0}
    """)
    geometry = lua.eval('require("training_geometry")')[0]

    assert geometry.fits(lua.globals().bounds, "electric-mining-drill", lua.globals().drill_inside) is True
    assert geometry.fits(lua.globals().bounds, "electric-mining-drill", lua.globals().drill_outside) is False
    assert geometry.fits(lua.globals().bounds, "splitter", lua.globals().splitter_inside) is True
    assert geometry.fits(lua.globals().bounds, "splitter", lua.globals().splitter_outside) is False