#!/usr/bin/env python3
# Path: tools/run_laya_context_suite.py | Purpose: Eight-hour frozen, bounded matched experiments with independent component fixtures and two-hour reviews.
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from core.local_time import local_stamp
from tools.laya_experiment_helper import review

VARIANTS=['baseline','buffer-1','buffer-3','buffer-1-guidelines-8','buffer-1-menu-wide','buffer-1-observe-only','buffer-1-dependency','buffer-1-stall-24']
COMPONENTS=[
 {'family':'circuit','transport':'belt','producers':2},
 {'family':'circuit','transport':'belt','producers':3},
 {'family':'circuit','transport':'belt','producers':4},
 {'family':'circuit','transport':'bots','producers':3},
 {'family':'circuit','transport':'bots','producers':3,'request_multiplier':3},
 {'family':'circuit','transport':'bots','producers':3,'request_multiplier':15},
 {'family':'circuit','transport':'belt','producers':3,'input_share':.5},
 {'family':'circuit','transport':'belt','producers':3,'delay_ticks':3600},
 {'family':'plastic','transport':'belt','producers':1},
 {'family':'plastic','transport':'belt','producers':2},
 {'family':'plastic','transport':'belt','producers':3},
 {'family':'plastic','transport':'belt','producers':2,'input_share':.5},
 {'family':'plastic','transport':'belt','producers':2,'delay_ticks':3600},
]


def write(path,payload):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(payload,indent=2)+'\n');temp.replace(path)


def analyze(rows):
    groups=defaultdict(list)
    for r in rows:groups[(r['kind'],r.get('phase'),r.get('case'),r['variant'])].append(r)
    table=[]
    for (kind,phase,case,variant),jobs in sorted(groups.items(),key=lambda x:str(x[0])):
        usable=[j for j in jobs if j.get('status')=='finished' and not j.get('infrastructure_error')]
        reached=[j for j in usable if j.get('target_met')]
        table.append({'kind':kind,'phase':phase,'case':case,'variant':variant,'attempts':len(jobs),'usable':len(usable),
          'reached':len(reached),'rate':len(reached)/len(usable) if usable else None,
          'errors':len([j for j in jobs if j.get('infrastructure_error')]),
          'median_target_ticks':__import__('statistics').median([j['target_ticks'] for j in reached if j.get('target_ticks') is not None]) if any(j.get('target_ticks') is not None for j in reached) else None,
          'mean_production_per_s':sum(j.get('production_per_s',0) for j in usable)/len(usable) if usable and kind=='component' else None})
        for metric in ['repair_ticks','recovery_production_per_s']:
            values=[j[metric] for j in usable if j.get(metric) is not None]
            table[-1]['mean_'+metric]=sum(values)/len(values) if values else None
    paired=[]
    for kind in {'raw','component'}:
        by=defaultdict(dict)
        for r in rows:
            if r['kind']==kind:by[(r.get('phase'),r.get('case'),r['seed'])][r['variant']]=r
        for key,arms in by.items():
            baseline=arms.get('baseline')
            if not baseline:continue
            for variant,r in arms.items():
                if variant=='baseline':continue
                clean=not baseline.get('infrastructure_error') and not r.get('infrastructure_error') and all(x.get('status')=='finished' for x in [baseline,r])
                paired.append({'kind':kind,'phase':key[0],'case':key[1],'seed':key[2],'variant':variant,'usable':clean,
                      'completion_delta':int(bool(r.get('target_met')))-int(bool(baseline.get('target_met'))) if clean else None,
                      'target_ticks_delta':r['target_ticks']-baseline['target_ticks'] if clean and r.get('target_met') and baseline.get('target_met') and r.get('target_ticks') is not None and baseline.get('target_ticks') is not None else None})
                for metric in ['production_per_s','delivered_per_s','repair_ticks','recovery_production_per_s']:
                    paired[-1][metric+'_delta']=r[metric]-baseline[metric] if clean and r.get(metric) is not None and baseline.get(metric) is not None else None
    return {'recorded_at':local_stamp(),'variants':table,'matched_pairs':paired,'adoption':'none; defaults and production authority unchanged',
      'scope':('Raw starts share one immutable base; continuations are grouped by their recorded earned checkpoint. ' if any(r.get('resume_bundle') for r in rows) else 'Raw worlds share one immutable base; ')+
              'Fresh RNG seeds test policy repeatability, not unseen-terrain generalization. Fixture components are a separate domain.'}


