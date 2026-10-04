#!/usr/bin/env python3
# Path: tools/run_laya_mall_followup.py | Purpose: Frozen mall-slot continuation screens for an eight-hour window, with separate predecessor evidence and seeded replication.
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import signal
import random
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from core.local_time import local_stamp
from tools.run_laya_context_suite import Supervisor,analyze,write
from tools.laya_experiment_helper import exclusive
from training.laya_checkpoint import load_bundle


def matrix(bundles):
    rules={r['rule_id']:r.get('prefer',[]) for r in json.loads((ROOT/'docs/rl/laya_mission_guidelines.json').read_text())['rules']}
    variants={};arms=[('baseline',{},None),('slots',{'mall_slots':True},None)]
    arms += [(f'gate-{v:g}',{'mall_slots':True,'mall_gate_scale':v},None) for v in [.5,.75,1.5,2]]
    arms += [(f'requests-{v}',{'mall_slots':True,'mall_buildable_multiplier':v},None) for v in [1,3,8,15]]
    for rule,values in [('mission.rotating-mall',[.5,1.5,3]),('mission.stock-gates',[.5,2]),('mission.slot-settle',[.5,2]),
                        ('mission.purposeful-coverage',[.5,2])]:
        for factor in values:
            prefs=deepcopy(rules[rule])
            for p in prefs:p['weight']*=factor
            arms.append((f'{rule.removeprefix("mission.")}-{factor:g}',{'mall_slots':True},{rule:{'prefer':prefs}}))
    arms += [(f'context-{v}',{'mall_slots':True,'context_view':v},None) for v in ['dependency','combined']]
    arms += [('combined-gate-request',{'mall_slots':True,'mall_gate_scale':1.5,'mall_buildable_multiplier':3,'context_view':'combined'},None)]
    for name,settings,patch in arms:
        v={'version':'1.0.0','variant_id':name.replace('.','p'),'description':'Matched earned-C1 mall-slot follow-up; no adoption.',
           'settings':{'stall_decisions':48,**settings}}
        if patch:v['catalog_patch']=patch
        variants[v['variant_id']]=v
    jobs=[]
    for bundle in bundles:load_bundle(bundle)
    for seed in [62000,62001,62002]:
        for index,bundle in enumerate(bundles):
            for window in [120,180]:
                for name,settings,patch in arms:
                    jobs.append({'kind':'raw','phase':'C2','category':'mall-slot-followup','case':f'checkpoint-{index}-{window}min',
                         'variant':name.replace('.','p'),'seed':seed,'resume_bundle':str(bundle),'speed':4,
                         'max_ticks':window*3600,'timeout':window*15+600,
                         'window_game_minutes':window})
    controls=[j for j in jobs if j['variant'] in {'baseline','slots'}]
    screens=[j for j in jobs if j['variant'] not in {'baseline','slots'}]
    for seed in [63000,63001,63002]:
        for variant in ['baseline','context-dependency','context-combined']:
            for family,transport,fault in [('circuit','belt','machine'),('circuit','belt','pole'),('circuit','belt','belt'),
                                           ('circuit','bots','machine'),('plastic','belt','machine'),('plastic','belt','pole')]:
                component={'family':family,'transport':transport,'fault':fault}
                if family=='circuit':component.update(producers=3,consumers=2)
                screens.append({'kind':'component','phase':'COMPONENT','category':'repair-replication',
                    'case':f'{family}-{transport}-{fault}','component':component,'variant':variant,'seed':seed})
    random.Random(20261004).shuffle(screens)
    jobs=controls+screens
    for i,j in enumerate(jobs):j['job_id']=f'followup-{i:04d}'
    return variants,jobs


class Followup(Supervisor):
    def variant(self,name):return Path(self.cfg['variant_directory'])/(name+'.json')

    def checkin(self,hour):
        self.checkpoint(hour)
        previous=Path(self.cfg['previous_root'])
        prior=json.loads((previous/'progress.json').read_text()) if (previous/'progress.json').exists() else None
        write(self.root/f'window-checkin-{hour}h.json',{'at':local_stamp(),'followup':analyze(self.rows),'predecessor_progress':prior,
              'predecessor_analysis_file':str(previous/'analysis.json'),'domains_combined':False})

    def tick(self,status,active=0,round_index=0):
        write(self.root/'progress.json',{'status':status,'at':local_stamp(),'completed_jobs':len(self.rows),'active_jobs':active,
              'round':round_index,'initial_queue_jobs':self.cfg['initial_queue_jobs'],
              'remaining_seconds':max(0,int(self.deadline-time.time())),'code_commit':self.cfg['code_commit']})
        write(self.root/'analysis.json',analyze(self.rows))
        while time.time()>=self.next_check and self.check_hour<=8:
            self.checkin(self.check_hour);self.next_check+=7200;self.check_hour+=2

    def run(self):
        base=json.loads((self.root/'queue.json').read_text());self.next_check=self.cfg['started_epoch']+7200;self.check_hour=2
        signal.signal(signal.SIGTERM,lambda *_:self.stop.set());signal.signal(signal.SIGINT,lambda *_:self.stop.set())
        with exclusive(self.root,'mall-followup'):
            previous=Path(self.cfg['previous_root']);round_index=0
            try:
                while time.time()<self.deadline-120 and not self.stop.is_set():
                    state=subprocess.run(['systemctl','--user','is-active',self.cfg['previous_actor_unit']],capture_output=True,text=True).stdout.strip()
                    if (previous/'DONE').exists() and state in {'inactive','failed','unknown'}:break
                    self.tick('queued');time.sleep(5)
                self.event({'event':'followup_started','at':local_stamp(),'predecessor':str(previous)})
                with ThreadPoolExecutor(max_workers=self.cfg['parallel']) as pool:
                    while time.time()<self.deadline-120 and not self.stop.is_set():
                        # New seeds are replication, never automatic adoption or positive labels.
                        jobs=[{**j,'seed':j['seed']+round_index*1000,'replication_round':round_index} for j in base]
                        for offset in range(0,len(jobs),self.cfg['parallel']):
                            if self.stop.is_set() or time.time()>=self.deadline-120:break
                            futures=[pool.submit(self.job,j,slot,f'r{round_index}-{j["job_id"]}') for slot,j in enumerate(jobs[offset:offset+self.cfg['parallel']])]
                            while futures:
                                for f in [f for f in futures if f.done()]:f.result();futures.remove(f)
                                self.tick('running',len(futures),round_index);time.sleep(5)
                        round_index+=1
            finally:
                self.stop.set()
                for p in list(self.running.values()):self.terminate(p)
                write(self.root/'report.json',analyze(self.rows));self.checkin(8)
                self.tick('finished',round_index=round_index);(self.root/'DONE').write_text(local_stamp()+'\n')


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,required=True);a=p.parse_args();Followup(a.root).run()
