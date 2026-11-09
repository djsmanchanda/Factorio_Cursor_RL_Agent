#!/usr/bin/env python3
# Path: tools/run_laya_mall_fleet.py | Purpose: Supervise twenty isolated mall population-RL sessions under one explicit deadline and preserve outcome-qualified Laya cases.

import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from copy import deepcopy
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
import numpy as np
from orchestrator.laya_operations import sha256_hash
from tools.run_laya_saved_world import prepare as prepare_host,manager
from training.laya_mall import scenario,decision_state
from training.laya_mall_supply import MALL_KIT
from training.laya_mall_world import MallWorld
from training.laya_saved_world import verify_instance
from training.laya_mall_growth import reward,VERSION as REWARD_VERSION
from training.laya_mall_population import action_vector,choose,initial_population,evolve,VERSION as POLICY_VERSION
from training.laya_dataset import seed_split,validate_case
from training.laya_workflow import QUESTION_INSTRUCTIONS


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp');temp.write_text(json.dumps(value,indent=2)+'\n');temp.replace(path)


def scenario_split(configuration):
    return seed_split(int(configuration['scenario_hash'][7:15],16)&0x7fffffff)


def select_seed(start,split):
    for seed in range(start,65536):
        if scenario_split(scenario(seed,'group'))==split:return seed
    raise ValueError('scenario seed bank exhausted')


def episode(job):
    # Each process owns exactly one engine/clock, including its fixture reset.
    root=Path(job['root']);world=MallWorld(root);rows=[];total=0;previous_rate=0;history=[];measurement=None
    rng=np.random.default_rng(job['seed']*100+job['worker'])
    try:
        cfg=scenario(job['seed'],job['identity'])
        if world.scenario['scenario_hash']!=cfg['scenario_hash'] or job['round']>0:
            world.reset_episode(cfg,f"r{job['round']:04d}-seed-{world.scenario['seed']}")
        facts=world.query()
        if facts['provider_stock']!=MALL_KIT or any(facts['production'].values()):raise RuntimeError('episode fixture is not fresh')
        for step in range(job['max_decisions']):
            if time.time()>=job['deadline'] or (Path(job['run'])/'STOP').exists():break
            world.reconcile();before=world.query();remaining=cfg['max_episode_ticks']-before['elapsed_ticks']
            if remaining<2*job['measure_ticks']:break
            menu,_=world.options(before,job['seed']*1000+step+job['worker'],full=False)
            selected=choose(job['weights'],before,menu,rng,epsilon=job['epsilon'])
            c=menu[selected]
            state=decision_state(before,menu,f"mall-r{job['round']}-w{job['worker']}",
                {'model_id':POLICY_VERSION,'model_hash':sha256_hash(job['weights'])},history,measurement)
            pending={'step':step,'state':state,'candidates':menu,'selected':c['option_id'],'before':before,'status':'pending'}
            trace=Path(job['run'])/'episodes'/f"r{job['round']:04d}-w{job['worker']:02d}.jsonl"
            trace.parent.mkdir(exist_ok=True)
            with trace.open('a') as out:out.write(json.dumps(pending)+'\n')
            result=world.execute(c['option_id'],menu,job['measure_ticks']);after=world.query()
            measured=reward(before,after,previous_rate=previous_rate,
                            upgrade_tick=result.get('operation',{}).get('tick') if c['family']=='upgrade' else None)
            previous_rate=measured['needed_production_rate'];total+=measured['reward'];measurement=result['measurement']
            row={**pending,'status':result['status'],'after':after,'reward':measured};rows.append(row)
            with trace.open('a') as out:out.write(json.dumps(row)+'\n')
            history.append({'decision_id':f'mall.{step}','tick':before['tick'],'summary':c['summary'],
                            'outcome':'completed' if result['status']=='observed' else 'pending'})
            write_json(root/'training-state.json',{'round':job['round'],'worker':job['worker'],'seed':job['seed'],'step':step,
                'split':scenario_split(cfg),'fitness':total,'elapsed_ticks':after['elapsed_ticks'],
                'upgrades':after['ledger']['upgrades'],'authority':'disposable_mall_training','policy':POLICY_VERSION,
                'selected':c['summary'],'reward':measured,'pending_ghosts':after['pending_ghosts']})
        # Evaluate remaining physics at the same fixed horizon; this is not another policy decision.
        before=world.query();remaining=cfg['max_episode_ticks']-before['elapsed_ticks']
        while remaining>0 and time.time()<job['deadline'] and not (Path(job['run'])/'STOP').exists():
            world.advance(min(remaining,600));world.reconcile();after=world.query()
            measured=reward(before,after,previous_rate=previous_rate);previous_rate=measured['needed_production_rate']
            total+=measured['reward'];before=after;remaining=cfg['max_episode_ticks']-after['elapsed_ticks']
        final=world.query();complete=final['elapsed_ticks']==cfg['max_episode_ticks']
        result={'worker':job['worker'],'round':job['round'],'seed':job['seed'],'scenario_hash':cfg['scenario_hash'],
                'split':scenario_split(cfg),'fitness':total,'complete_horizon':complete,'decisions':len(rows),
                'upgrades':final['ledger']['upgrades'],'pending_ghosts':final['pending_ghosts'],
                'pending_upgrades':final.get('pending_upgrades',0),'final':final,'status':'observed' if complete else 'interrupted'}
        write_json(Path(job['run'])/'results'/f"r{job['round']:04d}-w{job['worker']:02d}.json",result)
        return result
    except Exception as error:
        failure={'worker':job['worker'],'round':job['round'],'seed':job['seed'],'status':'quarantined','error':str(error),'fitness':-1}
        write_json(Path(job['run'])/'results'/f"r{job['round']:04d}-w{job['worker']:02d}.json",failure)
        return failure
    finally:world.close()


