#!/usr/bin/env python3
# Path: tools/run_laya_mechanics_matrix.py | Purpose: Finite, frozen follow-on experiment queue covering six systems, with matched controls and owned worker leases.
from __future__ import annotations
import argparse
from copy import deepcopy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from core.local_time import local_stamp
from tools.run_laya_context_suite import Supervisor,analyze,write
from tools.laya_experiment_helper import exclusive


def matrix(code):
    """One-factor screens and declared interactions; three paired policy seeds per arm."""
    variants={};families=defaultdict(list)
    seeds=[51000,51001,51002]
    preferences={r['rule_id']:r.get('prefer',[]) for r in json.loads((ROOT/'docs/rl/laya_mission_guidelines.json').read_text())['rules']}
    def weight(rule,condition,value):
        rows=deepcopy(preferences[rule])
        match=next((r for r in rows if r['when']==condition),None)
        if match:match['weight']=value
        else:rows.append({'when':condition,'weight':value})
        return rule,rows
    def variant(name,settings=None,pref=None,epsilon=None):
        if name=='baseline':return
        value={'version':'1.0.0','variant_id':name,'description':'Mechanics matrix intervention; no default adoption.'}
        if settings:value['settings']=settings
        if pref:value['catalog_patch']={pref[0]:{'prefer':pref[1]}}
        if epsilon is not None:value['policy']={'epsilon':epsilon}
        if name in variants and variants[name]!=value:raise ValueError('inconsistent variant')
        variants[name]=value
    def raw(family,arms,phase='C1',resume=None):
        for seed in seeds:
            for name,settings,pref,epsilon,window in arms:
                variant(name,settings,pref,epsilon)
                j={'kind':'raw','case':family,'category':family,'phase':phase,'variant':name,'seed':seed,'speed':4,
                   'max_ticks':window,'timeout':2400 if window>216000 else 1500}
                if resume:j['resume_bundle']=str(resume)
                families[family].append(j)
    default=('baseline',None,None,None,216000)
    context=[default]+[(f'context-{m}',{'context_view':m},None,None,216000) for m in ['inventory','bottleneck','dependency','history','combined']]
    context +=[(f'context-combined-e{int(e*100)}',{'context_view':'combined'},None,e,216000) for e in [.05,.3]]
    context +=[(f'context-rules-{n}',{'guideline_limit':n},None,None,216000) for n in [8,10,12]]
    for feature,view in [('blocks_other_builds','dependency'),('repeat_without_output','history')]:
        context += [(f'feedback-{view}-{w:g}',{'context_view':view},weight('mission.conserve-scarce',{feature:True},w),None,216000) for w in [.35,.7,1.5]]
    raw('context',context)
    planning=[default]
    for value in [.5,2,4]:
        planning.append((f'plan-goal-{value:g}',None,weight('mission.phase-goals-first',{'goal_need':True},value),None,216000))
    for value in [1.5,3,6]:
        planning.append((f'plan-upstream-{value:g}',None,weight('mission.serve-bottleneck',{'deepest_bottleneck':True},value),None,216000))
    for rate in [.005,.02,.05]:planning.append((f'plan-stock-{rate:g}',{'stock_rate':rate},None,None,216000))
    planning += [('plan-lookahead',{'context_view':'combined','dependency_context':True},
                  ('mission.conserve-scarce',[{'when':{'blocks_other_builds':True},'weight':.5}]),None,216000)]
    raw('planning',planning,'C2')
    bootstrap=[]
    for seed in [41000,41400,41401]:
        bundle=code/f'data/laya-experiments/earned-c1-{seed}'
        bootstrap.append({'kind':'raw','case':'checkpoint','category':'checkpoint','phase':'C1','variant':'baseline','seed':seed,
                          'speed':4,'max_ticks':216000,'timeout':1500,'export_checkpoint':str(bundle)})
        arms=[default]+[(f'transition-{m}',{'reservation_mode':m},None,None,216000) for m in ['mall-open','corridor-open','soft-zones']]
        arms += [(f'transition-buffer-{n}',{'request_multiplier':n},None,None,216000) for n in [1,3,6]]
        arms += [(f'transition-patience-{n}',{'stall_decisions':n},None,None,216000) for n in [24,48]]
        arms += [('transition-window-2x',None,None,None,432000),
                 ('transition-observed',{'context_view':'combined'},None,None,216000),
                 ('transition-open-buffer',{'reservation_mode':'mall-open','request_multiplier':3,'context_view':'combined'},None,None,216000)]
        raw(f'transition-{seed}',arms,'C2',bundle)
    # Previously successful seeds give the earned-state curriculum a measured search budget.
    for seed in [45300,45500,45501,45901,46100]:
        bootstrap.append({'kind':'raw','case':'checkpoint','category':'checkpoint','phase':'C1','variant':'baseline','seed':seed,
                          'speed':4,'max_ticks':216000,'timeout':1500,
                          'export_checkpoint':str(code/f'data/laya-experiments/earned-c1-{seed}')})
    def component(category,case,base,changes):
        for seed in seeds:
            for name,physical,policy in [('baseline',{},None),*changes]:
                if policy:
                    settings,pref,epsilon=policy
                    variant(name,settings,pref,epsilon)
                else:variant(name)
                families[category].append({'kind':'component','case':case,'category':category,'phase':'COMPONENT','variant':name,
                                          'seed':seed,'component':{**base,**physical}})
    belt={'family':'circuit','transport':'belt','producers':3,'consumers':2}
    bots={**belt,'transport':'bots','request_multiplier':15}
    transport=[]
    for key,values in [('supply_distance',[6,15,30,45]),('logistic_robots',[22,55,110,220,550]),
                       ('request_multiplier',[1,2,3,6,10]),('spacing',[6,12,24])]:
        transport += [(f'transport-{key.replace("_","-")}-{v}',{key:v},None) for v in values]
    transport += [('transport-long-low-buffer',{'supply_distance':45,'logistic_robots':55,'request_multiplier':3},None),
                  ('transport-long-large-buffer',{'supply_distance':45,'logistic_robots':55,'request_multiplier':15},None)]
    component('transport','transport-bots',bots,transport)
    component('transport','transport-belt',belt,
              [(f'belt-{k}-{v}',{k:v},None) for k,values in [('spacing',[6,12,24]),('producers',[2,4]),('consumers',[1,3]),('input_share',[.75,1.25])] for v in values])
    plastic={'family':'plastic','transport':'belt','producers':2,'consumers':2}
    changes=[(f'plastic-{k}-{v}',{k:v},None) for k,values in [('producers',[1,3,4]),('consumers',[1,3]),
             ('input_share',[.5,.75,1.25,1.5]),('spacing',[6,12,24]),('sink_pause_ticks',[600,1800,3600])] for v in values]
    changes += [(f'plastic-outage-{source}-{ticks}',{'interruption_source':source,'interruption_ticks':ticks},None)
                for source in ['coal','crude-oil'] for ticks in [600,1800,3600]]
    changes += [('plastic-extra-refinery-half-coal',{'producers':3,'input_share':.5},None),
                ('plastic-long-route-coal-outage',{'spacing':24,'interruption_source':'coal','interruption_ticks':1800},None)]
    component('plastic','plastic-integration',plastic,changes)
    recovery=[(f'context-{m}',{},({'context_view':m},None,None)) for m in ['inventory','dependency','history','combined']]
    recovery += [(f'recovery-e{int(e*100)}',{},(None,None,e)) for e in [.05,.3]]
    recovery += [('feedback-history-0.7',{},({'context_view':'history'},weight('mission.conserve-scarce',{'repeat_without_output':True},.7),None))]
    for label,base,faults in [('circuit-belt',belt,['belt','machine','pole']),('circuit-bots',bots,['machine','pole']),('plastic',plastic,['belt','machine','pole'])]:
        for fault in faults:component('recovery',f'recovery-{label}-{fault}',{**base,'fault':fault},recovery)
    # Family round-robin gives every type evidence before spending the whole budget on one.
    ordered=[]
    names=list(families)
    while any(families.values()):
        for name in names:
            if families[name]:ordered.append(families[name].pop(0))
    jobs=bootstrap+ordered
    def safe(name):return name.replace('_','-').replace('.','p')
    variants={safe(name):{**v,'variant_id':safe(name)} for name,v in variants.items()}
    for i,j in enumerate(jobs):
        j['job_id']=f'matrix-{i:04d}';j['variant']=safe(j['variant'])
    return variants,jobs


