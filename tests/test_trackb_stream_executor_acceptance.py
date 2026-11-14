# Path: tests/test_trackb_stream_executor_acceptance.py | Purpose: Exercise stream submissions against both Lua executors offline.
import json
import importlib.util
import subprocess
from pathlib import Path

import pytest

from orchestrator.game_bridge import GameBridge
from training.laya_layout_stream import submit, recover
from orchestrator.game_bridge import LayoutCapabilityError, BridgeError
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


def executable_bridge(tmp_path, reference, *, outcome="normal", mutations=1, setup=""):
    executor = Path(__file__).parent / "fixtures/rc9_reference_69a2accc/layout_executor.lua" if reference else EXECUTOR
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    bridge._poll_interval = 0.001
    bridge._retain_reports = 200
    events = []
    def command(text):
        events.append(text.split(' ', 1)[0])
        if text == '/layout_executor_capabilities':
            script = 'package.path = ' + lua_value(str(EXECUTOR.parent / '?.lua') + ';') + ' .. package.path\n' + _HARNESS + f'''\nrcon = {{print=function(s) print(s) end}}\ndofile({lua_value(str(executor))})\nregistered.layout_executor_capabilities()\n'''
        else:
            raw = text.split(' ', 1)[1]
            payload = json.loads(raw)
            script = 'package.path = ' + lua_value(str(EXECUTOR.parent / '?.lua') + ';') + ' .. package.path\n' + _HARNESS + f'''
              storage = {{}}
              {setup}
              local function encode(v)
                local t = type(v)
                if t == 'string' then return string.format('%q', v) end
                if t ~= 'table' then return tostring(v) end
                local parts = {{}}
                if #v > 0 then
                  for _, x in ipairs(v) do parts[#parts+1] = encode(x) end
                  return '[' .. table.concat(parts, ',') .. ']'
                end
                for k, x in pairs(v) do parts[#parts+1] = encode(tostring(k)) .. ':' .. encode(x) end
                return '{{' .. table.concat(parts, ',') .. '}}'
              end
              helpers.table_to_json = encode
              helpers.write_file = function(path, data) print(path); print(data) end
              payload = {lua_value(payload)}
              dofile({lua_value(str(executor))})
              registered.build_layout_plan({{parameter={lua_value(raw)}}})
              registered.build_layout_plan({{parameter={lua_value(raw)}}})
              assert(#mutation_log == {mutations}, 'unexpected mutation count: ' .. #mutation_log)
              if {mutations} == 1 then assert(mutation_log[1] == 'create:transport-belt') end
            '''
        result = subprocess.run(['lua', '-'], input=script, text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        if text == '/layout_executor_capabilities':
            return result.stdout.strip()
        path, data, _, _ = result.stdout.strip().split('\n', 3)
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if outcome.startswith('mismatch:'):
            receipt = json.loads(data)
            field = outcome.split(':', 1)[1]
            receipt[field] = {'request_digest': '0' * 64, 'request_id': '0' * 32, 'stream_id': '0' * 16, 'sequence': 2}[field]
            data = json.dumps(receipt)
        if outcome != 'missing':
            target.write_text(data)
        if outcome != 'normal':
            raise TimeoutError('response lost after receipt')
        return ''
    bridge.command = command
    return bridge, events


@pytest.mark.parametrize("reference", [False, True])
@pytest.mark.parametrize("outcome", ["normal", "lost", "missing", "mismatch:request_digest",
                                      "mismatch:request_id", "mismatch:stream_id", "mismatch:sequence"])
def test_stream_submission_reaches_executor(tmp_path, reference, outcome):
    bridge, events = executable_bridge(tmp_path, reference, outcome=outcome)
    authorization = {'approved_actions': ['place_core_infrastructure'],
                     'scope_limits': {'surface': 'planner-sandbox', 'force': 'planner', 'max_count': 1}}
    plan = {'surface': 'planner-sandbox', 'force': 'planner', 'phases': [{'name': 'one', 'actions': [
        {'action_type': 'place_entity', 'entity': 'transport-belt', 'position': {'x': 0, 'y': 0}, 'direction': 'north'}]}]}

    def run():
        return submit(tmp_path, bridge, authorization, plan,
                      transaction='transaction-' + 'a' * 32 + '.json', prepared=lambda request: None)
    if outcome != 'normal':
        with pytest.raises(TimeoutError):
            run()
        if outcome == 'missing' or outcome.startswith('mismatch:'):
            with pytest.raises(BridgeError):
                recover(tmp_path, bridge)
            with pytest.raises(Exception, match='unknown outcome'):
                run()
            assert events == ['/layout_executor_capabilities', '/build_layout_plan']
            return
        _, report = recover(tmp_path, bridge)
    else:
        report = run()
    assert report['ok'] is True
    assert report['sequence'] == 1
    assert events == ['/layout_executor_capabilities', '/build_layout_plan']


@pytest.mark.parametrize("response", ["", "{}", "{bad", '{"correlated_identity":1,"correlated_identity":1}',
    '{"receipt_protocols":["layout-submission-receipt-v2"],"correlated_identity":true}'])
def test_capability_refusal_keeps_pending_and_never_builds(tmp_path, response):
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    events = []
    bridge.command = lambda text: events.append(text) or response
    plan = {"phases": [{"actions": [{"action_type": "place_entity"}]}]}
    with pytest.raises(LayoutCapabilityError):
        submit(tmp_path, bridge, {}, plan, transaction="transaction-" + "a" * 32 + ".json", prepared=lambda _: None)
    assert events == ["/layout_executor_capabilities"]
    with pytest.raises(Exception, match="refusal publication"):
        recover(tmp_path, bridge)
    assert json.loads((tmp_path / "evidence/layout-stream.json").read_text())["pending"] is not None

@pytest.mark.parametrize('reference,action', [
    (False, {'action_type': 'configure_entity', 'configuration_preconditions': {}}),
    (True, {'action_type': 'place_ghost_no_reconcile'}),
])
def test_real_executor_advertisement_refuses_unsupported_plan(tmp_path, reference, action):
    executor = Path(__file__).parent / 'fixtures/rc9_reference_69a2accc/layout_executor.lua' if reference else EXECUTOR
    script = 'package.path = ' + lua_value(str(EXECUTOR.parent / '?.lua') + ';') + ' .. package.path\n' + _HARNESS + f'''
      rcon = {{print=function(s) print(s) end}}
      dofile({lua_value(str(executor))})
      registered.layout_executor_capabilities()
    '''
    response = subprocess.run(['lua', '-'], input=script, text=True, capture_output=True, check=True).stdout.strip()
    bridge = GameBridge.__new__(GameBridge)
    bridge.script_output = tmp_path
    events = []
    bridge.command = lambda text: events.append(text) or response
    with pytest.raises(LayoutCapabilityError):
        submit(tmp_path, bridge, {}, {'phases': [{'actions': [action]}]},
               transaction='transaction-' + 'a' * 32 + '.json', prepared=lambda _: None)
    assert events == ['/layout_executor_capabilities']

@pytest.mark.parametrize('reference', [False, True])
def test_refusal_publication_recovers_transaction_then_allows_new_request(tmp_path, reference):
    from training.laya_saved_world import SavedWorldAdapter
    bridge, events = executable_bridge(tmp_path, reference)
    adapter = SavedWorldAdapter.__new__(SavedWorldAdapter)
    adapter.root = tmp_path
    transaction = 'transaction-' + 'a' * 32 + '.json'
    path = tmp_path / 'evidence' / transaction
    path.parent.mkdir()
    def prepared(request):
        path.write_text(json.dumps({'status': 'submitting', 'layout_request': request}))
    def interrupted(request, refusal):
        adapter._store_layout_refusal(request, refusal)
        raise RuntimeError('interrupted publication')
    action = ({'action_type': 'place_ghost_no_reconcile'} if reference else
              {'action_type': 'configure_entity', 'configuration_preconditions': {}})
    plan = {'phases': [{'actions': [action]}]}
    with pytest.raises(RuntimeError, match='interrupted'):
        submit(tmp_path, bridge, {}, plan, transaction=transaction, prepared=prepared, refused=interrupted)
    request, refusal = recover(tmp_path, bridge, refused=adapter._store_layout_refusal)
    assert path.exists() and json.loads(path.read_text())['layout_refusal'] == refusal
    assert refusal['request_digest'] and request['sequence'] == 1
    assert events == ['/layout_executor_capabilities']
    # Lua never saw the refused sequence, so the next request starts a fresh
    # stream at one and completes a real Lua build rather than skipping a number.
    legal = {'surface': 'planner-sandbox', 'force': 'planner', 'phases': [{'name': 'one', 'actions': [
        {'action_type': 'place_entity', 'entity': 'transport-belt', 'position': {'x': 0, 'y': 0}, 'direction': 'north'}]}]}
    next_request = []
    report = submit(tmp_path, bridge, {'approved_actions': ['place_core_infrastructure']}, legal,
                    transaction='transaction-' + 'b' * 32 + '.json', prepared=next_request.append)
    assert report['ok'] is True and report['sequence'] == 1
    assert next_request[0]['stream_id'] != request['stream_id']
    assert next_request[0]['request_id'] != request['request_id']
    assert json.loads(path.read_text())['status'] == 'submission_refused'
    assert events == ['/layout_executor_capabilities', '/layout_executor_capabilities', '/build_layout_plan']



@pytest.mark.parametrize('field', ['configuration_preconditions', 'optimizer_ownership', 'research_lab_ownership'])
def test_legacy_guard_and_ownership_fail_before_command(field):
    bridge = GameBridge.__new__(GameBridge)
    bridge._run_and_collect = lambda *args: pytest.fail('unexpected executor mutation')
    with pytest.raises(ValueError):
        bridge.build_layout({}, {'phases': [{'actions': [{'action_type': 'configure_entity', field: {}}]}]})


@pytest.mark.parametrize('reference', [False, True])
@pytest.mark.parametrize('guard', ['scope', 'configuration', 'optimizer_ownership'])
def test_stream_ownership_and_guards_reject_without_mutation(tmp_path, reference, guard):
    bridge, events = executable_bridge(tmp_path, reference, mutations=0,
                                      setup="add_entity('transport-belt', 0, 0)")
    action = {'action_type': 'place_entity', 'entity': 'transport-belt', 'position': {'x': 0, 'y': 0}}
    authorization = {'approved_actions': ['place_core_infrastructure', 'project_more_ghosts']}
    plan = {'surface': 'planner-sandbox', 'force': 'planner', 'atomic': True,
            'phases': [{'name': 'guarded', 'actions': [action]}]}
    if guard == 'scope':
        authorization['scope_limits'] = {'surface': 'different-surface', 'force': 'planner'}
    elif guard == 'configuration':
        action.update(action_type='configure_entity', configuration_preconditions={})
    else:
        action.update(action_type='place_ghost_no_reconcile', optimizer_ownership={
            'optimizer_episode': 'episode', 'optimizer_project': 'project',
            'optimizer_transaction': 'a' * 32, 'optimizer_plan_hash': 'b' * 64})
    refusals = []
    call = lambda: submit(tmp_path, bridge, authorization, plan,
                          transaction='transaction-' + 'a' * 32 + '.json', prepared=lambda _: None,
                          refused=lambda request, evidence: refusals.append((request, evidence)))
    if guard == 'scope':
        with pytest.raises(ValueError, match='scope differs'):
            call()
        assert events == []
    elif (guard == 'configuration' and not reference) or (guard == 'optimizer_ownership' and reference):
        with pytest.raises(LayoutCapabilityError):
            call()
        assert refusals and events == ['/layout_executor_capabilities']
    else:
        report = call()
        assert report['ok'] is False and report['error']
        assert events == ['/layout_executor_capabilities', '/build_layout_plan']
    assert recover(tmp_path, bridge) is None


@pytest.mark.parametrize('correlated', [False, True])
@pytest.mark.parametrize('limits', [
    {'surface': 'another', 'force': 'planner'}, {'surface': 'planner-sandbox'},
    {'max_count': 0}, {'max_count': True}, {'max_count': -1}, {'unexpected': 1}])
def test_authorization_bounds_fail_before_any_executor_command(limits, correlated):
    bridge = GameBridge.__new__(GameBridge)
    bridge.command = lambda *args: pytest.fail('scope refused before capability or build command')
    bridge._run_and_collect = lambda *args: pytest.fail('scope refused before legacy build command')
    plan = {'surface': 'planner-sandbox', 'force': 'planner', 'phases': [{'actions': [{'action_type': 'place_entity'}]}]}
    authorization = {'scope_limits': limits}
    with pytest.raises(ValueError):
        if correlated:
            bridge.build_layout_correlated(authorization, plan, request_id='a' * 32)
        else:
            bridge.build_layout(authorization, plan)
