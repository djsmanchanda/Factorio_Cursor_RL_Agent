# Path: tests/test_campaign_pingpong.py | Purpose: Protect exact model/session/deadline handoffs on the campaign branch.
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tools import opencode_campaign_orchestrator as orch

pytestmark = pytest.mark.fast


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
        return json.dumps({'type':'text','part':{'text':('Evidence with cited useful-state stall. '*12)+'\nSYNTHESIS:\nhypothesis: unchanged\nconfidence: high'}})
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
