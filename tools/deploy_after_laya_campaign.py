#!/usr/bin/env python3
# Path: tools/deploy_after_laya_campaign.py | Purpose: Apply one explicitly authorized pinned mod deployment only after a campaign ends and shared consumers stop.
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import psutil


def consumers_present(server_root):
    for name in ['factorio.pid','factorio-stdin-keeper.pid']:
        p=server_root/name
        if p.exists():
            try:
                if psutil.pid_exists(int(p.read_text().strip())):return True
            except ValueError:return True
    for p in psutil.process_iter(['cmdline']):
        try:args=p.info['cmdline'] or []
        except psutil.Error:return True
        if str(server_root/'config.ini') in args or str(server_root/'mods') in args:return True
    return False


def verify_snapshot(snapshot, files):
    actual={str(p.relative_to(snapshot)) for folder in ['factorio_mod','factorio_training_lab'] for p in (snapshot/folder).rglob('*') if p.is_file()}
    expected={rel for rel in files if rel.startswith(('factorio_mod/','factorio_training_lab/'))}
    if actual!=expected:raise ValueError('pinned deployment mod file set changed')
    for rel,digest in files.items():
        path=(snapshot/rel).resolve()
        if not path.is_relative_to(snapshot.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('pinned deployment source changed: '+rel)


def run(root):
    cfg=json.loads((root/'request.json').read_text());snapshot=Path(cfg['snapshot']);server=Path(cfg['server_root'])
    actor=json.loads((Path(cfg['campaign'])/'suite.json').read_text())['actor_unit']
    def report(status,**fields):
        (root/'status.json').write_text(json.dumps({'status':status,'recorded_epoch':time.time(),'commit':cfg['commit'],**fields},indent=2)+'\n')
    while time.time()<cfg['deadline_epoch']:
        state=subprocess.run(['systemctl','--user','is-active',actor],capture_output=True,text=True).stdout.strip()
        ended=(Path(cfg['campaign'])/'DONE').exists() and state in {'inactive','failed','unknown'}
        if not ended:report('waiting_campaign',actor_unit=actor)
        elif consumers_present(server):report('waiting_shared_consumers',server_root=str(server))
        else:
            verify_snapshot(snapshot,cfg['files'])
            # The manager independently asserts stopped again. It never starts a server or changes a save.
            result=subprocess.run(['bash',str(snapshot/'scripts/manage_linux_deterministic_server.sh'),'deploy-if-required',
                    '--root',str(server),'--gui-mods',cfg['gui_mods']],cwd=snapshot,capture_output=True,text=True,timeout=120)
            (root/'deployment.log').write_text(result.stdout+result.stderr)
            if result.returncode:
                report('deployment_failed',returncode=result.returncode);return result.returncode
            for source,folder in [('factorio_mod','factorio_cursor_rl_agent'),('factorio_training_lab','factorio_training_lab')]:
                for p in (snapshot/source).rglob('*'):
                    if not p.is_file():continue
                    for dest in [server/'mods'/folder,Path(cfg['gui_mods'])/folder]:
                        target=dest/p.relative_to(snapshot/source)
                        if not target.exists() or p.read_bytes()!=target.read_bytes():raise RuntimeError('deployed mod copy mismatch')
            report('deployed',server_root=str(server),gui_mods=cfg['gui_mods'],server_restarted=False,
                   saves_modified=False,gui_reload_required=True,
                   evidence_limit='Recipe-switch operations observed; physical returned-item delivery still needs a live balance check.')
            return 0
        time.sleep(30)
    report('expired_without_deployment');return 0


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);a=p.parse_args()
    try:raise SystemExit(run(a.root.resolve()))
    except Exception as e:
        (a.root/'status.json').write_text(json.dumps({'status':'failed_safe','error':str(e)},indent=2)+'\n')
        raise
