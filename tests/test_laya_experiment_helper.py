# Path: tests/test_laya_experiment_helper.py | Purpose: Protect observer ownership, failure evidence, checkpoint acknowledgement and session recovery.
import json
import subprocess
from types import SimpleNamespace

import pytest

from tools import laya_experiment_helper as helper


def test_observer_lease_excludes_duplicate_processes_and_releases(tmp_path):
    with helper.exclusive(tmp_path, 'observer'):
        with pytest.raises(RuntimeError, match='Another observer'):
            with helper.exclusive(tmp_path, 'observer'):
                pytest.fail('duplicate observer acquired ownership')
    with helper.exclusive(tmp_path, 'observer'):
        pass


def test_failed_reviews_keep_transcripts_and_fresh_session_keeps_provenance(tmp_path, monkeypatch):
    work=tmp_path/'helper';work.mkdir()
    (work/'state.json').write_text(json.dumps({'session_id':'ses_old','checkpoints':37,'session_start_checkpoint':37}))
    (work/'opencode-038.jsonl').write_text('original failure')
    monkeypatch.setattr(helper,'packet',lambda root:'cumulative evidence')
    results=iter([SimpleNamespace(returncode=1,stdout='new failure',stderr=''),
                  SimpleNamespace(returncode=0,stdout=json.dumps({'type':'text','sessionID':'ses_new','part':{'text':'Recovered findings'}}),stderr='')])
    commands=[]
    def run(cmd,**kwargs):
        commands.append(cmd)
        return next(results)
    monkeypatch.setattr(helper.subprocess,'run',run)
    with pytest.raises(RuntimeError,match='incomplete'):
        helper.review(tmp_path)
    assert (work/'opencode-038.jsonl').read_text()=='original failure'
    assert (work/'opencode-039.jsonl').read_text()=='new failure'
    helper.review(tmp_path,'recovery',fresh_session=True)
    state=json.loads((work/'state.json').read_text())
    assert state['checkpoints']==38 and state['attempts']==40
    assert state['session_id']=='ses_new' and state['previous_sessions'][0]['session_id']=='ses_old'
    assert '--session' in commands[0] and '--session' not in commands[1]
    assert 'last_failure' not in state


def test_failed_checkpoint_is_not_acknowledged_or_retried_in_a_tight_loop(tmp_path, monkeypatch):
    work=tmp_path/'helper';work.mkdir()
    request=work/'review-request-2h';request.touch()
    (tmp_path/'suite.json').write_text(json.dumps({'deadline_epoch':20}))
    clock=[0];calls=[]
    monkeypatch.setattr(helper.time,'time',lambda:clock[0])
    monkeypatch.setattr(helper.time,'sleep',lambda duration:clock.__setitem__(0,clock[0]+duration))
    def fail(*args,**kwargs):
        calls.append(clock[0])
        raise RuntimeError('provider failure')
    monkeypatch.setattr(helper,'review',fail)
    helper.observe(tmp_path,interval=10)
    assert calls==[0,10] and request.exists()
    assert not (work/'reviewed-2h').exists()


def test_timeout_preserves_partial_provider_output(tmp_path, monkeypatch):
    monkeypatch.setattr(helper,'packet',lambda root:'evidence')
    def timeout(*args,**kwargs):
        raise subprocess.TimeoutExpired('opencode',2,output=b'partial evidence',stderr=b'timed out')
    monkeypatch.setattr(helper.subprocess,'run',timeout)
    with pytest.raises(subprocess.TimeoutExpired):helper.review(tmp_path,timeout=2)
    state=json.loads((tmp_path/'helper/state.json').read_text())
    assert state['last_failure']['reason']=='timeout'
    assert (tmp_path/'helper/opencode-001.jsonl').read_text()=='partial evidencetimed out'


def test_long_campaign_rotates_session_without_losing_cumulative_findings(tmp_path,monkeypatch):
    work=tmp_path/'helper';work.mkdir()
    (work/'state.json').write_text(json.dumps({'session_id':'ses_old','checkpoints':8,'session_start_checkpoint':0}))
    (work/'findings.md').write_text('previous cumulative findings')
    seen=[]
    def run(cmd,**kwargs):
        seen.append(cmd)
        return SimpleNamespace(returncode=0,stdout=json.dumps({'type':'text','sessionID':'ses_rotated','part':{'text':'continued review'}}),stderr='')
    monkeypatch.setattr(helper.subprocess,'run',run)
    helper.review(tmp_path)
    state=json.loads((work/'state.json').read_text())
    assert '--session' not in seen[0] and state['session_start_checkpoint']==8
    assert state['previous_sessions'][0]['reason']=='bounded eight-review session rotation'
    assert 'previous cumulative findings' in (work/'packet-001.md').read_text()
