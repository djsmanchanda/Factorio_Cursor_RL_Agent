# Path: tests/test_training_factory_health_lua.py | Purpose: Exercise actual Lua stock, status and consumption sampling.

from tests.test_training_item_telemetry_lua import _run_lua


def test_phase_stock_health_and_samples_exclude_foreign_and_destroyed_entities():
    _run_lua('''
      defines.inventory.furnace_source, defines.inventory.furnace_result = 2, 3
      defines.entity_status.working, defines.entity_status.no_ingredients = 1, 2
      local machine = {valid=true, type='furnace', name='electric-furnace',
        force=training_force, surface=training_surface, status=1, energy=100, electric_network_id=1,
        get_inventory=function(id) return {get_item_count=function(item)
          if id == 2 and item == 'iron-ore' then return 10 end
          if id == 3 and item == 'iron-plate' then return 3 end
          return 0
        end} end}
      local foreign = {valid=true, type='furnace', force={}, surface=training_surface}
      local dead = {valid=false}
      episode_state.phase_entities = {iron={machine,foreign,dead}}
      local health = require('workflow_health')
      local role = function() return 'consumer' end
      health.sample(episode_state, training_surface, training_force, 60)
      machine.status = 2
      health.sample(episode_state, training_surface, training_force, 120)
      local result = health.snapshot(episode_state, training_surface, training_force, 120,
        {'iron-ore','iron-plate'}, role, {}, 100)
      assert(result.phases.iron.entities == 1 and result.phases.iron.machines == 1)
      assert(result.phases.iron.statuses.starved == 1)
      assert(result.phases.iron.stock.input.items['iron-ore'] == 10)
      assert(result.phases.iron.stock.output.items['iron-plate'] == 3)
      assert(result.phases.iron.machine_sample_ticks == 120)
      assert(result.phases.iron.working_sample_ticks == 60)
      assert(result.phases.iron.power.disconnected == 0)
      machine.get_inventory = function() error('inventory unavailable') end
      machine.electric_network_id = 0
      result = health.snapshot(episode_state, training_surface, training_force, 120,
        {'iron-ore','iron-plate'}, role, {}, 100)
      assert(not result.phases.iron.stock.input.available)
      assert(result.phases.iron.power.disconnected == 1)
      assert(result.phases.iron.machine_sample_ticks == 120, 'observing must not add samples')
    ''')


def test_consumption_is_separate_from_production_and_counter_reset_breaks_window():
    _run_lua('''
      local consumed = 2
      stats.get_output_count = function(item) if item == 'iron-plate' then return consumed end; return 0 end
      local measurement = require('episode_measurement')
      measurement.sample_all(60)
      assert(episode_state.item_production_metrics.items['iron-plate'].sample_items == 5)
      assert(episode_state.item_consumption_metrics.items['iron-plate'].sample_items == 2)
      consumed, game.tick = 7, 120
      measurement.sample_all(120)
      assert(episode_state.item_consumption_metrics.items['iron-plate'].sample_items == 5)
      assert(episode_state.item_consumption_metrics.items['iron-plate'].window_end_tick == 120)
      consumed, game.tick = 1, 180
      measurement.sample_all(180)
      assert(not episode_state.item_consumption_metrics.items['iron-plate'].available)
      consumed, game.tick = 4, 240
      measurement.sample_all(240)
      assert(episode_state.item_consumption_metrics.items['iron-plate'].window_ticks == 0)
      consumed, game.tick = 6, 300
      measurement.sample_all(300)
      assert(episode_state.item_consumption_metrics.items['iron-plate'].sample_items == 2)
    ''')
