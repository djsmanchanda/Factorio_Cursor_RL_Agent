# Path: tests/test_blueprint_only_executor_lua.py
# Purpose: Execute the Lua command boundary and prove direct production construction fails before mutation.
from pathlib import Path

import pytest
import shutil
import subprocess

LUA = shutil.which("lua")
pytestmark = pytest.mark.skipif(LUA is None, reason="Lua interpreter unavailable")
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('limits,plan_scope,error', [
    ("{surface='training/a',force='training-a'}", "surface='nauvis',force='player'", "does not match authorization"),
    ("{surface='training/a',force='training-a'}", "surface='training/a'", "does not match authorization"),
    ("{surface='training/a'}", "surface='training/a',force='training-a'", "explicit surface and force"),
    ("{surface='',force='training-a'}", "surface='',force='training-a'", "explicit surface and force"),
    ("{max_count=0}", "surface='training/a',force='training-a'", "exceeds authorization max_count"),
    ("{max_count=-1}", "surface='training/a',force='training-a'", "non-negative integer"),
    ("{max_count=1.5}", "surface='training/a',force='training-a'", "non-negative integer"),
    ("{block_filter={'allowed'}}", "surface='training/a',force='training-a'", "Unsupported layout authorization scope limit"),
    ("{unknown=true}", "surface='training/a',force='training-a'", "Unsupported layout authorization scope limit"),
])
@pytest.mark.parametrize('atomic', [False, True])
def test_scope_limits_reject_before_world_access(limits, plan_scope, error, atomic):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        accessed=0
        package.loaded.sandbox_shared={
          get_or_create_sandbox_surface=function() accessed=accessed+1; error('unexpected access') end,
          get_or_create_planner_force=function() accessed=accessed+1; error('unexpected access') end}
        game={tick=1}; defines={direction={north=0}}
        commands={add_command=function(_, _, fn) execute=fn end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end, write_file=function() end}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append(f'''
        payload={{authorization={{approved_actions={{'project_more_ghosts'}},scope_limits={limits}}},
          build_plan={{{plan_scope},atomic={str(atomic).lower()},phases={{{{name='test',actions={{{{
            action_type='place_ghost',entity='transport-belt',position={{x=1.5,y=2.5}}
          }}}}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(result.ok == false)
        assert(string.find(result.error, '{error}', 1, true))
        assert(accessed == 0)
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_matching_scope_and_action_limit_can_reach_normal_execution():
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        accessed=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function(name)
          assert(name=='training/a'); accessed=accessed+1; error('scope-passed') end}
        game={tick=1}; defines={direction={north=0}}
        commands={add_command=function(_, _, fn) execute=fn end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end, write_file=function() end}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append('''
        local action={action_type='place_ghost',entity='transport-belt',position={x=1.5,y=2.5}}
        local plan={surface='training/a',force='training-a',phases={{name='test',actions={action}}}}
        local auth={approved_actions={'project_more_ghosts'},
          scope_limits={surface='training/a',force='training-a',max_count=1}}
        payload={authorization=auth,build_plan=plan}
        execute({parameter='{}'})
        assert(result.ok == false and string.find(result.error, 'scope-passed', 1, true))
        assert(accessed == 1)
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_action_limit_counts_all_phases_before_world_access():
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        accessed=0
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function()
          accessed=accessed+1; error('unexpected access') end}
        game={tick=1}; defines={direction={}}
        commands={add_command=function(_, _, fn) execute=fn end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end, write_file=function() end}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append('''
        local action={action_type='place_ghost',entity='transport-belt',position={x=1.5,y=2.5}}
        local plan={phases={{name='one',actions={action}},{name='two',actions={action}}}}
        payload={authorization={approved_actions={'project_more_ghosts'},scope_limits={max_count=1}},build_plan=plan}
        execute({parameter='{}'})
        assert(result.ok == false and string.find(result.error, 'exceeds authorization max_count', 1, true))
        assert(accessed == 0)
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('atomic', [False, True])
@pytest.mark.parametrize('bad_action,error', [
    ("{action_type='place_entity', entity='inserter', position={x=4.5,y=4.5}}", "Authorization does not permit"),
    ("{action_type='unknown', entity='inserter', position={x=4.5,y=4.5}}", "Unsupported build plan action"),
    ("{action_type='place_ghost', entity='inserter', position={x='bad',y=4.5}}", "finite numeric position"),
    ("{action_type='place_ghost', entity='inserter', position={x=math.huge,y=4.5}}", "finite numeric position"),
    ("{action_type='place_ghost', position={x=4.5,y=4.5}}", "requires entity name"),
    ("{action_type='place_ghost', entity='inserter', position={x=4.5,y=4.5}, direction='bad'}", "Unknown direction"),
])
def test_entire_plan_is_authorized_before_surface_or_force_access(atomic, bad_action, error):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        accessed=0; created=0
        local surface={name='nauvis', create_entity=function() created=created+1; return {valid=true} end}
        local force={name='player'}
        package.loaded.sandbox_shared={
          get_or_create_sandbox_surface=function() accessed=accessed+1; return surface end,
          get_or_create_planner_force=function() accessed=accessed+1; return force end}
        game={tick=1}; prototypes={entity={}}; defines={direction={north=0}}
        commands={add_command=function(_, _, fn) execute=fn end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end, write_file=function() end}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append(f'''
        payload={{authorization={{approved_actions={{'project_more_ghosts'}}}},
          build_plan={{surface='nauvis',force='player',atomic={str(atomic).lower()},phases={{{{name='test',actions={{
            {{action_type='place_ghost',entity='transport-belt',position={{x=1.5,y=2.5}}}},
            {bad_action}
          }}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(result.ok == false)
        assert(string.find(result.error, '{error}', 1, true))
        assert(accessed == 0 and created == 0)
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('atomic', [False, True])
@pytest.mark.parametrize('entity', ['inserter', 'passive-provider-chest', 'substation', 'assembling-machine-2'])
def test_player_direct_placement_rejected_before_other_actions(atomic, entity):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        created = 0
        surface = {name = 'nauvis', find_entities_filtered = function() return {} end,
            create_entity = function() created = created + 1; error('unexpected creation') end}
        game = {tick = 1, surfaces = {nauvis = surface}, forces = {player = {name = 'player'}}}
        commands = {add_command = function(name, help, fn) execute = fn end}
        helpers = {json_to_table = function() return payload end,
            table_to_json = function(report) result = report; return '{}' end,
            write_file = function() end}
        defines = {direction = {north = 0}}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append(f"atomic = {str(atomic).lower()}; entity_name = '{entity}'")
    scripts.append('''
        payload = {authorization = {approved_actions = {'project_more_ghosts', 'place_core_infrastructure'}},
            build_plan = {surface = 'nauvis', force = 'player', atomic = atomic, phases = {{name = 'test', actions = {
                {action_type = 'place_ghost', entity = 'transport-belt', position = {x = 2.5, y = 2.5}},
                {action_type = 'place_entity', entity = entity_name, position = {x = 10.5, y = 10.5}}
            }}}}}
        execute({parameter = '{}'})
    ''')
    scripts.append("""
        assert(result.ok == false)
        assert(result.error == 'direct_construction_forbidden')
        assert(result.placement_failures[1].reason == 'direct_construction_forbidden')
        assert(result.failed_entities == 1)
        assert(created == 0)
        local trash = require('trash_requesters')
        assert(trash.managed_force('player'))
        assert(not trash.managed_force('training-01'))
    """)
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('expected,success', [('42', True), ('43', False), ('nil', False), ('0', False)])
def test_guarded_configuration_checks_unit_number_before_mutation(expected, success):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        local target = {valid=true, name='splitter', type='splitter', unit_number=42,
            splitter_input_priority='none', position={x=2.5,y=2.5}}
        local surface = {name='nauvis'}
        local force = {name='player'}
        package.loaded.sandbox_shared = {
            get_or_create_sandbox_surface=function() return surface end,
            get_or_create_planner_force=function() return force end,
            find_exact_entity=function() return target end
        }
        game = {tick=1}
        commands = {add_command=function(_, _, fn) execute=fn end}
        helpers = {json_to_table=function() return payload end,
            table_to_json=function(report) result=report; return '{}' end,
            write_file=function() end}
        defines = {direction={}}
        prototypes = {entity={}}
        _G.target = target
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append(f'''
        payload = {{authorization={{approved_actions={{'place_core_infrastructure'}}}},
            build_plan={{surface='nauvis', force='player', phases={{{{name='guard', actions={{{{
                action_type='configure_entity_if_unit_number', entity='splitter',
                position={{x=2.5,y=2.5}}, input_priority='left', expected_unit_number={expected}
            }}}}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(result.ok == {str(success).lower()})
        assert(target.splitter_input_priority == '{'left' if success else 'none'}')
        if not result.ok then
            assert(result.placement_failures[1].reason == 'configure_target_unit_number_mismatch')
        end
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('action,success', [('place_ghost_no_reconcile', False), ('place_ghost', True)])
def test_nonreconciling_ghost_never_retargets_existing_inserter(action, success):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        local target = {valid=true, name='inserter', type='inserter', direction=4,
            position={x=2.5,y=2.5}}
        local surface = {name='nauvis', find_entities_filtered=function() return {} end}
        local force = {name='player'}
        package.loaded.sandbox_shared = {
            get_or_create_sandbox_surface=function() return surface end,
            get_or_create_planner_force=function() return force end,
            find_exact_entity=function() return target end
        }
        game = {tick=1}
        commands = {add_command=function(_, _, fn) execute=fn end}
        helpers = {json_to_table=function() return payload end,
            table_to_json=function(report) result=report; return '{}' end,
            write_file=function() end}
        defines = {direction={north=0}}
        prototypes = {entity={}}
        _G.target = target
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    scripts.append(f'''
        payload = {{authorization={{approved_actions={{'project_more_ghosts'}}}},
            build_plan={{surface='nauvis', force='player', phases={{{{name='guard', actions={{{{
                action_type='{action}', entity='inserter',
                position={{x=2.5,y=2.5}}, direction='north'
            }}}}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(result.ok == {str(success).lower()})
        assert(target.direction == {0 if success else 4})
        if not result.ok then assert(result.placement_failures[1].reason == 'direction_mismatch') end
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('valid', [True, False])
def test_lab_ghost_creation_passes_only_valid_transaction_tags(valid):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        created=0
        local surface={name='nauvis', find_entities_filtered=function() return {} end,
          create_entity=function(params) created=created+1; create_params=params; return {valid=true} end}
        local force={name='player'}
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function() return surface end,
          get_or_create_planner_force=function() return force end,
          find_exact_entity=function() return nil end}
        game={tick=1}; prototypes={entity={}}; defines={direction={}}
        commands={add_command=function(_, _, fn) execute=fn end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end,
          write_file=function() end}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    transaction = 'a' * 32 if valid else '../bad'
    scripts.append(f'''
        payload={{authorization={{approved_actions={{'project_more_ghosts'}}}},
          build_plan={{surface='nauvis',force='player',phases={{{{name='lab',actions={{{{
            action_type='place_ghost_no_reconcile',entity='lab',position={{x=1.5,y=2.5}},
            research_lab_ownership={{research_lab_episode='episode/1',research_lab_packet='packet',
              research_lab_transaction='{transaction}'}}
          }}}}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(result.ok == {str(valid).lower()})
        assert(created == {1 if valid else 0})
        if created == 1 then assert(create_params.tags.research_lab_transaction == '{transaction}') end
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('action,valid', [('place_ghost_no_reconcile', True), ('place_ghost', True),
                                         ('place_ghost_no_reconcile', False)])
def test_optimizer_tags_require_valid_nonreconciling_ghost(action, valid):
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path']
    scripts.append('''
        created=0
        local surface={name='nauvis',find_entities_filtered=function() return {} end,
          create_entity=function(params) created=created+1; create_params=params; return {valid=true} end}
        local force={name='player'}
        package.loaded.sandbox_shared={get_or_create_sandbox_surface=function() return surface end,
          get_or_create_planner_force=function() return force end,find_exact_entity=function() return nil end}
        game={tick=1}; prototypes={entity={}}; defines={direction={}}
        commands={add_command=function(name, help, fn) if name == 'build_layout_plan' then execute=fn end end}
        helpers={json_to_table=function() return payload end,
          table_to_json=function(report) result=report; return '{}' end,write_file=function() end}
    ''')
    scripts.append((ROOT / 'factorio_mod/layout_executor.lua').read_text())
    transaction = 'a' * 32 if valid else 'invalid'
    expected = valid and action == 'place_ghost_no_reconcile'
    scripts.append(f'''
        local tags={{optimizer_episode='episode/1',optimizer_project='project',
          optimizer_transaction='{transaction}',optimizer_plan_hash=string.rep('b',64)}}
        local action={{action_type='{action}',entity='lab',position={{x=1.5,y=2.5}},optimizer_ownership=tags}}
        payload={{authorization={{approved_actions={{'project_more_ghosts'}}}},
          build_plan={{surface='nauvis',force='player',phases={{{{name='test',actions={{action}}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(result.ok == {str(expected).lower()})
        assert(created == {1 if expected else 0})
        if created == 1 then assert(create_params.tags.optimizer_plan_hash == string.rep('b',64)) end
    ''')
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


_SHARED_CHEST_SETUP = '''
    created=0
    local function new_section(group)
        local s={valid=true,group=group,filters_count=0,slots={}}
        function s.clear_slot(i) s.slots[i]=nil end
        function s.set_slot(i, spec) s.slots[i]=spec; if i>s.filters_count then s.filters_count=i end end
        function s.get_slot(i) return s.slots[i] end
        return s
    end
    local holder={sections={new_section('laya-quad:east')}}
    holder.sections[1].set_slot(1,{value='iron-plate',min=2})
    function holder.add_section() local s=new_section(''); table.insert(holder.sections,s); return s end
    target={valid=true,name='requester-chest',type='logistic-container',unit_number=77,
        position={x=2.5,y=2.5},get_logistic_sections=function() return holder end}
    holder_ref=holder
    local surface={name='nauvis',find_entities_filtered=function() return {} end,
        create_entity=function() created=created+1; return {valid=true} end}
    local force={name='player'}
    package.loaded.sandbox_shared={get_or_create_sandbox_surface=function() return surface end,
        get_or_create_planner_force=function() return force end,find_exact_entity=function() return target end}
    game={tick=1}; defines={direction={}}
    prototypes={entity={['requester-chest']={collision_box={{-0.35,-0.35},{0.35,0.35}},collision_mask={layers={object=true}}}}}
    commands={add_command=function(name, help, fn) if name == 'build_layout_plan' then execute=fn end end}
    helpers={json_to_table=function() return payload end,
        table_to_json=function(report) result=report; return '{}' end,write_file=function() end}
'''


@pytest.mark.parametrize('tagged', [False, True])
def test_untagged_preexisting_shared_chest_is_present_but_tagged_one_is_rejected(tagged):
    tags = ("optimizer_ownership={optimizer_episode='episode/1',optimizer_project='project',"
            "optimizer_transaction=string.rep('a',32),optimizer_plan_hash=string.rep('b',64)},") if tagged else ""
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path', _SHARED_CHEST_SETUP,
               (ROOT / 'factorio_mod/layout_executor.lua').read_text(), f'''
        payload={{authorization={{approved_actions={{'project_more_ghosts'}}}},
          build_plan={{surface='nauvis',force='player',atomic=true,phases={{{{name='share',actions={{{{
            action_type='place_ghost_no_reconcile',entity='requester-chest',{tags}position={{x=2.5,y=2.5}}}}}}}}}}}}}}
        execute({{parameter='{{}}'}})
        assert(created==0)
        assert(result.ok=={str(not tagged).lower()})
        if {str(tagged).lower()} then assert(result.error=='optimizer_tagged_position_already_occupied') end
    ''']
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_guarded_configuration_of_shared_chest_keeps_the_earlier_slots_requests():
    scripts = [f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path', _SHARED_CHEST_SETUP,
               (ROOT / 'factorio_mod/layout_executor.lua').read_text(), '''
        payload={authorization={approved_actions={'place_core_infrastructure'}},
          build_plan={surface='nauvis',force='player',atomic=true,phases={{name='configure',actions={{
            action_type='configure_entity_if_unit_number',entity='requester-chest',expected_unit_number=77,
            position={x=2.5,y=2.5},
            logistic_sections={{group='laya-quad:west',multiplier=15,requests={{name='copper-plate',count=1}}}}}}}}}}
        execute({parameter='{}'})
        assert(result.ok==true)
        local sections=holder_ref.sections
        assert(#sections==2)
        assert(sections[1].group=='laya-quad:east' and sections[1].slots[1].value=='iron-plate' and sections[1].slots[1].min==2)
        assert(sections[2].group=='laya-quad:west' and sections[2].slots[1].value=='copper-plate')
    ''']
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
