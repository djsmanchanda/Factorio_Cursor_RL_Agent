# Path: tests/test_laya_deferred_deploy.py | Purpose: Prevent deployment into live consumers or from changed sources.
from types import SimpleNamespace
import hashlib
import json
import pytest
from tools import deploy_after_laya_campaign as deploy


def test_live_or_reused_pid_blocks_the_manager_including_its_fifo_keeper(tmp_path,monkeypatch):
    (tmp_path/'factorio-stdin-keeper.pid').write_text('123')
    monkeypatch.setattr(deploy.psutil,'pid_exists',lambda pid:pid==123)
    assert deploy.consumers_present(tmp_path)


def test_live_mod_consumer_without_pid_file_blocks_deployment(tmp_path,monkeypatch):
    monkeypatch.setattr(deploy.psutil,'process_iter',lambda attrs:[SimpleNamespace(info={'cmdline':['factorio','--mod-directory',str(tmp_path/'mods')]})])
    assert deploy.consumers_present(tmp_path)
    monkeypatch.setattr(deploy.psutil,'process_iter',lambda attrs:[])
    assert not deploy.consumers_present(tmp_path)


def test_deployment_refuses_changed_or_out_of_scope_snapshot(tmp_path):
    p=tmp_path/'mod.lua';p.write_text('verified code')
    digest=hashlib.sha256(p.read_bytes()).hexdigest();deploy.verify_snapshot(tmp_path,{'mod.lua':digest})
    p.write_text('unreviewed change')
    with pytest.raises(ValueError):deploy.verify_snapshot(tmp_path,{'mod.lua':digest})
    with pytest.raises(ValueError):deploy.verify_snapshot(tmp_path,{'../other.lua':digest})


def test_all_campaigns_require_terminal_evidence_and_inactive_actor(tmp_path,monkeypatch):
    campaigns=[tmp_path/'old',tmp_path/'followup']
    for p in campaigns:
        p.mkdir();(p/'suite.json').write_text(json.dumps({'actor_unit':p.name}))
    (campaigns[0]/'DONE').touch()
    states={'old':'inactive','followup':'active'}
    monkeypatch.setattr(deploy.subprocess,'run',lambda args,**kw:SimpleNamespace(stdout=states[args[-1]]))
    assert len(deploy.pending_campaigns(campaigns))==1
    (campaigns[1]/'DONE').touch()
    assert len(deploy.pending_campaigns(campaigns))==1
    states['followup']='inactive'
    assert deploy.pending_campaigns(campaigns)==[]
