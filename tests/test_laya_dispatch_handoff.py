# Path: tests/test_laya_dispatch_handoff.py | Purpose: Protect running jobs and cooling settings during an adaptive coordinator handoff.
import pytest
from tools import laya_dispatch_handoff as handoff
from tools import laya_host_scheduler as host
from tools.checkpoint_catalog import atomic_json

pytestmark = pytest.mark.fast


def setup_handoff(tmp_path):
    repo = tmp_path
    cohort = repo/'data/laya-experiments/cohort'
    spec = {'until':'2099-01-01T00:00:00+00:00'}
    atomic_json(cohort/'regression.json',spec)
    atomic_json(cohort/'freeze.json',{'spec_sha256':handoff.regression.sha(cohort/'regression.json')})
    atomic_json(cohort/'job-status.json',{'jobs':[{'status':'queued'}]})
    atomic_json(cohort.parent/'_host-scheduler-gate/job-status.json',{'controller':{'slots':0}})
    root = repo/'data/laya-host'
    host._set_config(root,mode='cool',dashboard_cool_token='cool-owner')
    host._atomic(root/'state.json',{'target':9,'mode':'cool'})
    path = root/'dispatch-handoff.json'
    handoff.arm(repo,cohort,'old.service','new.service',path)
    return root,cohort,path


@pytest.mark.parametrize('job',[
    {'status':'running','admission_reserved':True},
    {'status':'completed','admission_reserved':True},
    {'status':'completed','preparation_started':1,'cleanup_completed':False},
])
def test_handoff_never_stops_running_or_unclean_jobs(tmp_path,monkeypatch,job):
    root,cohort,path = setup_handoff(tmp_path)
    atomic_json(cohort/'job-status.json',{'jobs':[job,{'status':'queued'}]})
    monkeypatch.setattr(handoff,'command',lambda args:pytest.fail('Lifecycle action before drain'))
    assert handoff.tick(path)['phase']=='draining'
    assert host._load_config(root)['manual_target']==0


def test_drained_handoff_preserves_freeze_and_cooling_then_resumes_search(tmp_path,monkeypatch):
    root,cohort,path = setup_handoff(tmp_path)
    before = (cohort/'regression.json').read_bytes()
    calls=[]
    monkeypatch.setattr(handoff,'command',lambda args:calls.append(args))
    assert handoff.tick(path)['phase']=='complete'
    assert calls[0]==['systemctl','--user','stop','old.service']
    assert '--adaptive-workers' in calls[1]
    config = host._load_config(root)
    assert config['mode']=='cool' and config['dashboard_cool_token']=='cool-owner'
    assert config['manual_target'] is None and config['dispatch_handoff_token'] is None
    assert host._load_state(root)['target']==9
    assert (cohort/'regression.json').read_bytes()==before


def test_failed_replacement_resumes_original_and_releases_drain(tmp_path,monkeypatch):
    root,cohort,path = setup_handoff(tmp_path)
    calls=[]
    def command(args):
        calls.append(args)
        if args[0]=='systemd-run':
            raise RuntimeError('injected launch failure')
    monkeypatch.setattr(handoff,'command',command)
    with pytest.raises(RuntimeError,match='launch failure'):
        handoff.tick(path)
    assert ['systemctl','--user','start','old.service'] in calls
    assert host._load_config(root)['manual_target'] is None


def test_user_ceiling_supersedes_handoff(tmp_path,monkeypatch):
    root,cohort,path = setup_handoff(tmp_path)
    host._set_config(root,manual_target=4)
    monkeypatch.setattr(handoff,'command',lambda args:pytest.fail('Superseded request made lifecycle action'))
    assert handoff.tick(path)['phase']=='superseded'
    assert host._load_config(root)['manual_target']==4