def export_cases(run,results):
    count=0
    with (run/'cases.jsonl').open('a') as out:
        for result in results:
            if not (result['status']=='observed' and result['upgrades']>0 and not result['pending_ghosts'] and not result['pending_upgrades']):continue
            trace=run/'episodes'/f"r{result['round']:04d}-w{result['worker']:02d}.jsonl"
            for line in trace.read_text().splitlines():
                row=json.loads(line)
                if row['status'] not in {'observed','upgrade_pending','construction_pending'}:continue
                confidence=.2;options=row['candidates'];selected=row['selected']
                case={'version':'1.0.0','case_id':'mall.'+sha256_hash(row)[7:31],
                      'source':{'kind':'rl_transition','run_id':run.name,'episode_id':trace.stem,
                                'scenario_id':'mall.'+result['scenario_hash'][7:23],
                                'scenario_seed':int(result['scenario_hash'][7:15],16)&0x7fffffff,'scenario_hash':result['scenario_hash'],
                                'provenance_hash':sha256_hash(row)},'state':row['state'],
                      'questions':{'select_candidate':{'type':'choice','instructions':QUESTION_INSTRUCTIONS['select_candidate'],
                                'criteria':{c['option_id']:c['summary'] for c in options}}},
                      'gold':{'select_candidate':{'label':selected,'probabilities':{c['option_id']:float(c['option_id']==selected) for c in options},
                                'label_source':'outcome','confidence':confidence}},
                      'weights':{'case_weight':confidence,'question_weights':{'select_candidate':1},'source_quality':confidence},
                      'created_at':datetime.now(timezone.utc).isoformat(),'dataset_version':'1.0.0','split':result['split'],
                      'label_provenance':{'method':'hindsight','evidence_hash':sha256_hash(row),
                           'notes':REWARD_VERSION+'; successful measured population trajectory; untried actions are not counterfactual losers; no promotion'}}
                validate_case(case);out.write(json.dumps(case)+'\n');count+=1
    return count


def bootstrap_worker(job):
    root=Path(job['root'])
    configuration=scenario(job['seed'],job['identity'])
    prepare_host(SimpleNamespace(root=root,source_save=Path(job['source_save']),mods_source=Path(job['mods_source']),
        game_port=34200+job['worker'],rcon_port=27100+job['worker'],runtime='disposable_mall',mall=configuration))
    w=MallWorld(root)
    try:
        w.initialize();facts=w.query()
        assert facts['provider_stock']==MALL_KIT and not any(facts['production'].values())
        write_json(root/'initial-state.json',facts)
        return {'worker':job['worker'],'root':str(root),'game_port':34200+job['worker'],'rcon_port':27100+job['worker'],'ready':True}
    finally:w.close()


