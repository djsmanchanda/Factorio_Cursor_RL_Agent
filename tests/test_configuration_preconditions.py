# Path: tests/test_configuration_preconditions.py | Purpose: Verify atomic owned-configuration guards and the read-only protocol.
from __future__ import annotations

import copy
import json
from pathlib import Path
import shutil
import subprocess

import jsonschema
import pytest

from orchestrator.configuration_preconditions import supports_configuration_preconditions

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.fast

GUARD = {
    'unit_number': 10, 'recipe': 'pipe',
    'logistic_condition': {'signal': 'pipe', 'comparator': '<', 'constant': 20},
    'circuit_enabled': False,
    'requester': {'entity': 'requester-chest', 'position': {'x': 4, 'y': 1},
                  'unit_number': 11, 'groups': ['mall:a', 'mall:pipe']},
}
ACTION = {'action_type': 'configure_entity', 'entity': 'assembling-machine-2',
          'position': {'x': 0, 'y': 0}, 'configuration_preconditions': GUARD}


@pytest.mark.parametrize('response, expected', [
    ('{"configuration_preconditions":1}', True),
    ('\n{"configuration_preconditions":1}\n', True),
    ('{"configuration_preconditions":0}', False),
    ('{"configuration_preconditions":2}', False),
    ('{"configuration_preconditions":true}', None),
    ('{"configuration_preconditions":1.0}', None),
    ('{"configuration_preconditions":"1"}', None),
    ('{"configuration_preconditions":-1}', None),
    ('Unknown command: layout_executor_capabilities', None),
    ('{}', None), ('[]', None), ('null', None), ('', None),
    ('prefix {"configuration_preconditions":1}', None),
    ('{"configuration_preconditions":1} trailing', None),
    ('{"configuration_preconditions":0,"configuration_preconditions":1}', None),
    ('{"configuration_preconditions":1,"extra":NaN}', None),
])
def test_capability_probe(response, expected):
    class Client:
        def command(self, command):
            assert command == '/layout_executor_capabilities'
            return response
    assert supports_configuration_preconditions(Client()) is expected


def test_capability_transport_failure_is_unknown():
    class Client:
        def command(self, command):
            raise OSError('disconnected')
    assert supports_configuration_preconditions(Client()) is None


def validate(action):
    schema = json.loads((ROOT / 'schemas/build_plan.schema.json').read_text())
    jsonschema.Draft7Validator(schema).validate({'phases': [{'name': 'gate', 'actions': [action]}]})


def test_guard_schema_and_legacy():
    validate(ACTION)
    legacy = copy.deepcopy(ACTION)
    del legacy['configuration_preconditions']
    validate(legacy)


@pytest.mark.parametrize('field', list(GUARD))
def test_schema_requires_every_guard_field(field):
    action = copy.deepcopy(ACTION)
    del action['configuration_preconditions'][field]
    with pytest.raises(jsonschema.ValidationError):
        validate(action)


@pytest.mark.parametrize('kind', ['place_entity', 'place_ghost', 'place_tile_ghost', 'remove_entity'])
def test_schema_rejects_guard_on_other_action_kinds(kind):
    action = copy.deepcopy(ACTION)
    action['action_type'] = kind
    if kind == 'place_tile_ghost':
        action.pop('entity')
        action['tile'] = 'landfill'
    with pytest.raises(jsonschema.ValidationError):
        validate(action)


@pytest.mark.parametrize('path,value', [
    (('unit_number',), 0), (('unit_number',), True), (('unit_number',), 1.5),
    (('requester', 'unit_number'), -1), (('requester', 'entity'), 'buffer-chest'),
    (('requester', 'position'), {'x': 1}), (('requester', 'groups'), ['']),
    (('circuit_enabled',), 0), (('logistic_condition', 'constant'), 1.5),
    (('logistic_condition', 'second_signal'), 'copper-plate'),
    (('unexpected',), 1),
])
def test_schema_rejects_invalid_guard(path, value):
    action = copy.deepcopy(ACTION)
    target = action['configuration_preconditions']
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(jsonschema.ValidationError):
        validate(action)


