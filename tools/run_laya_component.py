#!/usr/bin/env python3
# Path: tools/run_laya_component.py | Purpose: Measured guided/exploratory circuit and plastic chain experiments with explicit fixture provenance.
from __future__ import annotations
import argparse
from copy import deepcopy
from collections import Counter
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from core.local_time import local_stamp
from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from training.laya_component_lab import ComponentWorld, component_spec
from training.laya_clean_runner import Settings, build_state, rolling_measurement, _training_record, _option_record
from training.laya_factory_status import compile_status
from training.laya_guided_policy import GuidedPolicy, load_preferences
from training.laya_power import internal_power_variant, POWERED
from training.laya_saved_world import owned_root,file_hash
from training.laya_variants import apply,load_variant
from tools.run_laya_saved_world import prepare,manager
from tools.run_ledger import record


def run(a):
    root=owned_root(a.root);started=local_stamp();start=time.monotonic();w=None;summary={'status':'error','fixture_funded':True};history=[];samples=[]
    spec=component_spec(a.family,transport=a.transport,producers=a.producers,consumers=a.consumers,input_share=a.input_share,delay_ticks=a.delay_ticks,
                        request_multiplier=a.request_multiplier)
    if root.exists():raise ValueError('fresh component root required')
    staging=root.parent/(root.name+'-mods');staging.mkdir(parents=True,exist_ok=False)
    shutil.copytree(a.mods_source/'factorio_cursor_rl_agent',staging/'factorio_cursor_rl_agent')
    shutil.copytree(a.mods_source/'factorio_training_lab',staging/'factorio_training_lab')
    # During development only: private copies receive the current reviewed lab helper.
    if a.current_lab:
        shutil.rmtree(staging/'factorio_training_lab');shutil.copytree(ROOT/'factorio_training_lab',staging/'factorio_training_lab')
    prepare(argparse.Namespace(root=root,source_save=a.source_save,mods_source=staging,game_port=a.game_port,rcon_port=a.rcon_port))
    evidence=root/'evidence';evidence.mkdir(exist_ok=True);journal=evidence/'decision-states.jsonl'
    settings,policy_opts,_=apply(load_variant(a.variant));policy=GuidedPolicy(a.seed,preferences=load_preferences(ROOT/settings.catalog),**policy_opts)
    done=set();receipts=[];pending=None
    try:
        w=ComponentWorld(root,spec);fixture=w.rpc('snapshot');initial_tick=fixture['tick']
        (evidence/'fixture.json').write_text(json.dumps(spec,indent=2)+'\n')
        manifest=json.loads((root/'manifest.json').read_text());manifest.update(authority='guided_exploration',fixture_funded=True,
          component_spec={k:v for k,v in spec.items() if k!='groups'},surface=w.surface,force=w.force,
          code_hashes={str(p.relative_to(ROOT)):file_hash(p) for p in [Path(__file__),ROOT/'training/laya_component_lab.py',ROOT/'training/laya_experiment_context.py']})
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        phase={'phase_id':'COMPONENT','name':a.family,'targets':{spec['recipe']:spec['target']},
               'scopes':('mission.goal','mission.decide','mission.assemble','mission.build','mission.transport','mission.power')}
        for index in range(50):
            w.reconcile();facts=w.query();samples.append((facts['tick'],facts['production'],facts['consumption']))
            measured=rolling_measurement(samples,7200)
            if len(done)==len(spec['groups']):break
            board=compile_status(facts,phase['targets'],measured)
            cs=[]
            for group,acts in spec['groups'].items():
                if group in done:continue
                plan={'surface':w.surface,'force':w.force,'atomic':True,'phases':[{'name':group,'actions':acts}]}
                c={'option_id':f'component.{group}','family':'assemble','product':spec['recipe'] if group=='transport' else group,
                   'summary':f'Build {group} stage ({len(acts)} funded entities)','plan':plan,'internal_power':True,'need_id':'item:'+group,'group':group,
                   'network':facts['ports'][0]['network'],'network_anchor':facts['ports'][0]['position']}
                if any(v['entity'] in POWERED for v in acts):c=internal_power_variant(c,facts)
                if c:cs.append(c)
            checked=w.query(cs)['validations'] if cs else {}
            (evidence/f'menu-{index}.json').write_text(json.dumps({'proposed':cs,'validations':checked},indent=2)+'\n')
            legal=[{**c,'bill':checked[c['option_id']]['bill']} for c in cs if not checked[c['option_id']].get('reason')]
            if not legal and facts['pending_ghosts']==0:
                raise RuntimeError('no legal component build: '+json.dumps(checked))
            legal.append({'option_id':'wait.300','family':'wait','summary':'Wait five game seconds','wait_ticks':300,'need_id':'wait'})
            menu={'options':legal,'needs':[{'need_id':c['need_id'],'options':[c['option_id']]} for c in legal],
                  'blocked':{c['option_id']:checked[c['option_id']].get('reason') for c in cs if checked[c['option_id']].get('reason')},'shortfalls':{},'goal_reserve':{}}
            state=deepcopy(build_state(facts,menu,board,phase,root.name,policy.model_ref,history,measured,settings))
            state['run'].update(surface=w.surface,force=w.force,bootstrap_profile='declared-component-fixture')
            for o in state['options']:o['scope'].update(surface=w.surface,force=w.force)
            state['observation_digest']['fixture_funded']=True;validate_state(state)
            choice=policy.choose(state,menu);distribution=choice.pop('distribution')
            selected=next(c for c in legal if c['option_id']==choice['option_id'])
            pending={'run_id':root.name,'index':index,'phase':'COMPONENT','authority':'guided_exploration','state':state,
                     'options':[_option_record(c) for c in legal],'distribution':distribution,'choice':choice,
                     'no_legal_build':not any(c['family']!='wait' for c in legal)}
            (evidence/f'decision-{index}-pending.json').write_text(json.dumps(pending)+'\n')
            receipt=w.execute(choice['option_id'],legal,300);receipts.append(receipt)
            _training_record(journal,**pending,outcome={'status':receipt['status'],'measurement':receipt['measurement']})
            if selected.get('group') and receipt['status']=='observed':done.add(selected['group'])
            pending=None;history.append({'decision_id':str(index),'tick':facts['tick'],'summary':selected['summary'],'outcome':'completed'})
        if len(done)!=len(spec['groups']):raise RuntimeError(f'component not commissioned; built {sorted(done)}')
        w.advance(18000);before=w.query();w.advance(7200);after=w.query()
        rate=(after['production'][spec['recipe']]-before['production'][spec['recipe']])/120
        delivered=(after['fixture']['delivered']-before['fixture']['delivered'])/120
        summary={'status':'measured','fixture_funded':True,'family':a.family,'transport':a.transport,'producers':a.producers,'consumers':a.consumers,
                 'input_share':a.input_share,'delay_ticks':a.delay_ticks,'request_multiplier':a.request_multiplier,'seed':a.seed,'variant':load_variant(a.variant)['variant_id'],
                 'production_per_s':rate,'delivered_per_s':delivered,'target_per_s':spec['target'],'target_met':rate>=.95*spec['target'] and delivered>=.95*spec['target'],
                 'window_ticks':7200,'construction_ticks':before['tick']-initial_tick-18000,'decisions':len(receipts),
                 'target_verified_ticks':after['tick']-initial_tick,
                 'first_output_observed_ticks':after['fixture'].get('first_output_observed_tick',after['tick'])-initial_tick
                      if after['fixture'].get('first_output_observed_tick') is not None else None,
                 'first_output_sampling_ticks':60,
                 'engine_production_delta':{n:after['production'][n]-before['production'].get(n,0) for n in after['production']},
                 'engine_consumption_delta':{n:after['consumption'][n]-before['consumption'].get(n,0) for n in after['consumption']},
                 'ghosts':after['pending_ghosts'],'before':before,'after':after,
                 'infrastructure_error':bool(after['pending_ghosts']) or any(m['status']=='no_power' for m in after['machines'])}
        print(json.dumps({k:v for k,v in summary.items() if k not in {'before','after'}}),flush=True)
    except Exception as e:
        if pending:_training_record(journal,**pending,outcome={'status':'failed','error':str(e)})
        summary.update(error=str(e),error_type=type(e).__name__);raise
    finally:
        (evidence/'component-result.json').write_text(json.dumps(summary,indent=2)+'\n')
        if w:
            try:w.bridge.command('/server-save component-final')
            finally:w.close()
        manager(root,'stop')
        record({'system':'rl-lab','run_id':root.name,'started':started,'ended':local_stamp(),'duration_s':round(time.monotonic()-start),
          'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'target':a.family+' component interaction',
          'highest_milestone':a.family if summary.get('target_met') else None,'milestones':[],'milestone_source':'engine_counters',
          'outcome':summary['status'],'failure_cause':summary.get('error'),'failure_code':summary.get('error_type'),'limiting_factor':None,
          'decisions':len(receipts),'log_file':None,'root':str(root),'experiment':a.experiment,'variant_id':load_variant(a.variant)['variant_id'],'fixture_funded':True})
    return 0

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);p.add_argument('--family',choices=['circuit','plastic'],required=True)
    p.add_argument('--transport',choices=['belt','bots'],default='belt');p.add_argument('--producers',type=int,default=3);p.add_argument('--consumers',type=int,default=2)
    p.add_argument('--input-share',type=float,default=1);p.add_argument('--delay-ticks',type=int,default=0);p.add_argument('--variant',type=Path);p.add_argument('--seed',type=int,default=1)
    p.add_argument('--request-multiplier',type=int,default=1)
    p.add_argument('--experiment',default='component-pilot');p.add_argument('--game-port',type=int,default=34320);p.add_argument('--rcon-port',type=int,default=27220)
    p.add_argument('--source-save',type=Path,default=ROOT/'saves/mod_playground.zip');p.add_argument('--mods-source',type=Path,default=Path.home()/'.local/share/factorio-rl/deterministic/mods')
    p.add_argument('--current-lab',action='store_true');a=p.parse_args();sys.exit(run(a))
