# Path: tests/test_training_item_telemetry_lua.py
# Purpose: Verify bounded read-only per-item production measurements in disposable episodes.

from __future__ import annotations

import shutil
import subprocess
import json
from pathlib import Path

import pytest
from training.canonical import scenario_hash
from training.scenarios.mining_delivery import generate_mining_delivery_scenario

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "factorio_training_lab"

LUA_FIXTURES = """
        storage = {training_lab={episodes={}}}
        defines = {inventory={chest=1}, entity_status={}}
        production = {['iron-plate']=5, ['iron-ore']=0}
        stats = {get_input_count=function(item) return production[item] end,
          get_output_count=function() error('consumption counter is not production') end}
        training_force = {name='training-telemetry-00000001',
          get_item_production_statistics=function(surface)
            assert(surface.name == 'training/telemetry-00000001')
            return stats
          end}
        sink_inventory = {get_item_count=function() return 0 end,
          remove=function() return 0 end}
        sink_entity = {valid=true, name='infinity-chest', type='container', unit_number=1,
          position={x=0, y=0}, surface={name='training/telemetry-00000001'},
          get_inventory=function() return sink_inventory end}
        training_surface = {name='training/telemetry-00000001',
          find_entities=function() return {} end,
          find_entities_filtered=function() return {sink_entity} end}
        game = {tick=60,
          surfaces={['training/telemetry-00000001']=training_surface},
          forces={['training-telemetry-00000001']=training_force}}
        episode_state = {owner='factorio_training_lab', episode_id='ep-1',
          surface_name='training/telemetry-00000001', force_name='training-telemetry-00000001',
          started_tick=0, last_sample_tick=0, status='ready',
          sink_fixture_id='sink', sink_fixture_ids={'sink'},
          fixtures={sink={kind='item_sink', name='infinity-chest', unit_number=1,
            position={x=0, y=0}},}, obstacle_units={},
          scenario={objective={item='iron-plate', input_item='iron-ore',
            destination_fixture_id='sink', target_rate_per_tick=1/60, sustain_ticks=180},
            resource_patch={resource='iron-ore'},
            constraints={allowed_entities={}, allowed_build_area={x_min=-10,x_max_exclusive=10,
              y_min=-10,y_max_exclusive=10}, max_episode_ticks=10000}, construction_budget={}}}
        storage.training_lab.episodes['ep-1'] = episode_state
        """


def _run_lua(body: str) -> None:
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("Lua interpreter is required for behavioral telemetry tests")
    source = f'package.path = "{LAB.as_posix()}/?.lua;" .. package.path\n{LUA_FIXTURES}\n{body}\n'
    result = subprocess.run([lua, "-"], input=source, text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr or result.stdout


def _lua_literal(value):
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "{" + ",".join(_lua_literal(item) for item in value) + "}"
    if isinstance(value, dict):
        return "{" + ",".join(f"[{_lua_literal(key)}]={_lua_literal(item)}" for key, item in value.items()) + "}"
    raise TypeError(type(value))


def test_item_telemetry_is_force_scoped_bounded_and_tracks_rates() -> None:
    _run_lua(
        """
        local measurement = require('episode_measurement')
        measurement.sample_all(60)
        local first = episode_state.item_production_metrics
        assert(first.available == true)
        assert(first.items['iron-plate'].cumulative_items == 5)
        assert(first.items['iron-plate'].sample_items == 5)
        assert(first.items['iron-plate'].sample_ticks == 60)
        assert(math.abs(first.items['iron-plate'].sample_rate_per_tick - 5/60) < 1e-9)
        assert(math.abs(first.items['iron-plate'].window_rate_per_tick - 5/60) < 1e-9)
        assert(first.items['iron-plate'].window_ticks == 60)
        assert(first.items['iron-plate'].sustained_ticks == 60)
        assert(first.items['iron-ore'].cumulative_items == 0)
        local item_count = 0
        for _ in pairs(first.items) do item_count = item_count + 1 end
        assert(item_count == 2, 'item list must deduplicate objective and patch inputs')

        production['iron-plate'] = 11
        game.tick = 120
        measurement.sample_all(120)
        local second = episode_state.item_production_metrics.items['iron-plate']
        assert(second.cumulative_items == 11)
        assert(second.sample_items == 6)
        assert(math.abs(second.window_rate_per_tick - 11/120) < 1e-9)
        assert(second.window_ticks == 120)
        assert(second.sustained_ticks == 120)

        game.tick = 180
        measurement.sample_all(180)
        assert(episode_state.item_production_metrics.items['iron-plate'].sustained_ticks == 0)
        """
    )


def test_unavailable_statistics_are_reported_as_unknown() -> None:
    _run_lua(
        """
        training_force.get_item_production_statistics = function() error('unavailable') end
        require('episode_measurement').sample_all(60)
        local metrics = episode_state.item_production_metrics
        assert(metrics.available == false)
        assert(metrics.items['iron-plate'].available == false)
        assert(metrics.items['iron-ore'].available == false)
        training_force.get_item_production_statistics = function() return stats end
        production['iron-plate'] = 9
        game.tick = 120
        require('episode_measurement').sample_all(120)
        local resumed = episode_state.item_production_metrics.items['iron-plate']
        assert(resumed.sample_items == 0)
        assert(resumed.window_ticks == 0)
        production['iron-plate'] = 12
        game.tick = 180
        require('episode_measurement').sample_all(180)
        local fresh = episode_state.item_production_metrics.items['iron-plate']
        assert(fresh.sample_items == 3)
        assert(fresh.window_ticks == 60)
        """
    )


def test_extra_workflow_items_are_measured_without_skipping_patch_resource() -> None:
    _run_lua(
        """
        episode_state.scenario.objective.input_item = nil
        episode_state.scenario.objective.workflow_items = {'iron-gear-wheel', 'transport-belt'}
        production['iron-gear-wheel'] = 3
        production['transport-belt'] = 1
        require('episode_measurement').sample_all(60)
        local items = episode_state.item_production_metrics.items
        assert(items['iron-ore'].available == true)
        assert(items['iron-gear-wheel'].cumulative_items == 3)
        assert(items['transport-belt'].cumulative_items == 1)
        """
    )


def test_lua_validator_accepts_bounded_workflow_item_request() -> None:
    scenario = generate_mining_delivery_scenario(7)
    scenario["objective"]["workflow_items"] = ["iron-plate", "iron-gear-wheel", "transport-belt"]
    scenario["scenario_hash"] = scenario_hash(scenario)
    _run_lua(
        "local scenario = " + _lua_literal(scenario) + "\n"
        "assert(require('scenario_validation').validate_scenario(scenario))\n"
        "scenario.objective.workflow_items[4] = 'iron-gear-wheel'\n"
        "local ok = pcall(function() require('scenario_validation').validate_scenario(scenario) end)\n"
        "assert(not ok)\n"
    )