HARNESS = r'''
registered = {}
commands = {add_command=function(name, _, callback) registered[name]=callback end}
helpers = {
  json_to_table=function() return payload end,
  table_to_json=function(value) report=value; return '{}' end,
  write_file=function() end,
}
game = {tick=1, print=function() error('unexpected game.print') end}
rcon = {print=function(value) printed=value end}
defines = {direction={north=0}, inventory={chest=1}}
local force = {name='player'}
local foreign = {name='other'}
behavior = {connect_to_logistic_network=true, circuit_enable_disable=false,
  logistic_condition={first_signal={type='item',name='pipe'},comparator='<',constant=20}}
sections = {sections={{valid=true, group='mall:pipe'}, {valid=true,group='mall:a'}}}
recipe = 'pipe'
mutations = 0
machine = {valid=true, name='assembling-machine-2',unit_number=10,force=force,position={x=0,y=0},
  type='assembling-machine',splitter_input_priority='none'}
function machine.get_recipe() return {name=recipe} end
function machine.get_control_behavior() return behavior end
function machine.get_or_create_control_behavior() mutations=mutations+1; return behavior end
function machine.set_recipe(value) mutations=mutations+1; recipe=value end
chest = {valid=true,name='requester-chest',unit_number=11,force=force,position={x=4,y=1}}
function chest.get_logistic_sections() return sections end
local surface = {}
function surface.create_entity() error('creation forbidden') end
function surface.find_entities_filtered() error('incidental wiring forbidden') end
package.preload['sandbox_shared'] = function() return {
  get_or_create_sandbox_surface=function() return surface end,
  get_or_create_planner_force=function() return force end,
  find_exact_entity=function(_, f, name, p)
    for _, e in ipairs({machine,chest}) do
      if e.valid and e.name==name and e.force==f and e.position.x==p.x and e.position.y==p.y then return e end
    end
  end,
} end
-- An electric-pole prototype deliberately detects unwanted tail recomputation.
prototypes = {entity={['assembling-machine-2']={type='electric-pole',get_max_wire_distance=function() return 10 end}}}
guard = {unit_number=10,recipe='pipe',logistic_condition={signal='pipe',comparator='<',constant=20},
  circuit_enabled=false,requester={entity='requester-chest',position={x=4,y=1},unit_number=11,groups={'mall:a','mall:pipe'}}}
action = {action_type='configure_entity',entity=machine.name,position={x=0,y=0},configuration_preconditions=guard,
  recipe='pipe',input_priority='left',clear_chest_gate=true,
  logistic_condition={signal='pipe',comparator='<',constant=50}}
payload = {authorization={approved_actions={'place_core_infrastructure','remove_entities','project_more_ghosts'}},
  build_plan={surface='nauvis',force='player',phases={{name='gate',actions={action}}}}}
function execute() registered.build_layout_plan({parameter='ignored'}) end
'''


