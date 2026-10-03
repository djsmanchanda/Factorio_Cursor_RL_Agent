#!/usr/bin/env python3
# Path: tools/run_laya_smelting_test.py | Purpose: Bot-built, fixture-accounted smelting capacity test on an exclusive disposable lab host.
from __future__ import annotations
import argparse
from copy import deepcopy
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from core.local_time import local_stamp
from orchestrator.game_bridge import load_json
from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from planners.sandbox_infrastructure import build_layout_authorization
from training.laya_power import internal_power_variant
from training.laya_saved_world import SavedWorldAdapter, file_hash, owned_root, verify_instance
from training.laya_smelting_array import bill, checked_plan, headers, measurement, serial_line, validate_union
from tools.run_laya_saved_world import prepare, manager
from tools.run_ledger import record

class Lab:
    def __init__(self,root):
        self.root=root;self.host=SavedWorldAdapter(root);self.bridge=self.host.bridge
        self.manifest=verify_instance(root);self.surface='training/smelting-'+root.name;self.force='training-smelting-'+root.name
    def rpc(self,op,**fields):
        verify_instance(self.root)
        name=f'smelting-fixture/{uuid.uuid4().hex}.json'
        self.bridge.command('/rl_smelting_fixture '+json.dumps({'op':op,'report':name,**fields},separators=(',',':')))
        r=load_json(self.bridge.script_output/name)
        if not r['ok']: raise RuntimeError(r.get('error'))
        return r['value']
    def advance(self,ticks):
        verify_instance(self.root);start=self.rpc('snapshot')['tick']
        self.bridge.command(f'/sc game.tick_paused=true;game.speed=16;game.ticks_to_run={ticks}')
        deadline=time.monotonic()+max(45,ticks/60)
        while time.monotonic()<deadline:
            s=json.loads(self.bridge.command('/sc rcon.print(helpers.table_to_json({tick=game.tick,left=game.ticks_to_run}))'))
            if s['left']==0:
                if s['tick']!=start+ticks:raise RuntimeError('unexpected tick advancement')
                return
            time.sleep(.1)
        raise RuntimeError('engine step deadline')
    def power_facts(self):
        name=f'smelting-fixture/{uuid.uuid4().hex}.json'
        cmd=f'''/sc local s=game.surfaces["{self.surface}"];local f=game.forces["{self.force}"];local p={{}};for _,e in pairs(s.find_entities_filtered{{type="electric-pole",force=f}}) do p[#p+1]={{name=e.name,position=e.position,wire=e.prototype.get_max_wire_distance(),supply=e.prototype.get_supply_area_distance(),generated=true}} end;helpers.write_file("{name}",helpers.table_to_json({{poles=p,ports={{}},machines={{}}}}),false)'''
        self.bridge.command(cmd);return load_json(self.bridge.script_output/name)
    def build(self,plan):
        before=self.rpc('snapshot');needed=bill([plan])
        if any(before['stock'].get(n,0)-before['pending_bill'].get(n,0)<v for n,v in needed.items()):raise RuntimeError('construction budget unavailable')
        actions=plan['phases'][0]['actions']
        for i in range(0,len(actions),30):self.rpc('validate',actions=actions[i:i+30])
        receipt=load_json(self.bridge.build_layout(build_layout_authorization([plan]),plan,timeout=60))
        if not receipt['ok']:raise RuntimeError('ghost submission failed: '+json.dumps(receipt)[:600])
        stagnant=0;last=None
        for _ in range(80):
            self.advance(600);after=self.rpc('snapshot')
            if after['ghosts']==0:return {'submission':receipt,'construction_ticks':after['tick']-before['tick'],'bill':needed}
            stagnant=stagnant+1 if after['pending_bill']==last else 0;last=after['pending_bill']
            if stagnant>=10:break
        raise RuntimeError(f'construction incomplete: {after["pending_bill"]}')