class Supervisor:
    def __init__(self,root):
        self.root=root.resolve();self.cfg=json.loads((root/'suite.json').read_text());self.code=Path(self.cfg['code_root']);self.python=self.cfg['python']
        self.deadline=self.cfg['deadline_epoch'];self.stop=threading.Event();self.lock=threading.Lock();self.rows=[];self.running={};self.errors=Counter()
    def event(self,row):
        with self.lock:
            with (self.root/'jobs.jsonl').open('a') as out:out.write(json.dumps(row,sort_keys=True)+'\n')
    def variant(self,name):return Path(self.cfg.get('variant_directory',self.code/'docs/rl/laya_variants/context-20261004'))/f'{name}.json' if name!='baseline' else None
    def terminate(self,p):
        if p.poll() is None:
            p.terminate()
            try:p.wait(timeout=15)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
    def process(self,cmd,log,timeout,slot):
        with log.open('a') as out:
            p=subprocess.Popen(cmd,cwd=self.code,stdout=out,stderr=subprocess.STDOUT,env={**os.environ,'OMP_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1'})
            with self.lock:self.running[slot]=p
            end=min(time.time()+timeout,self.deadline-30)
            try:
                while p.poll() is None:
                    if self.stop.is_set() or time.time()>=end:
                        self.terminate(p);return {'exit':p.returncode,'interrupted':True}
                    time.sleep(1)
                return {'exit':p.returncode,'interrupted':False}
            finally:
                with self.lock:self.running.pop(slot,None)
    def cleanup(self,world,log):
        if not (world/'manifest.json').exists():return
        # Verify in the frozen checkout's own strict root namespace before its manager can stop anything.
        cmd=[self.python,'-c',"from pathlib import Path;from training.laya_saved_world import verify_instance;from tools.run_laya_saved_world import manager;r=Path(__import__('sys').argv[1]);verify_instance(r);print(manager(r,'stop'))",str(world)]
        if not (world/'server/factorio.pid').exists():return
        with log.open('a') as out:
            r=subprocess.run(cmd,cwd=self.code,stdout=out,stderr=subprocess.STDOUT,timeout=40)
            if r.returncode:self.event({'event':'cleanup_failed','root':str(world),'log':str(log),'at':local_stamp()})
    def job(self,j,slot,batch):
        name=f"{j['kind']}-{batch}-{j['variant']}-s{j['seed']}"
        world=self.code/'data/laya-saved-world'/name
        log=self.root/'logs'/f'{name}.log';log.parent.mkdir(exist_ok=True)
        row={**j,'root':str(world),'log':str(log),'started':local_stamp(),'event':'started','slot':slot}
        self.event(row)
        ports=['--game-port',str(self.cfg.get('game_port_base',34300)+slot),'--rcon-port',str(self.cfg.get('rcon_port_base',27200)+slot)]
        inputs=['--source-save',self.cfg['source_save'],'--mods-source',self.cfg['mods_source']]
        result={'status':'finished','infrastructure_error':False,'target_met':False,'target_ticks':None}
        try:
            if j['kind']=='raw':
                resume=['--resume-bundle',j['resume_bundle']] if j.get('resume_bundle') else []
                prep=self.process([self.python,str(self.code/'tools/run_laya_clean.py'),'prepare','--root',str(world),*ports,*inputs,*resume],log,120,slot)
                if prep['exit'] or prep['interrupted']:raise RuntimeError('world preparation failed or interrupted')
                cmd=[self.python,str(self.code/'tools/run_laya_clean.py'),'run','--root',str(world),'--seed',str(j['seed']),'--no-helper',
                     '--speed',str(j.get('speed',4)),'--until-phase',j['phase'],'--max-game-ticks',str(j.get('max_ticks',216000)),'--experiment',self.root.name]
                if self.variant(j['variant']):cmd+=['--variant',str(self.variant(j['variant']))]
                cmd+=resume
                if j.get('export_checkpoint'):cmd+=['--export-checkpoint',j['export_checkpoint']]
                if j.get('measure_ticks'):cmd+=['--measure-ticks',str(j['measure_ticks'])]
                status=self.process(cmd,log,j.get('timeout',1500),slot)
                index=self.code/'data/laya-saved-world/laya-runs.jsonl'
                records=[json.loads(x) for x in index.read_text().splitlines()] if index.exists() else []
                r=next((r for r in reversed(records) if r.get('root')==str(world)),None)
                if r is None:raise RuntimeError('runner missing terminal index')
                summary=r['summary'];result.update(target_met=j['phase'] in summary.get('phases_reached',[]),target_ticks=summary.get('phase_ticks',{}).get(j['phase']),
                  summary=summary,run_id=r['run_id'],interrupted=status['interrupted'],infrastructure_error=bool(status['exit']) or summary.get('stop')=='runner_error' or summary.get('failures',0)>0)
                if status['interrupted']:result['status']='interrupted'
            else:
                case=j.get('component') or COMPONENTS[j['case']]
                cmd=[self.python,str(self.code/'tools/run_laya_component.py'),'--root',str(world),'--seed',str(j['seed']),*ports,*inputs,'--experiment',self.root.name]
                for key,v in case.items():cmd += ['--'+key.replace('_','-'),str(v)]
                if self.variant(j['variant']):cmd+=['--variant',str(self.variant(j['variant']))]
                status=self.process(cmd,log,900,slot)
                path=world/'evidence/component-result.json'
                r=json.loads(path.read_text()) if path.exists() else {'status':'missing'}
                result.update(target_met=r.get('target_met',False),target_ticks=r.get('target_verified_ticks'),
                  first_output_observed_ticks=r.get('first_output_observed_ticks'),commissioning_ticks=r.get('construction_ticks'),production_per_s=r.get('production_per_s'),
                  repair_ticks=r.get('repair_ticks'),recovery_production_per_s=r.get('recovery_production_per_s'),
                  delivered_per_s=r.get('delivered_per_s'),summary={k:v for k,v in r.items() if k not in {'before','after'}},
                  infrastructure_error=bool(status['exit']) or r.get('status')=='missing' or r.get('infrastructure_error',False),interrupted=status['interrupted'])
                if status['interrupted']:result['status']='interrupted'
            if result['infrastructure_error']:self.errors[(j['kind'],j.get('case'))]+=1
        except Exception as e:
            result.update(status='error',infrastructure_error=True,error=str(e));self.errors[(j['kind'],j.get('case'))]+=1
        finally:self.cleanup(world,log)
        row.update(event='ended',ended=local_stamp(),**result);self.event(row)
        with self.lock:self.rows.append(row)
        return row
    def checkpoint(self,hour):
        analysis=analyze(self.rows);write(self.root/'analysis.json',analysis);write(self.root/f'checkin-{hour}h.json',analysis)
        helper=self.root/'helper';helper.mkdir(exist_ok=True)
        (helper/f'review-request-{hour}h').write_text(local_stamp()+'\n')
    def run(self):
        next_check=self.cfg['started_epoch']+7200;check_hour=2;batch=0;seed=41000
        signal.signal(signal.SIGTERM,lambda *_:self.stop.set());signal.signal(signal.SIGINT,lambda *_:self.stop.set())
        try:
            with ThreadPoolExecutor(max_workers=self.cfg['parallel']) as pool:
                while time.time()<self.deadline-120 and not self.stop.is_set():
                    elapsed=time.time()-self.cfg['started_epoch'];segment=min(3,int(elapsed//7200))
                    kind='component' if batch%3==2 or segment==2 and batch%2 else 'raw'
                    if kind=='raw':
                        variants=VARIANTS if segment==0 else ['baseline','buffer-1','buffer-3','buffer-1-dependency','buffer-1-guidelines-8','buffer-1-stall-24']
                        phase='C1' if segment==0 or segment==3 and batch%2 else 'C2'
                        jobs=[{'kind':'raw','variant':v,'seed':s,'phase':phase,'case':None,'speed':4,'max_ticks':216000,'timeout':1500} for s in range(seed,seed+2) for v in variants]
                    else:
                        cases=[i for i in range(len(COMPONENTS)) if self.errors[('component',i)]<3]
                        jobs=[{'kind':'component','variant':v,'seed':seed+i,'case':i,'phase':'COMPONENT'} for i in cases for v in ['baseline','buffer-1-observe-only','buffer-1-dependency']]
                    # Slots are leased explicitly; no two live jobs can own an endpoint.
                    for offset in range(0,len(jobs),self.cfg['parallel']):
                        if self.stop.is_set() or time.time()>=self.deadline-120:break
                        cohort=jobs[offset:offset+self.cfg['parallel']]
                        futures=[pool.submit(self.job,j,slot,batch) for slot,j in enumerate(cohort)]
                        while futures:
                            completed=[f for f in futures if f.done()]
                            for f in completed:f.result();futures.remove(f)
                            write(self.root/'progress.json',{'recorded_at':local_stamp(),'status':'running','batch':batch,'segment':segment,
                               'completed_jobs':len(self.rows),'active_jobs':len(futures),'remaining_seconds':max(0,int(self.deadline-time.time())),
                               'source_sha256':self.cfg['source_sha256'],'code_commit':self.cfg['code_commit']})
                            write(self.root/'analysis.json',analyze(self.rows))
                            if time.time()>=next_check:
                                self.checkpoint(check_hour);next_check+=7200;check_hour+=2
                            time.sleep(5)
                    seed+=100;batch+=1
        finally:
            self.stop.set()
            for p in list(self.running.values()):self.terminate(p)
            write(self.root/'report.json',analyze(self.rows));(self.root/'DONE').write_text(local_stamp()+'\n')
            self.checkpoint(8)
            write(self.root/'progress.json',{'status':'finished','at':local_stamp(),'completed_jobs':len(self.rows),'report':str(self.root/'report.json')})

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);a=p.parse_args();Supervisor(a.root).run()