def student_gate(run):
    counts={};groups={}
    path=run/'cases.jsonl'
    if path.exists():
        for line in path.read_text().splitlines():
            case=json.loads(line);split=case['split'];counts[split]=counts.get(split,0)+1
            groups.setdefault(split,set()).add(case['source']['scenario_hash'])
    ready=counts.get('train',0)>=100 and counts.get('calibration',0)>=40 and len(groups.get('train',()))>=2 and len(groups.get('calibration',()))>=2
    return {'ready':ready,'counts':counts,'scenario_groups':{k:len(v) for k,v in groups.items()}}


def train_student(run,base_model,deadline):
    gate=student_gate(run);write_json(run/'student-gate.json',gate)
    if not gate['ready']:return False
    env=os.environ.copy();env.update(LAYA_CONTEXT_PROFILE='director-2048',LAYA_MAX_LEN='2048',LAYA_HEAD_MAX_LEN='256',
        LAYA_MICRO_BATCH='1',LAYA_GRADIENT_ACCUMULATION='32',LAYA_GROUP_SIZE='1',LAYA_EPOCHS='2',LAYA_GRADIENT_CHECKPOINTING='1')
    probe=subprocess.run([sys.executable,str(ROOT/'tools/laya_preflight.py'),'--max-len','2048'],capture_output=True,text=True,env=env,timeout=120)
    write_json(run/'gpu-preflight.json',json.loads(probe.stdout))
    if probe.returncode:return False
    output=run/'mall-text-checkpoint'
    with (run/'student-training.log').open('w') as log:
        trainer=subprocess.Popen([sys.executable,str(ROOT/'tools/train_laya_4060.py'),'--cases',str(run/'cases.jsonl'),
            '--model-dir',str(base_model),'--output-dir',str(output)],stdout=log,stderr=subprocess.STDOUT,env=env)
        try:status=trainer.wait(timeout=max(1,deadline-time.time()-180))
        except subprocess.TimeoutExpired:
            trainer.terminate()
            try:trainer.wait(timeout=90)
            except subprocess.TimeoutExpired:trainer.kill();trainer.wait()
            write_json(run/'student-result.json',{'status':'deadline_interrupted','checkpoint':str(output)});return True
    write_json(run/'student-result.json',{'status':'trained' if status==0 else 'failed','exit_code':status,'checkpoint':str(output),'promotion':False})
    if status==0 and time.time()<deadline-180 and gate['counts'].get('held_out',0):
        with (run/'student-held-out.log').open('w') as log:
            try:
                subprocess.run([sys.executable,str(ROOT/'tools/evaluate_laya.py'),'--cases',str(run/'cases.jsonl'),'--checkpoint',str(output),
                    '--base-model',str(base_model),'--split','held_out','--output',str(output/'evaluation-held-out.json')],
                    stdout=log,stderr=subprocess.STDOUT,env=env,timeout=max(1,deadline-time.time()-120),check=True)
            except (subprocess.TimeoutExpired,subprocess.CalledProcessError):pass
    return True


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True)
    p.add_argument('--source-save',type=Path,required=True);p.add_argument('--mods-source',type=Path,required=True)
    p.add_argument('--hours',type=float,default=8);p.add_argument('--start-seed',type=int,default=21000)
    p.add_argument('--base-model',type=Path)
    p.add_argument('--measure-ticks',type=int,default=300);p.add_argument('--max-decisions',type=int,default=80)
    args=p.parse_args(argv)
    if not 0<args.hours<=8 or not 60<=args.measure_ticks<=600 or not 1<=args.max_decisions<=200:p.error('bounded duration/decisions required')
    run=args.run.resolve();run.mkdir(parents=True,exist_ok=True)
    if (run/'manifest.json').exists():p.error('fresh campaign root required')
    deadline=time.time()+args.hours*3600;stopping=False
    def stop(signum,frame):
        nonlocal stopping
        stopping=True;(run/'STOP').touch()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    os.environ.update(OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')
    first=select_seed(args.start_seed,'train')
    jobs=[{'worker':i,'identity':f'{run.name}-w{i:02d}','root':str(ROOT/'data/laya-saved-world'/f'{run.name}-w{i:02d}'),
           'run':str(run),'seed':first,'round':0,'deadline':deadline,'max_decisions':args.max_decisions,
           'measure_ticks':args.measure_ticks,'epsilon':.25,'source_save':str(args.source_save.resolve()),'mods_source':str(args.mods_source.resolve())} for i in range(20)]
    manifest={'version':'mall-fleet-v1','sessions':20,'deadline_utc':datetime.fromtimestamp(deadline,timezone.utc).isoformat(),
              'policy':POLICY_VERSION,'reward':REWARD_VERSION,'kit':MALL_KIT,'production_authority':False,
              'worker_roots':[j['root'] for j in jobs],'source_save':str(args.source_save),'hours':args.hours,
              'fine_tune_gate':'qualified growth trajectories + independent calibration + GPU readiness; never promote'}
    write_json(run/'manifest.json',manifest)
    results=[];pop=None;round_index=0;next_seed=first;student_attempted=False
    try:
        with ProcessPoolExecutor(max_workers=4) as pool:
            ready=list(pool.map(bootstrap_worker,jobs))
        write_json(run/'startup.json',{'workers':ready,'ready_count':len(ready)})
        w=MallWorld(Path(jobs[0]['root']))
        try:
            f=w.query();menu,_=w.options(f);width=len(action_vector(f,menu[0]))
        finally:w.close()
        pop=initial_population(width,first)
        while not stopping and time.time()<deadline-120:
            split=('train','train','calibration','train','held_out')[round_index%5]
            seed=select_seed(next_seed,split);next_seed=seed+1
            round_jobs=[{**j,'round':round_index,'seed':seed,'weights':pop[i]} for i,j in enumerate(jobs)]
            write_json(run/f'population-r{round_index:04d}.json',{'population':pop,'seed':seed,'split':split})
            write_json(run/'status.json',{'phase':'collecting','round':round_index,'seed':seed,'split':split,'sessions':20,'deadline':manifest['deadline_utc']})
            with ProcessPoolExecutor(max_workers=20) as pool:
                futures=[pool.submit(episode,j) for j in round_jobs]
                cohort=[f.result() for f in as_completed(futures)]
            cohort.sort(key=lambda r:r['worker']);results.extend(cohort)
            write_json(run/f'cohort-r{round_index:04d}.json',cohort)
            count=export_cases(run,cohort)
            if any(r['status']=='quarantined' for r in cohort):
                write_json(run/'status.json',{'phase':'quarantined','round':round_index,'errors':[r for r in cohort if r['status']=='quarantined']});break
            if split=='train' and all(r['complete_horizon'] for r in cohort):pop=evolve(pop,[r['fitness'] for r in cohort],first+round_index)
            write_json(run/'latest-policy.json',{'version':POLICY_VERSION,'weights':pop[0],'reward':REWARD_VERSION,'trained_input':'mall-growth-v2',
                                               'round':round_index,'input_profile':'mall-world-v1','text_checkpoint':False})
            print(json.dumps({'round':round_index,'split':split,'upgrades':sum(r['upgrades'] for r in cohort),'qualified_cases':count,'sessions':20}),flush=True)
            round_index+=1
            if args.base_model and not student_attempted and time.time()<deadline-1800 and student_gate(run)['ready']:
                write_json(run/'status.json',{'phase':'student_training','sessions':20,'deadline':manifest['deadline_utc']})
                student_attempted=train_student(run,args.base_model,deadline)
    finally:
        (run/'STOP').touch()
        cleaned=[]
        for job in jobs:
            root=Path(job['root'])
            if (root/'server/factorio.pid').exists():
                try:
                    verify_instance(root,runtime='disposable_mall')
                    cleaned.append({'root':str(root),'status':manager(root,'stop')})
                except Exception as error:cleaned.append({'root':str(root),'error':str(error)})
        write_json(run/'cleanup.json',cleaned)
        write_json(run/'summary.json',{'completed_rounds':round_index,'observed_episodes':len(results),'stopped':True,'production_authority':False})
    return 0


if __name__=='__main__':raise SystemExit(main())