def state(facts,option,run_id):
    # Reuse the clean runner's typed state vocabulary; fixture observations stay explicit.
    from training.laya_saved_world import decision_state
    robots={p['network']:p['robots'] for p in facts.get('ports',[]) if p.get('network')}
    pseudo={'tick':facts['tick'],'networks':{'lab':{'items':facts['stock'],'reserved':facts['pending_bill'],'construction_robots':sum(robots.values())}},
            'poles':[],'ports':[],'machines':[],'pending_ghosts':facts['ghosts'],'production':{facts['recipe']:facts['produced']},'consumption':{facts['ore']:facts['consumed']}}
    # state_option uses SCOPE; replace all scope fields before validation and recording.
    s=deepcopy(decision_state(pseudo,[option],run_id,{'model_id':'smelting-mechanics-replay','model_hash':'sha256:'+'0'*64},
                     'Measure belt-only Start-Middle-Middle-End smelting, 180 ore/s to 180 plates/s.'))
    s['run'].update(surface=facts['surface'],force=facts['force'],bootstrap_profile='declared-smelting-fixture')
    for o in s['options']:o['scope'].update(surface=facts['surface'],force=facts['force'],bounds={'x_min':-8,'y_min':-28,'x_max_exclusive':280,'y_max_exclusive':65})
    s['acceptance_gap'].update(target=180,required_sustained_ticks=7200)
    s['observation_digest']['smelting_fixture']=facts
    validate_state(s);return s


