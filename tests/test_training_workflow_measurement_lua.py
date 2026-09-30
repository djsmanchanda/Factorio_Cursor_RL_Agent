# Path: tests/test_training_workflow_measurement_lua.py | Purpose: Verify simultaneous output, transient-item audits and branch-owned construction in Lua.

from tests.test_training_item_telemetry_lua import _run_lua


def test_separate_historical_output_successes_do_not_complete_joint_service():
    _run_lua("""
        local primary_items, secondary_items = 0, 0
        sink_inventory.get_item_count = function() return primary_items end
        sink_inventory.remove = function() primary_items = 0 end
        local secondary_inventory = {
          get_item_count=function() return secondary_items end,
          remove=function() secondary_items = 0 end,
        }
        local secondary_entity = {valid=true, name='infinity-chest', type='container',
          unit_number=2, position={x=3,y=0}, surface=training_surface,
          get_inventory=function() return secondary_inventory end}
        training_surface.find_entities_filtered = function(filter)
          if filter.area and filter.area[1][1] > 1 then return {secondary_entity} end
          return {sink_entity}
        end
        episode_state.fixtures.secondary = {kind='item_sink', name='infinity-chest',
          unit_number=2, position={x=3,y=0}}
        local objective = episode_state.scenario.objective
        objective.target_rate_per_tick = 2/60
        objective.sustain_ticks = 1200
        objective.requirements = {{id='secondary', item='copper-plate',
          destination_fixture_id='secondary', target_rate_per_tick=2/60, sustain_ticks=1200}}
        production['copper-plate'] = 0
        local measurement = require('episode_measurement')
        for step=1,30 do
          primary_items = 2
          measurement.sample_all(step*60)
        end
        assert(episode_state.requirement_state.primary.closed)
        assert(episode_state.status == 'running')
        for step=31,60 do
          secondary_items = 2
          measurement.sample_all(step*60)
        end
        assert(not episode_state.requirement_state.primary.closed)
        assert(episode_state.requirement_state.secondary.closed)
        assert(episode_state.status == 'running', 'past success cannot mask lost service')
        assert(episode_state.joint_sustained_ticks == 0)
        for step=61,90 do
          primary_items, secondary_items = 2, 2
          measurement.sample_all(step*60)
        end
        assert(episode_state.status == 'completed')
        assert(episode_state.joint_sustained_ticks >= 1200)
        assert(episode_state.sustained_ticks == episode_state.joint_sustained_ticks)
    """)


def test_dropped_items_are_not_infrastructure_but_illegal_entities_still_fail():
    _run_lua("""
        local dropped = {name='item-on-ground', type='item-entity', force={name='neutral'},
          position={x=100,y=100}}
        training_surface.find_entities = function() return {dropped} end
        local measurement = require('episode_measurement')
        local counts, forbidden, outside, overruns, efficiency = measurement.audit_entities(
          training_surface, training_force, episode_state, 60)
        assert(next(counts) == nil)
        assert(forbidden == 0 and outside == 0 and overruns == 0)
        assert(efficiency.ground_item_entities == 1)
        assert(efficiency.occupied_footprint_tiles == 0)
        local illegal = {name='unapproved', type='container', force=training_force,
          unit_number=9, position={x=0,y=0}}
        prototypes = {entity={}}
        training_surface.find_entities = function() return {dropped, illegal} end
        counts, forbidden, outside, overruns = measurement.audit_entities(
          training_surface, training_force, episode_state, 60)
        assert(counts.unapproved == 1 and forbidden == 1 and overruns == 1)
    """)


def test_phase_ownership_is_idempotent_and_does_not_transfer_on_retries():
    _run_lua("""
        local built, next_unit = {}, 100
        package.loaded.training_geometry = {fits=function() return true end}
        training_surface.find_entities_filtered = function(filter)
          local found = {}
          for _, entity in pairs(built) do
            if not filter.name or entity.name == filter.name then found[#found+1] = entity end
          end
          return found
        end
        training_surface.create_entity = function(details)
          next_unit = next_unit + 1
          local entity = {valid=true, name=details.name, type='furnace',
            force=training_force, surface=training_surface, unit_number=next_unit,
            position=details.position}
          built[#built+1] = entity
          return entity
        end
        episode_state.scenario.constraints.allowed_entities = {'electric-furnace'}
        episode_state.scenario.construction_budget = {['electric-furnace']=2}
        local current_package
        helpers = {json_to_table=function() return current_package end}
        local execution = require('plan_execution')
        local function execute(phase, x)
          current_package = {authorization={approved_actions={'place_core_infrastructure'}},
            build_plan={surface=episode_state.surface_name, force=episode_state.force_name,
              phases={{name=phase, actions={{action_type='place_entity',entity='electric-furnace',
                position={x=x,y=0}}}}}}}
          execution.upload({episode_id='ep-1',upload_id='upload',chunk_index=1,chunk_count=1,chunk='mock'})
          return execution.execute_plan({episode_id='ep-1',request_id='test',upload_id='upload',
            confirmation_token='EXECUTE_TRAINING_PLAN'})
        end
        assert(execute('connected_workflow.iron',0).ok)
        local retry = execute('connected_workflow.iron2',0)
        assert(retry.execution.already_present_placements == 1)
        assert(next(episode_state.phase_entities['connected_workflow.iron2']) == nil)
        assert(execute('connected_workflow.iron2',6).ok)
        assert(episode_state.phase_entities['connected_workflow.iron'][101] == built[1])
        assert(episode_state.phase_entities['connected_workflow.iron2'][102] == built[2])
        local oversized = {phases={}}
        for i=1,17 do
          oversized.phases[i] = {name='phase'..i, actions={{action_type='place_entity',
            entity='electric-furnace', position={x=0,y=0}}}}
        end
        assert(not pcall(function()
          execution.plan_actions(oversized, episode_state, training_surface, training_force)
        end))
        assert(#built == 2, 'an oversized ownership ledger must fail before execution')
    """)


def test_phase_report_counts_only_surviving_entities_in_the_episode_scope():
    _run_lua("""
        storage.training_lab.report_sequence = 0
        episode_state.scenario.resource_patch.bounds = {x1=20,y1=20,x2=21,y2=21}
        local owned = {valid=true, name='electric-furnace', type='furnace',
          surface=training_surface, force=training_force}
        local foreign = {valid=true, name='electric-furnace', type='furnace',
          surface={}, force=training_force}
        local ghost = {valid=true, name='entity-ghost', type='entity-ghost',
          surface=training_surface, force=training_force}
        episode_state.phase_entities = {['connected_workflow.iron2']={owned,foreign,ghost}}
        local written, callback
        commands = {add_command=function(name, help, fn) callback=fn end}
        helpers = {
          json_to_table=function() return {version='1.1.0',request_id='test',episode_id='ep-1'} end,
          table_to_json=function(report) return report end,
          write_file=function(path, report) written=report end,
        }
        require('episode_measurement').register_commands()
        callback({parameter='mock'})
        assert(written.ok)
        assert(written.metrics.live_entities_by_phase['connected_workflow.iron2']['electric-furnace'] == 1)
        owned.valid = false
        callback({parameter='mock'})
        assert(next(written.metrics.live_entities_by_phase['connected_workflow.iron2']) == nil)
    """)
