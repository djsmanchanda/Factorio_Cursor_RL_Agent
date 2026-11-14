# Path: tests/test_layout_executor_contract.py | Purpose: Exercise negotiated layout receipts and executor replay offline.
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from orchestrator.game_bridge import BridgeError, GameBridge

ROOT = Path(__file__).resolve().parents[1]
LUA = shutil.which('lua')
pytestmark = pytest.mark.fast


def test_lua_capabilities_and_duplicate_replay():
    if not LUA:
        pytest.skip('Lua unavailable')
    script = f'''package.path = "{ROOT}/factorio_mod/?.lua;" .. package.path
    handlers = {{}}; commands = {{add_command=function(n, h, f) handlers[n]=f end}}
    rcon = {{print=function(s) capability=s end}}
    storage = {{}}; writes = 0
    game = {{tick=1, surfaces={{nauvis={{name='nauvis'}}}}, forces={{player={{name='player'}}}}, print=function(s) message=s end}}
    defines = {{direction={{north=0}}}}
    helpers = {{json_to_table=function() return payload end,
      table_to_json=function(r) result=r; return '{{}}' end,
      write_file=function() writes=writes+1 end}}
    dofile('{ROOT}/factorio_mod/layout_executor.lua')
    handlers.layout_executor_capabilities()
    print(capability)
    payload={{authorization={{approved_actions={{}}}}, build_plan={{surface='nauvis', force='player', phases={{}}}},
      request_id=string.rep('a',16)..'0000000000000001', request_digest=string.rep('b',64),
      stream_id=string.rep('a',16), sequence=1}}
    handlers.build_layout_plan({{parameter='same'}})
    assert(result.ok and result.sequence == 1 and writes == 1)
    handlers.build_layout_plan({{parameter='same'}})
    assert(result.ok and writes == 2)
    handlers.build_layout_plan({{parameter='different'}})
    assert(writes == 2 and message:find('conflicts'))
    payload.sequence=2; payload.request_id=string.rep('a',16)..'0000000000000002'
    handlers.build_layout_plan({{parameter='second'}})
    assert(writes == 3)
    payload.sequence=1; payload.request_id=string.rep('a',16)..'0000000000000001'
    handlers.build_layout_plan({{parameter='same'}})
    assert(writes == 3 and message:find('stale'))
    '''
    result = subprocess.run([LUA, '-'], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    caps = json.loads(result.stdout.strip())
    assert caps['receipt_protocols'] == ['legacy-tick-v1', 'layout-submission-receipt-v1', 'layout-submission-receipt-v2']
    assert caps['actions'] == ['place_entity', 'place_ghost', 'place_tile_ghost', 'configure_entity', 'remove_entity']
    assert caps['configuration_preconditions'] == caps['idempotent_requests'] == 1


CAPS = {'receipt_protocols': ['legacy-tick-v1', 'layout-submission-receipt-v1', 'layout-submission-receipt-v2'],
        'actions': ['place_ghost', 'configure_entity'], 'ownership': 'force-surface-v1',
        'configuration_preconditions': 1, 'correlated_identity': 1, 'idempotent_requests': 1}
PLAN = {'phases': [{'name': 'one', 'actions': [{'action_type': 'place_ghost', 'entity': 'transport-belt', 'position': {'x': 1, 'y': 2}}]}]}


def test_adapter_correlated_roundtrip_and_legacy(tmp_path):
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    commands = []
    bridge.command = lambda command: json.dumps(CAPS)
    def collect(command, subdir, timeout, **kwargs):
        commands.append(command)
        payload = json.loads(command.split(' ', 1)[1])
        path = tmp_path / 'receipt.json'
        if 'request_id' in payload:
            report = {'request_id': payload['request_id'], 'request_digest': payload['request_digest'],
                      'receipt_version': 'layout-submission-receipt-v2', 'stream_id': payload['stream_id'],
                      'sequence': payload['sequence'], 'tick': 1, 'ok': True, 'attempted_placements': 1,
                      'succeeded_placements': 1, 'already_present_placements': 0, 'failed_placements': 0,
                      'placement_failures': []}
            path.write_text(json.dumps(report))
        return path
    bridge._run_and_collect = collect
    stream = 'a' * 16
    report = bridge.build_layout_correlated({}, PLAN, request_id=stream+'0000000000000001', stream_id=stream, sequence=1)
    assert report['sequence'] == 1
    bridge.build_layout({}, PLAN)
    assert len(commands) == 2
    report['sequence'] = 2
    with pytest.raises(BridgeError):
        bridge._validate_layout_receipt(report, stream+'0000000000000001', report['request_digest'], 'layout-submission-receipt-v2', 1, stream, 1)
    report['sequence'] = 1
    report['request_id'] = 'f' * 32
    with pytest.raises(BridgeError):
        bridge._validate_layout_receipt(report, stream+'0000000000000001', report['request_digest'], 'layout-submission-receipt-v2', 1, stream, 1)


@pytest.mark.parametrize('caps', [None, {}, {**CAPS, 'actions': []}, {**CAPS, 'idempotent_requests': 0}, {**CAPS, 'ownership': 'unknown'}])
def test_incompatible_before_submission(caps):
    bridge = GameBridge.__new__(GameBridge)
    bridge.command = lambda _: json.dumps(caps)
    bridge._run_and_collect = lambda *a, **k: pytest.fail('submitted incompatible plan')
    with pytest.raises(BridgeError):
        bridge.build_layout_correlated({}, PLAN, request_id='a'*32)


def test_guard_and_ownership_cannot_bypass_preflight():
    bridge = GameBridge.__new__(GameBridge)
    bridge.command = lambda _: '{}'
    bridge._run_and_collect = lambda *a, **k: pytest.fail('submitted guarded plan')
    plan = {'phases': [{'actions': [{'action_type': 'configure_entity', 'configuration_preconditions': {}}]}]}
    with pytest.raises(BridgeError):
        bridge.build_layout({}, plan)
    with pytest.raises(BridgeError):
        bridge.build_layout({}, {**PLAN, 'optimizer_ownership': {}})


@pytest.mark.parametrize('corruption', ['duplicate', 'nonfinite'])
def test_receipt_recovery_uses_the_same_strict_parser_as_submission(tmp_path, corruption):
    from orchestrator.game_bridge import LAYOUT_REPORT_SUBDIR
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    request_id = 'a' * 32
    _, digest, protocol = bridge._layout_request({}, PLAN, request_id, None, None)
    report = {'request_id': request_id, 'request_digest': digest, 'receipt_version': protocol,
              'tick': 1, 'ok': True, 'attempted_placements': 1, 'succeeded_placements': 1,
              'already_present_placements': 0, 'failed_placements': 0, 'placement_failures': []}
    directory = tmp_path/LAYOUT_REPORT_SUBDIR
    directory.mkdir(parents=True)
    path = directory/f'layout_{request_id}.json'
    path.write_text(json.dumps(report))
    assert bridge.read_layout_receipt({}, PLAN, request_id=request_id) == report
    prefix = '"ok":false,' if corruption == 'duplicate' else '"unexpected":NaN,'
    path.write_text('{' + prefix + json.dumps(report)[1:])
    with pytest.raises(BridgeError, match='outcome unknown'):
        bridge.read_layout_receipt({}, PLAN, request_id=request_id)
