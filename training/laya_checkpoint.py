# Path: training/laya_checkpoint.py | Purpose: Immutable, earned phase checkpoints for isolated continuation comparisons without grants.
from pathlib import Path
import json
import shutil
import time
from zipfile import is_zipfile

from core.local_time import local_stamp
from training.laya_saved_world import file_hash, verify_instance


def load_bundle(path):
    path=Path(path).resolve()
    meta=json.loads((path/'checkpoint.json').read_text())
    if meta.get('version')!='laya-earned-checkpoint-v1' or meta.get('production_authority') is not False:
        raise ValueError('not an earned disposable checkpoint')
    if meta.get('phases_reached')!=['C0','C1'] or meta.get('extra_items_granted')!=0:
        raise ValueError('checkpoint must earn C0/C1 without grants')
    for name,digest in meta['files'].items():
        p=(path/name).resolve()
        if not p.is_relative_to(path) or file_hash(p)!=digest:raise ValueError('checkpoint hash/scope mismatch')
    if 'source.zip' not in meta['files']:raise ValueError('checkpoint save missing')
    return meta


def export_bundle(adapter, path, summary):
    verify_instance(adapter.root)
    if summary.get('phases_reached')!=['C0','C1'] or summary.get('failures'):
        return None
    path=Path(path).resolve()
    experiments=Path(__file__).resolve().parents[1]/'data/laya-experiments'
    if not path.is_relative_to(experiments) or path.exists():raise ValueError('fresh experiment checkpoint path required')
    adapter.bridge.command('/sc game.tick_paused=true;game.ticks_to_run=0;rcon.print("checkpoint-paused")')
    facts=adapter.query()
    save=adapter.root/'server/saves/earned-checkpoint.zip'
    adapter.bridge.command('/server-save earned-checkpoint')
    deadline=time.monotonic()+30
    while not is_zipfile(save) and time.monotonic()<deadline:time.sleep(.1)
    if not is_zipfile(save):raise RuntimeError('checkpoint save did not complete')
    path.mkdir(parents=True)
    shutil.copy2(save,path/'source.zip')
    evidence=path/'evidence';evidence.mkdir()
    for p in (adapter.root/'evidence').glob('transaction-*.json'):shutil.copy2(p,evidence/p.name)
    for name in ['site-plan.json','latest-status.json','decision-states.jsonl']:
        p=adapter.root/'evidence'/name
        if p.exists():shutil.copy2(p,evidence/name)
    (path/'engine-state.json').write_text(json.dumps(facts,indent=2)+'\n')
    meta={'version':'laya-earned-checkpoint-v1','created_at':local_stamp(),'source_root':str(adapter.root),
          'source_sha256':adapter.manifest['source_sha256'],'production_authority':False,'extra_items_granted':0,
          'phases_reached':summary['phases_reached'],'summary':summary,'tick':facts['tick'],
          'files':{str(p.relative_to(path)):file_hash(p) for p in path.rglob('*') if p.is_file()}}
    (path/'checkpoint.json').write_text(json.dumps(meta,indent=2)+'\n')
    return meta


def restore_context(bundle, root):
    meta=load_bundle(bundle)
    target=Path(root)/'evidence';target.mkdir(exist_ok=True)
    # Preserve construction attribution; never copy the old run's decision journal into the new one.
    for p in (Path(bundle)/'evidence').glob('transaction-*.json'):shutil.copy2(p,target/p.name)
    p=Path(bundle)/'evidence/site-plan.json'
    if p.exists():shutil.copy2(p,target/p.name)
    (target/'resume-checkpoint.json').write_text(json.dumps(meta,indent=2)+'\n')
    return meta
