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