def run(args):
    root=owned_root(args.root);started=local_stamp();begin=time.monotonic();failures=[];milestones=[];rows=[];lab=None
    mods=root.parent/(root.name+'-mods-source')
    if root.exists() or mods.exists():raise ValueError('fresh root and mod staging directory required')
    mods.mkdir()
    shutil.copytree(args.mods_source/'factorio_cursor_rl_agent',mods/'factorio_cursor_rl_agent')
    shutil.copytree(ROOT/'factorio_training_lab',mods/'factorio_training_lab',ignore=shutil.ignore_patterns('__pycache__'))
    try:
        prepare(argparse.Namespace(root=root,source_save=args.source_save,mods_source=mods,game_port=args.game_port,rcon_port=args.rcon_port))
    except Exception as error:
        evidence=root/'evidence';evidence.mkdir(exist_ok=True)
        cause=getattr(error,'stderr',None) or str(error)
        (evidence/'summary.json').write_text(json.dumps({'outcome':'preparation_failed','failures':[{'cause':cause,'type':type(error).__name__}]},indent=2)+'\n')
        if (root/'server/factorio.pid').exists():
            verify_instance(root);manager(root,'stop')
        record({'system':'rl-lab','run_id':root.name,'started':started,'ended':local_stamp(),'duration_s':round(time.monotonic()-begin),
                'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'target':'4 express ore belts -> 4 express plate belts',
                'highest_milestone':None,'milestones':[],'milestone_source':'none','outcome':'preparation_failed',
                'failure_code':'lab_preparation','failure_cause':cause,'limiting_factor':None,'decisions':0,
                'log_file':None,'root':str(root)})
        raise
    journal=root/'evidence/decision-states.jsonl';log=root/'logs/smelting-test.log';log.parent.mkdir(exist_ok=True)
    journal.parent.mkdir(exist_ok=True)
    def emit(msg):
        print(msg,flush=True)
        with log.open('a') as out:out.write(f'{local_stamp()} {msg}\n')
    def append(row):
        with journal.open('a') as out:out.write(json.dumps(row,sort_keys=True)+'\n')
    outcome='error';active_record=None
    try:
        lab=Lab(root)
        plans=[checked_plan(lab.surface,lab.force,f'header-{g}',headers(g)) for g in range(4)]
        plans += [checked_plan(lab.surface,lab.force,f'line-{i}',serial_line(args.recipe,i,belt_tier=args.line_belt_tier,fast_arms=args.fast_arms)) for i in range(16)]
        validate_union(plans);budget=bill(plans);budget['medium-electric-pole']+=400
        fixture=lab.rpc('init',surface=lab.surface,force=lab.force,recipe=args.recipe,budget=budget)
        if abs(fixture['furnace_speed']/fixture['recipe_seconds']-.625)>1e-9 or abs(fixture['express_belt_speed']*480-45)>1e-9:
            raise RuntimeError('live furnace/belt capacity differs from declared sizing')
        (root/'evidence/fixture.json').write_text(json.dumps(fixture,indent=2)+'\n')
        manifest=json.loads((root/'manifest.json').read_text());manifest['mechanics_fixture']=fixture;manifest['authority']='mechanics_replay'
        manifest['code_hashes']={str(p.relative_to(ROOT)):file_hash(p) for p in [Path(__file__),ROOT/'training/laya_smelting_array.py',ROOT/'training/laya_lines.py',ROOT/'factorio_training_lab/smelting_fixture.lua']}
        (root/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        emit('BUILD headers (four express input/output groups)')
        for p in plans[:4]:
            facts=lab.rpc('snapshot');candidate={'option_id':p['phases'][0]['name'],'family':'line','product':args.recipe,
                    'summary':'Build express splitter fanout and output merge','plan':p,'bill':bill([p])}
            r={'version':'laya-decision-record-v1','recorded_at':local_stamp(),'run_id':root.name,'index':int(candidate['option_id'][-1]),
               'tick':facts['tick'],'phase':'smelting-mechanics','authority':'mechanics_replay','state':state(facts,candidate,root.name),
               'options':[{'option_id':candidate['option_id'],'family':'line','summary':candidate['summary'],'bill':candidate['bill'],'plan_hash':sha256_hash(p)}],
               'distribution':{candidate['option_id']:1},'choice':{'option_id':candidate['option_id'],'probability':1,'source':'user_design_mechanics_test'},'no_legal_build':False}
            (root/'evidence'/f'{candidate["option_id"]}-pending.json').write_text(json.dumps(r,indent=2)+'\n')
            active_record=r
            r['outcome']={'status':'built','execution':lab.build(p)};append(r)
            active_record=None
        for target in args.stages:
            a=[]
            for i in range(len(rows) and rows[-1]['lines'] or 0,target):a += plans[4+i]['phases'][0]['actions']
            candidate={'option_id':f'line.build-to-{target}','family':'line','summary':f'Build {target} parallel 18-furnace lines',
                       'product':args.recipe,'plan':{'surface':lab.surface,'force':lab.force,'atomic':True,'phases':[{'name':f'lines-to-{target}','actions':a}]},'bill':dict(Counter(v['entity'] for v in a))}
            linked=internal_power_variant(candidate,lab.power_facts())
            if not linked:raise RuntimeError('template power linkage invalid')
            candidate=linked;before=lab.rpc('snapshot')
            candidate['bill']=bill([candidate['plan']])
            row={'version':'laya-decision-record-v1','recorded_at':local_stamp(),'run_id':root.name,'index':4+len(rows),'tick':before['tick'],
                 'phase':'smelting-mechanics','authority':'mechanics_replay','state':state(before,candidate,root.name),
                 'options':[{'option_id':candidate['option_id'],'family':'line','summary':candidate['summary'],'bill':candidate['bill'],'plan_hash':sha256_hash(candidate['plan'])}],
                 'distribution':{candidate['option_id']:1},'choice':{'option_id':candidate['option_id'],'probability':1,'source':'user_design_mechanics_test'},'outcome':{'status':'pending'},'no_legal_build':False}
            pending=root/'evidence'/f'decision-{target}-pending.json';pending.write_text(json.dumps(row,indent=2)+'\n')
            active_record=row
            emit(f'BUILD to {target} lines / {target*18} furnaces')
            receipt=lab.build(candidate['plan'])
            rates=[min(4,max(0,target-g*4))*11.25 for g in range(4)];lab.rpc('rates',rates=rates)
            lab.advance(18000) # Fill transport and furnace WIP, independent of acceptance counters.
            samples=[lab.rpc('snapshot')]
            for _ in range(4):lab.advance(1800);samples.append(lab.rpc('snapshot'))
            result=measurement(samples[0],samples[-1],target)
            result['output_subwindows_per_s']=[[(v-u)*60/(b['tick']-a['tick']) for u,v in zip(a['delivered'],b['delivered'])] for a,b in zip(samples,samples[1:])]
            after=samples[-1]
            result['construction_budget_conserved']=all(after['stock'].get(n,0)+after['built'].get(n,0)==v for n,v in budget.items())
            result['accepted']=result['accepted'] and result['construction_budget_conserved']
            row['outcome']={'status':'accepted' if result['accepted'] else 'shortfall','measurement':result,'execution':receipt};append(row)
            active_record=None
            rows.append({'lines':target,'measurement':result,'snapshots':samples})
            (root/'evidence'/f'measurement-{target}.json').write_text(json.dumps(rows[-1],indent=2)+'\n')
            emit(json.dumps(result))
            if result['accepted']:milestones.append({'id':f'{args.recipe}-{target}-lines','tick':samples[-1]['tick']})
            else:
                failures.append({'step':target,'cause':'measured throughput shortfall','measurement':result})
                break
        outcome='completed' if rows and rows[-1]['lines']==16 and rows[-1]['measurement']['accepted'] else 'shortfall'
    except Exception as error:
        if active_record:
            append({**active_record,'outcome':{'status':'failed','error':str(error),'error_type':type(error).__name__}})
        failures.append({'cause':str(error),'type':type(error).__name__});emit('ERROR '+str(error));raise
    finally:
        if lab:
            try:
                lab.bridge.command('/server-save smelting-final')
            except Exception as error:
                failures.append({'cause':'final evidence save failed: '+str(error)})
            finally:
                lab.host.close()
        verify_instance(root)
        manager(root,'stop')
        summary={'outcome':outcome,'recipe':args.recipe,'steps':rows,'failures':failures,'milestones':milestones,'source_sha256':file_hash(args.source_save)}
        (root/'evidence/summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        record({'system':'rl-lab','run_id':root.name,'started':started,'ended':local_stamp(),'duration_s':round(time.monotonic()-begin),
                'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'target':'4 express ore belts -> 4 express plate belts',
                'highest_milestone':milestones[-1]['id'] if milestones else None,'milestones':milestones,'milestone_source':'engine_counters_and_belt_endpoints',
                'outcome':outcome,'failure_code':'smelting_shortfall' if failures else None,'failure_cause':failures[-1]['cause'] if failures else None,
                'limiting_factor':None,'decisions':sum(1 for _ in journal.open()) if journal.exists() else 0,'log_file':str(log),'root':str(root)})
    return 0 if outcome=='completed' else 2

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    p.add_argument('--source-save',type=Path,default=ROOT/'saves/mod_playground.zip')
    p.add_argument('--mods-source',type=Path,default=Path.home()/'.local/share/factorio-rl/deterministic/mods')
    p.add_argument('--game-port',type=int,default=34270);p.add_argument('--rcon-port',type=int,default=27170)
    p.add_argument('--recipe',choices=['iron-plate','copper-plate'],default='iron-plate')
    p.add_argument('--line-belt-tier',choices=['basic','express'],default='basic');p.add_argument('--fast-arms',action='store_true')
    p.add_argument('--stages',type=int,nargs='+',default=[1,4,8,12,16]);args=p.parse_args()
    if args.game_port in {34196,34199,34230,34232,*range(34200,34221)} or args.rcon_port in {27016,27017,27130,27132,*range(27100,27121)}:
        p.error('reserved production, mall or clean-runner ports')
    if not args.stages or args.stages[0]!=1 or args.stages[-1]!=16 or sorted(set(args.stages))!=args.stages:p.error('stages must increase from 1 to 16')
    sys.exit(run(args))