def assign_checkpoints(jobs):
    available=[j['export_checkpoint'] for j in jobs if j.get('export_checkpoint') and (Path(j['export_checkpoint'])/'checkpoint.json').exists()]
    assignment={};used=set()
    for j in jobs:
        if not j.get('resume_bundle') or j['case'] in assignment:continue
        preferred=j['resume_bundle']
        selected=preferred if preferred in available and preferred not in used else next((p for p in available if p not in used),None)
        assignment[j['case']]=selected
        if selected:used.add(selected)
    return assignment


class MatrixSupervisor(Supervisor):
    def wait_previous(self):
        previous=Path(self.cfg['previous_root']);unit=self.cfg['previous_actor_unit']
        while time.time()<self.deadline-120 and not self.stop.is_set():
            active=subprocess.run(['systemctl','--user','is-active',unit],capture_output=True,text=True).stdout.strip()
            if active in {'inactive','failed','unknown'}:
                self.event({'event':'predecessor_released','at':local_stamp(),'unit':unit,'terminal_evidence':(previous/'DONE').exists()})
                return
            write(self.root/'progress.json',{'status':'queued','at':local_stamp(),'waiting_for':unit,'total_jobs':len(self.jobs)})
            time.sleep(5)
        raise RuntimeError('queue stopped before predecessor released')

    def run(self):
        self.jobs=json.loads((self.root/'queue.json').read_text())
        signal.signal(signal.SIGTERM,lambda *_:self.stop.set());signal.signal(signal.SIGINT,lambda *_:self.stop.set())
        next_check=self.cfg['started_epoch']+7200;check_hour=2
        with exclusive(self.root,'matrix-supervisor'):
            self.wait_previous()
            self.event({'event':'matrix_started','at':local_stamp(),'total_jobs':len(self.jobs)})
            try:
                with ThreadPoolExecutor(max_workers=self.cfg['parallel']) as pool:
                    bootstrap_count=sum(bool(j.get('export_checkpoint')) for j in self.jobs)
                    cohorts=[self.jobs[:bootstrap_count]]+[self.jobs[i:i+self.cfg['parallel']] for i in range(bootstrap_count,len(self.jobs),self.cfg['parallel'])]
                    assignments=None
                    for cohort_index,cohort in enumerate(cohorts):
                        if self.stop.is_set() or time.time()>=self.deadline-120:break
                        # Bootstrap checkpoints are a dependency barrier, not concurrent with continuations.
                        if cohort_index==1:
                            assignments=assign_checkpoints(self.jobs)
                            self.event({'event':'checkpoint_assignments','at':local_stamp(),'assignments':assignments})
                        futures=[]
                        for slot,j in enumerate(cohort):
                            missing_assignment=j.get('resume_bundle') and assignments is not None and not assignments.get(j['case'])
                            if j.get('resume_bundle') and assignments and assignments.get(j['case']):j={**j,'resume_bundle':assignments[j['case']]}
                            if missing_assignment or j.get('resume_bundle') and not (Path(j['resume_bundle'])/'checkpoint.json').exists():
                                row={**j,'event':'ended','status':'blocked_setup','infrastructure_error':True,'target_met':False,
                                     'root':str(self.code/'data/laya-saved-world'/j['job_id']),'error':'earned C1 checkpoint unavailable','ended':local_stamp()}
                                self.event(row);self.rows.append(row);continue
                            futures.append(pool.submit(self.job,j,slot,j['job_id']))
                        while futures:
                            for f in [f for f in futures if f.done()]:f.result();futures.remove(f)
                            write(self.root/'progress.json',{'status':'running','at':local_stamp(),'completed_jobs':len(self.rows),
                                  'active_jobs':len(futures),'total_jobs':len(self.jobs),'remaining_seconds':max(0,int(self.deadline-time.time())),
                                  'code_commit':self.cfg['code_commit'],'source_sha256':self.cfg['source_sha256']})
                            write(self.root/'analysis.json',analyze(self.rows))
                            if time.time()>=next_check:self.checkpoint(check_hour);next_check+=7200;check_hour+=2
                            time.sleep(5)
            finally:
                self.stop.set()
                for p in list(self.running.values()):self.terminate(p)
                write(self.root/'report.json',analyze(self.rows))
                write(self.root/'progress.json',{'status':'finished' if len(self.rows)==len(self.jobs) else 'bounded_stop',
                      'at':local_stamp(),'completed_jobs':len(self.rows),'total_jobs':len(self.jobs)})
                (self.root/'DONE').write_text(local_stamp()+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True)
    a=p.parse_args();MatrixSupervisor(a.root).run()
