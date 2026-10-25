# Path: tests/test_blueprint_only_executor_lua.py
# Purpose: Execute the Lua command boundary and prove direct production construction fails before mutation.
from pathlib import Path

import pytest
import shutil
import subprocess

LUA = shutil.which("lua")
pytestmark = pytest.mark.skipif(LUA is None, reason="Lua interpreter unavailable")
ROOT = Path(__file__).resolve().parents[1]


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


@pytest.mark.parametrize('kind',['configure_entity','place_ghost'])
def test_physical_mirroring_reconfigures_an_existing_machine_without_replacement(kind):
    script=f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n'
    script+='''
local actor={valid=true,name='oil-refinery',type='assembling-machine',direction=0,
 position={x=10.5,y=20.5},force={name='player'},mirroring=false}
prototypes={entity={['oil-refinery']={type='assembling-machine'}}}
local created=0
surface={name='nauvis',find_entities_filtered=function(opts)
 if opts.type=='entity-ghost' then return {} end
 if opts.name=='oil-refinery' then return {actor} end;return {} end,
 create_entity=function() created=created+1;error('unexpected replacement') end}
game={tick=1,surfaces={nauvis=surface},forces={player=actor.force}}
commands={add_command=function(name,help,fn) execute=fn end}
helpers={json_to_table=function() return payload end,
 table_to_json=function(report) result=report;return '{}' end,write_file=function() end}
defines={direction={north=0}}
'''
    script+=(ROOT/'factorio_mod/layout_executor.lua').read_text()
    script+=f"\nlocal kind='{kind}'\n"
    script+='''
payload={authorization={approved_actions={'project_more_ghosts','place_core_infrastructure'}},
 build_plan={surface='nauvis',force='player',atomic=false,phases={{name='settings',actions={
 {action_type=kind,entity='oil-refinery',position={x=10.5,y=20.5},mirroring=true}
 }}}}}
execute({parameter='{}'})
assert(result.ok, result.error or (result.placement_failures[1] and result.placement_failures[1].reason))
assert(actor.mirroring==true and created==0)
assert(actor.position.x==10.5 and actor.position.y==20.5)
'''
    result=subprocess.run([LUA,'-'],input=script,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
