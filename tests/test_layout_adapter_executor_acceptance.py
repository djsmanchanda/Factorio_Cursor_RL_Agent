# Path: tests/test_layout_adapter_executor_acceptance.py | Purpose: Exercise generated adapter requests against the Lua executor API offline.
import json
import importlib.util
import subprocess
from pathlib import Path

import pytest

from orchestrator.game_bridge import GameBridge
spec = importlib.util.spec_from_file_location("atomic_fixture", Path(__file__).with_name("test_atomic_removal_preflight.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
_HARNESS, EXECUTOR = fixture._HARNESS, fixture.EXECUTOR

pytestmark = pytest.mark.fast


def lua_value(value):
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua_value(k) + ']=' + lua_value(v) for k, v in value.items()) + '}'
    if isinstance(value, list):
        return '{' + ','.join(map(lua_value, value)) + '}'
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if value is None:
        return 'nil'
    return str(value).lower()


@pytest.mark.parametrize('correlated', [False, True])
def test_adapter_submission_reaches_executor_and_receipt_parser(tmp_path, correlated):
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    bridge._poll_interval = 0.001
    bridge._retain_reports = 200
    events = []
    authorization = {'approved_actions': ['place_core_infrastructure']}
    plan = {'surface': 'planner-sandbox', 'force': 'planner', 'phases': [{'name': 'one', 'actions': [
        {'action_type': 'place_entity', 'entity': 'transport-belt', 'position': {'x': 0, 'y': 0}, 'direction': 'north'}]}]}

    def command(text):
        events.append(text.split(' ', 1)[0])
        if text == '/layout_executor_capabilities':
            script = _HARNESS + f'''\nrcon = {{print=function(s) print(s) end}}\ndofile({lua_value(str(EXECUTOR))})\nregistered.layout_executor_capabilities()\n'''
        else:
            raw = text.split(' ', 1)[1]
            payload = json.loads(raw)
            script = _HARNESS + f'''
              storage = {{}}
              local function encode(v)
                local t = type(v)
                if t == 'string' then return string.format('%q', v) end
                if t ~= 'table' then return tostring(v) end
                local parts = {{}}
                for k, x in pairs(v) do parts[#parts+1] = encode(tostring(k)) .. ':' .. encode(x) end
                return '{{' .. table.concat(parts, ',') .. '}}'
              end
              helpers.table_to_json = encode
              helpers.write_file = function(path, data) print(path); print(data) end
              payload = {lua_value(payload)}
              dofile({lua_value(str(EXECUTOR))})
              registered.build_layout_plan({{parameter={lua_value(raw)}}})
              assert(#mutation_log == 1 and mutation_log[1] == 'create:transport-belt')
            '''
        result = subprocess.run(['lua', '-'], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        if text == '/layout_executor_capabilities':
            return result.stdout.strip()
        path, data = result.stdout.strip().split('\n', 1)
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data)
        return ''
    bridge.command = command
    if correlated:
        stream = 'a' * 16
        report = bridge.build_layout_correlated(authorization, plan, request_id=stream+'0000000000000001', stream_id=stream, sequence=1)
        assert report['request_id'] == stream+'0000000000000001'
        assert events == ['/layout_executor_capabilities', '/build_layout_plan']
        assert (tmp_path / 'factorio_mod/layout_reports' / f"layout_{report['request_id']}.json").is_file()
    else:
        path = bridge.build_layout(authorization, plan)
        assert path.name == 'layout_101.json'
        assert json.loads(path.read_text())['ok'] is True
        assert events == ['/build_layout_plan']
