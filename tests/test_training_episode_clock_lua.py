# Path: tests/test_training_episode_clock_lua.py | Purpose: Prevent the global server phase from shifting episode measurement windows.

from tests.test_training_item_telemetry_lua import _run_lua


def test_window_covers_600_episode_ticks_for_every_start_phase():
    _run_lua('''
      local measurement = require('episode_measurement')
      for phase=0,59 do
        episode_state.started_tick = phase
        episode_state.last_sample_tick = phase
        episode_state.status = 'ready'
        episode_state.item_production_samples = nil
        episode_state.item_production_last_tick = nil
        episode_state.item_production_metrics = nil
        episode_state.rate_samples = {}
        episode_state.sink_rate_samples = {}
        episode_state.sustained_ticks = 0
        production['iron-plate'] = 0
        for elapsed=1,600 do
          game.tick = phase + elapsed
          production['iron-plate'] = math.floor(elapsed / 100)
          measurement.sample_all(game.tick)
          if elapsed < 60 then assert(episode_state.item_production_metrics == nil) end
        end
        local item = episode_state.item_production_metrics.items['iron-plate']
        assert(item.window_ticks == 600, 'incomplete window at start phase ' .. phase)
        assert(item.window_end_tick == phase + 600)
        assert(math.abs(item.window_rate_per_tick - 6/600) < 1e-9)
        local samples = #episode_state.item_production_samples['iron-plate'].samples
        measurement.sample_all(game.tick)
        assert(#episode_state.item_production_samples['iron-plate'].samples == samples)
      end
    ''')


def test_observation_finishes_paused_endpoint_sample_once():
    _run_lua('''
      local measurement = require('episode_measurement')
      episode_state.started_tick = 37
      episode_state.last_sample_tick = 37
      episode_state.scenario.resource_patch.bounds = {x1=20,y1=20,x2=21,y2=21}
      storage.training_lab.report_sequence = 0
      local written, callback
      commands = {add_command=function(name, help, fn) callback=fn end}
      helpers = {
        json_to_table=function() return {version='1.1.0',request_id='test',episode_id='ep-1'} end,
        table_to_json=function(report) return report end,
        write_file=function(path, report) written=report end,
      }
      measurement.register_commands()
      game.tick = 37
      production['iron-plate'] = 0
      callback({parameter='mock'})
      assert(written.ok)
      for elapsed=1,599 do
        game.tick = 37 + elapsed
        production['iron-plate'] = math.floor(elapsed / 100)
        measurement.sample_all(game.tick)
      end
      game.tick = 637
      production['iron-plate'] = 6
      assert(episode_state.item_production_metrics.items['iron-plate'].window_ticks == 540)
      callback({parameter='mock'})
      assert(written.ok)
      assert(written.metrics.item_production.items['iron-plate'].window_ticks == 600)
      assert(written.metrics.item_production.items['iron-plate'].window_end_tick == 637)
      assert(episode_state.last_sample_tick == 637)
      callback({parameter='mock'})
      measurement.sample_all(637)
      assert(#episode_state.item_production_samples['iron-plate'].samples == 10)
    ''')