def run_lua(tmp_path, body):
    lua = shutil.which('lua')
    if lua is None:
        pytest.skip('Lua interpreter required')
    script = tmp_path / 'configuration.lua'
    script.write_text(f'package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path\n'
                      + HARNESS + f'\ndofile("{ROOT}/factorio_mod/layout_executor.lua")\n' + body)
    result = subprocess.run([lua, str(script)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.parametrize('change,detail', [
    ('machine.unit_number=12', 'unit_number_mismatch'),
    ("recipe='copper-cable'", 'recipe_mismatch'),
    ('machine.valid=false', 'target_missing'),
    ('behavior.connect_to_logistic_network=false', 'logistic_disabled'),
    ('behavior.circuit_enable_disable=true', 'circuit_enabled_mismatch'),
    ('behavior.circuit_enable_disable=nil', 'circuit_enabled_mismatch'),
    ('behavior.logistic_condition.constant=21', 'logistic_condition_mismatch'),
    ("behavior.logistic_condition.first_signal.type='fluid'", 'logistic_condition_mismatch'),
    ("behavior.logistic_condition.first_signal.name='iron-plate'", 'logistic_condition_mismatch'),
    ("behavior.logistic_condition.comparator='>'", 'logistic_condition_mismatch'),
    ("behavior.logistic_condition.second_signal={type='item',name='iron-plate'}", 'logistic_condition_mismatch'),
    ("behavior.logistic_condition.first_signal.quality='legendary'", 'logistic_condition_mismatch'),
    ('chest.unit_number=12', 'requester_mismatch'),
    ('chest.position.x=4.01', 'requester_mismatch'),
    ("chest.force={name='other'}", 'requester_mismatch'),
    ("sections.sections[1].group='mall:other'", 'requester_groups_mismatch'),
    ('sections.sections[3]={valid=true,group="mall:extra"}', 'requester_groups_mismatch'),
    ('sections.sections[1].valid=false', 'requester_groups_unknown'),
    ('function chest.get_logistic_sections() error("read failure") end', 'read_failed'),
    ('function machine.get_control_behavior() error("read failure") end', 'read_failed'),
    ('function machine.get_recipe() error("read failure") end', 'read_failed'),
    ('guard.unit_number=0', 'invalid_shape'),
    ("guard.requester.groups={'mall:pipe','mall:a'}", 'invalid_groups'),
])
def test_stale_snapshot_fails_before_any_configuration(tmp_path, change, detail):
    run_lua(tmp_path, change + f'''
local original_recipe = recipe
local original_behavior = behavior
local original_gate = behavior.logistic_condition
execute()
assert(report.ok == false)
assert(report.error == 'configuration_precondition_failed')
assert(report.placement_failures[1].reason == 'configuration_precondition_failed:{detail}')
assert(mutations == 0)
assert(recipe == original_recipe)
assert(machine.splitter_input_priority == 'none')
assert(behavior == original_behavior and behavior.logistic_condition == original_gate)
''')


def test_guard_success_and_nested_factorio_condition(tmp_path):
    run_lua(tmp_path, '''
behavior.logistic_condition={condition=behavior.logistic_condition}
execute()
assert(report.ok == true, report.error)
assert(behavior.logistic_condition.constant == 50)
assert(machine.splitter_input_priority == 'left')
assert(recipe == 'pipe')
assert(report.wires_connected == 0)
''')


def test_guard_accepts_engine_item_signal_without_type(tmp_path):
    # Live Factorio 2.1.21 readback (2026-10-18 probe): first_signal={name="transport-belt"}, no type.
    run_lua(tmp_path, '''
behavior.logistic_condition.first_signal.type=nil
execute()
assert(report.ok == true, report.error)
assert(behavior.logistic_condition.constant == 50)
''')


def test_changed_after_python_snapshot_and_before_lua_write(tmp_path):
    run_lua(tmp_path, '''
-- The captured guard is the earlier Python snapshot; the first Lua preflight
-- still matches. A later read simulates a change before the immediate check.
local reads=0
function machine.get_control_behavior()
  reads=reads+1
  if reads==2 then behavior.logistic_condition.constant=99 end
  return behavior
end
execute()
assert(report.ok == false)
assert(report.placement_failures[1].reason == 'configuration_precondition_failed:logistic_condition_mismatch')
assert(behavior.logistic_condition.constant == 99)
assert(machine.splitter_input_priority == 'none')
assert(mutations == 0)
''')


def test_legacy_configure_keeps_semantics(tmp_path):
    run_lua(tmp_path, '''
action.configuration_preconditions=nil
prototypes.entity={}
execute()
assert(report.ok == true, report.error)
assert(behavior.logistic_condition.constant == 50)
assert(machine.splitter_input_priority == 'left')
''')


@pytest.mark.parametrize('kind', ['place_entity', 'place_ghost', 'place_tile_ghost', 'remove_entity'])
def test_executor_rejects_guard_on_wrong_action_kind(tmp_path, kind):
    run_lua(tmp_path, f'''
action.action_type='{kind}'
execute()
assert(report.ok == false)
assert(report.placement_failures[1].reason == 'configuration_precondition_failed:wrong_action_kind')
assert(mutations == 0 and machine.valid)
''')


def test_capability_command_is_read_only(tmp_path):
    run_lua(tmp_path, '''
registered.layout_executor_capabilities({})
assert(printed == '{"configuration_preconditions":1}')
assert(mutations == 0 and report == nil)
''')


def test_stale_guard_prevents_earlier_plan_mutation(tmp_path):
    run_lua(tmp_path, '''
local earlier={action_type='configure_entity',entity=machine.name,position={x=0,y=0},input_priority='right'}
payload.build_plan.phases[1].actions={earlier,action}
guard.unit_number=123
execute()
assert(report.ok == false)
assert(report.error == 'configuration_precondition_failed')
assert(machine.splitter_input_priority == 'none' and mutations == 0)
''')


def test_exact_group_identity_preserves_duplicates(tmp_path):
    run_lua(tmp_path, '''
sections.sections[3]={valid=true,group='mall:pipe'}
execute()
assert(report.ok == false)
assert(report.placement_failures[1].reason == 'configuration_precondition_failed:requester_groups_mismatch')
assert(mutations == 0)
''')
