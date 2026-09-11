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
        local destroyed = false
        local chest = {valid = true, force = {name = 'player'},
            destroy = function() destroyed = true end}
        local ok, reason = trash.revive_with_trash(surface, chest)
        assert(ok == false and reason == 'replacement_forbidden')
        assert(not destroyed and created == 0)
    """)
    result = subprocess.run([LUA, '-'], input='\n'.join(scripts), text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
