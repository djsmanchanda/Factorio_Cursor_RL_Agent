# Path: tests/test_campaign_pingpong.py | Purpose: Protect exact model/session/deadline handoffs on the campaign branch.
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import opencode_campaign_orchestrator as orch

pytestmark = pytest.mark.fast


def test_run_notification_does_not_request_active_gameplay_review(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp

    c = replace(config(tmp_path), code_root=tmp_path / 'episode/code',
                world_root=tmp_path / 'active-world')
    c.code_root.parent.mkdir()
    (c.code_root.parent / 'freeze.json').write_text(json.dumps({'revision': 'frozen'}))
    state = orch.State(active_cycle=1, active_episode_id='owned-run',
                       primary_session_id='saved-deepseek', deadline_utc='deadline')
    messages = []
    def primary(_config, actual_state, message, _sequence):
        assert actual_state.primary_session_id == 'saved-deepseek'
        messages.append(message)
        return json.dumps({'type': 'text', 'part': {'text': 'Acknowledged; waiting for RUN END.'}})
    monkeypatch.setattr(orch, '_primary_turn', primary)
    pp.notify_run(c, state)
    pp.notify_run(c, state)
    assert len(messages) == 1
    assert 'Do not inspect gameplay evidence' in messages[0]
    assert str(c.world_root) not in messages[0]
    assert 'latest-context.md' not in messages[0]
    assert 'after natural end' in messages[0]
    record = pp.read(c.code_root.parent / 'deepseek-run-notification.json')
    assert record['delivered'] and record['session_id'] == 'saved-deepseek'


def config(tmp_path):
    args = orch._parser().parse_args([
        '--runtime', 'laya', '--agent-topology', 'deepseek-haiku',
        '--campaign-branch', 't3/laya-deepseek-haiku-6h',
        '--campaign-worktree', str(tmp_path), '--state-root', str(tmp_path/'campaign'),
        '--fixer-model', 'claude-haiku-5-5', '--fixer-variant', 'xhigh',
        '--observer-model', 'opencode-go/deepseek-v4.1-flash', '--observer-variant', 'high',
        '--game-port', '34988', '--rcon-port', '27988'])
    return orch._config(args)


def test_exact_topology_requires_explicit_cli_route_and_no_muse(tmp_path):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    assert c.model == pp.DEEPSEEK and c.variant == 'high'
    assert c.fixer_model == pp.HAIKU and c.fixer_variant == 'xhigh'
    assert c.observer_model == pp.DEEPSEEK and c.aspect_observers is False
    command = pp.haiku_command(c, 'handoff', None, tmp_path/'settings.json')
    assert command[command.index('--model')+1] == 'claude-haiku-5-5'
    assert command[command.index('--effort')+1] == 'xhigh'
    assert '--worktree' not in command and '--session-id' not in command
    resumed = pp.haiku_command(c, 'correction', 'real-returned-id', tmp_path/'settings.json')
    assert resumed[-2:] == ['--resume', 'real-returned-id']


def test_fresh_haiku_each_cycle_but_same_session_for_corrections_and_restart(tmp_path):
    c = config(tmp_path)
    state = orch.State(active_cycle=1, fixer_session_id='previous-cycle', fixer_cycle=0)
    assert orch._ensure_fixer(c, state) is None
    assert state.fixer_cycle == 1
    state.fixer_session_id = 'actual-haiku-1'
    orch._save_state(c.state_file, state)
    resumed = orch._load_state(c.state_file)
    assert orch._ensure_fixer(c, resumed) == 'actual-haiku-1'
    resumed.active_cycle = 2
    assert orch._ensure_fixer(c, resumed) is None
    assert resumed.fixer_cycle == 2


def test_missing_saved_deepseek_never_creates_a_replacement(tmp_path, monkeypatch):
    c = config(tmp_path)
    monkeypatch.setattr(orch, '_ask_with_continue', lambda *a, **kw: pytest.fail('new session'))
    with pytest.raises(RuntimeError, match='Saved Hi-bootstrap'):
        orch._ensure_primary(c, orch.State(), 1, 1)
    assert orch._ensure_primary(c, orch.State(primary_session_id='real-deepseek'), 1, 1) == ('real-deepseek', False)


def test_deadline_and_saved_session_are_immutable(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    deadline = (datetime.now(timezone.utc)+timedelta(hours=6)).isoformat()
    state = orch.State(deadline_utc=deadline, primary_session_id='real-deepseek')
    pp.atomic(c.state_root/'campaign.json', {'deadline_utc': deadline, 'deepseek_session_id': 'real-deepseek'})
    monkeypatch.setattr(orch, '_run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, c.campaign_branch+'\n', ''))
    pp.validate_campaign(c, state)
    with pytest.raises(RuntimeError, match='deadline changed'):
        pp.validate_campaign(c, replace(state, deadline_utc=(datetime.now(timezone.utc)+timedelta(hours=7)).isoformat()))
    with pytest.raises(RuntimeError, match='session drift'):
        pp.validate_campaign(c, replace(state, primary_session_id='replacement'))
    with pytest.raises(ValueError, match='exact'):
        pp.validate_campaign(replace(c, observer_model='opencode-go/muse-spark-1.3-contributor'), state)


def test_deadline_forbids_edits_or_commits_without_running_git(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    state = orch.State(deadline_utc=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    monkeypatch.setattr(orch, '_run', lambda *a, **kw: pytest.fail('git operation after D'))
    with pytest.raises(TimeoutError, match='observation only'):
        pp.require_edit_window(c, state)


def test_completion_packet_is_relayed_verbatim_to_saved_session_once(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    state = orch.State(primary_session_id='real-deepseek', fixer_session_id='actual-haiku-1')
    packet = 'Changes: facts\nTests: passed\nPrediction: fewer failures\nUnresolved: unknown\nVerification: passed'
    output = json.dumps({'type':'text','part':{'text':packet}})
    messages = []
    def send(c, s, msg, sequence):
        assert s.primary_session_id == 'real-deepseek'
        messages.append(msg)
        return json.dumps({'type':'text','part':{'text':'Acknowledged packet'}})
    monkeypatch.setattr(orch, '_primary_turn', send)
    pp.relay_completion(c, state, 'F0001', output)
    pp.relay_completion(c, state, 'F0001', output)
    assert len(messages) == 1 and messages[0].endswith(packet)
    record = next((c.state_root/'agents').glob('completion-*.json'))
    assert pp.read(record)['delivered'] is True
    assert pp.read(record)['to'] == 'real-deepseek'


def test_terminal_deadline_observation_uses_same_session_and_no_fix_plan(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    from tools import campaign_runtime as runtime
    c = replace(config(tmp_path), code_root=tmp_path/'episodes/one/code', world_root=tmp_path/'episodes/one/code/data/laya-saved-world/one')
    c.code_root.parent.mkdir(parents=True)
    s = orch.State(primary_session_id='real-deepseek', deadline_utc=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat())
    orch._save_state(c.state_file, s)
    monkeypatch.setattr(runtime, 'comparator_records', lambda root: [{'run_id':'one', 'summary':{'stop':'stalled'}}])
    messages = []
    def send(c, state, msg, sequence):
        assert state.primary_session_id == 'real-deepseek'
        messages.append(msg)
        return json.dumps({'type':'text','part':{'text':('Evidence with cited useful-state stall. '*12)+'\nSYNTHESIS:\nhypothesis: unchanged\nconfidence: high\nsupporting: status\ncontradicting: none\ncompeting: unknown\ndiscriminating: observe'}})
    monkeypatch.setattr(orch, '_primary_turn', send)
    result = pp.observe_terminal(c, 'one')
    assert result['status'] == 'complete' and result['observation_only'] is True
    assert 'NO subsequent fix' in messages[0]
    assert pp.observe_terminal(c, 'one') == result and len(messages) == 1


def test_native_runner_death_is_harness_failure_not_infinite_observation(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = replace(config(tmp_path), code_root=tmp_path/'episode/code')
    pp.atomic(c.code_root.parent/'lifecycle.json', {'pid':123, 'phase':'running'})
    monkeypatch.setattr(pp, 'process_alive', lambda pid: False)
    with pytest.raises(RuntimeError, match='without natural RUN END'):
        pp.detect_runner_failure(c)


def test_terminal_restart_cannot_start_another_episode(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    deadline = (datetime.now(timezone.utc)+timedelta(hours=6)).isoformat()
    s = orch.State(phase='SUCCESS', deadline_utc=deadline, primary_session_id='real-deepseek')
    orch._save_state(c.state_file, s)
    pp.atomic(c.state_root/'campaign.json', {'deadline_utc':deadline,'deepseek_session_id':'real-deepseek'})
    monkeypatch.setattr(orch, '_run', lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, c.campaign_branch, ''))
    monkeypatch.setattr(orch, '_backend', lambda *a: pytest.fail('new episode after terminal restart'))
    assert orch.run_campaign(c) == 0
    assert orch._load_state(c.state_file).phase == 'SUCCESS'


def test_terminal_markdown_report_requests_only_same_session_machine_envelope(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    from tools import campaign_runtime as runtime
    c = replace(config(tmp_path), code_root=tmp_path/'episode/code', world_root=tmp_path/'episode/code/data/laya-saved-world/one')
    c.code_root.parent.mkdir(parents=True)
    s = orch.State(primary_session_id='real-deepseek', deadline_utc=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat())
    orch._save_state(c.state_file, s)
    monkeypatch.setattr(runtime, 'comparator_records', lambda root: [{'run_id':'one','summary':{'stop':'stalled'}}])
    prose = 'Evidence: decision 701, useful-state stall, real C0 only. '*10 + '\n### SYNTHESIS\n- **Hypothesis**: supply\n- **Confidence**: high'
    envelope = 'SYNTHESIS:\nhypothesis: supply\nconfidence: high\nsupporting: decision701\ncontradicting: none\ncompeting: reach\ndiscriminating: menu'
    calls = []
    def send(c, state, message, sequence):
        calls.append((state.primary_session_id, message))
        return json.dumps({'type':'text','part':{'text':prose if len(calls)==1 else envelope}})
    monkeypatch.setattr(orch, '_primary_turn', send)
    result = pp.observe_terminal(c, 'one')
    assert result['status'] == 'complete'
    assert len(calls) == 2 and {sid for sid, _ in calls} == {'real-deepseek'}
    assert 'Do not re-investigate' in calls[1][1]
    assert (c.code_root.parent/'observer/returned-report.md').read_text() == prose
    assert Path(result['path']).read_text() == prose+'\n\n'+envelope


def test_terminal_partial_envelope_stays_rejected_after_one_same_session_correction(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    from tools import campaign_runtime as runtime
    c = replace(config(tmp_path), code_root=tmp_path/'episode/code', world_root=tmp_path/'episode/code/data/laya-saved-world/one')
    c.code_root.parent.mkdir(parents=True)
    orch._save_state(c.state_file, orch.State(primary_session_id='real-deepseek',deadline_utc=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat()))
    monkeypatch.setattr(runtime, 'comparator_records', lambda root: [{'run_id':'one','summary':{'stop':'stalled'}}])
    calls = []
    def send(c, state, message, sequence):
        calls.append(state.primary_session_id)
        return json.dumps({'type':'text','part':{'text':'Evidence: preserved bounded terminal report. '*10+'\nSYNTHESIS:\nhypothesis: supply'}})
    monkeypatch.setattr(orch, '_primary_turn', send)
    with pytest.raises(RuntimeError, match='incomplete terminal report'):
        pp.observe_terminal(c, 'one')
    assert calls == ['real-deepseek','real-deepseek']
    assert not (c.code_root.parent/'observer/findings.md').exists()
    assert (c.code_root.parent/'observer/returned-report.md').exists()


def test_planner_export_streams_to_file_and_preserves_last_valid_export_on_truncation(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    calls = []
    complete = json.dumps({'messages':[{'sessionID':'real-deepseek','text':'x'*70000}]})
    def run(command, **kwargs):
        calls.append(command)
        assert command[-2:] == ['real-deepseek','--sanitize']
        assert 'capture_output' not in kwargs
        kwargs['stdout'].write(complete if len(calls)==1 else complete[:65536])
        return subprocess.CompletedProcess(command, 0, None, '')
    monkeypatch.setattr(pp.subprocess, 'run', run)
    assert pp.export_planner(c, 'real-deepseek') is True
    target = c.state_root/'agents/deepseek-session-export.json'
    assert target.read_text() == complete and target.stat().st_size > 65536
    assert pp.export_planner(c, 'real-deepseek') is False
    assert target.read_text() == complete
    assert len(list(target.parent.glob('failed-export-*.json'))) == 1


def test_repaired_harness_resume_retains_history_but_gets_bounded_recovery_attempts(tmp_path, monkeypatch):
    from tools import campaign_pingpong as pp
    c = config(tmp_path)
    state = orch.State(phase='OBSERVING',primary_session_id='real-deepseek',deadline_utc=(datetime.now(timezone.utc)+timedelta(hours=4)).isoformat())
    orch._save_state(c.state_file,state)
    failures=c.state_root/'logs/harness-failures.jsonl'
    failures.write_text(('{}\n')*3)
    pp.atomic(c.state_root/'recovery.json', {'failures_before':3})
    monkeypatch.setattr(orch, '_config', lambda args:c)
    def interrupted(config):raise RuntimeError('new transport failure')
    monkeypatch.setattr(orch,'run_campaign',interrupted)
    monkeypatch.setattr(pp,'final_report',lambda *a,**k:pytest.fail('old failures stopped repaired harness immediately'))
    with pytest.raises(RuntimeError, match='new transport failure'):
        pp.main([])
    assert len(failures.read_text().splitlines()) == 4
    assert orch._load_state(c.state_file).deadline_utc == state.deadline_utc
    assert orch._load_state(c.state_file).phase == 'OBSERVING'
