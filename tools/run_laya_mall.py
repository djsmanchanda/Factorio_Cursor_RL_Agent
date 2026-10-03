#!/usr/bin/env python3
# Path: tools/run_laya_mall.py | Purpose: Prepare, inspect or execute bounded decisions on a separate disposable supplied-input mall.

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.run_laya_saved_world import prepare as prepare_host, manager
from training.laya_input_profile import MALL_PROFILE, require_input_profile
from training.laya_mall import scenario, decision_state
from training.laya_mall_supply import MALL_KIT
from training.laya_mall_world import MallWorld
from training.laya_saved_world import SavedWorldError, owned_root, file_hash


def prepare(args):
    root=owned_root(args.root)
    configuration=scenario(args.seed,f'{args.seed}-{uuid.uuid4().hex[:8]}')
    prepare_host(SimpleNamespace(root=root,source_save=args.source_save,mods_source=args.mods_source,
        game_port=args.game_port,rcon_port=args.rcon_port,runtime='disposable_mall',mall=configuration))
    world=None
    try:
        world=MallWorld(root);world.initialize();facts=world.query()
        if facts['provider_stock']!=MALL_KIT or any(facts['production'].values()):
            raise SavedWorldError('mall fixture does not match exact kit/zero crafting')
        if facts['robots']!={'construction':50,'logistic':50}:raise SavedWorldError('mall bot fixture mismatch')
        (root/'initial-state.json').write_text(json.dumps(facts,indent=2)+'\n')
        return {'root':str(root),'profile':MALL_PROFILE,'scope':{'surface':world.surface,'force':world.force},
                'starter_kit_matches':True,'source_preserved':file_hash(args.source_save)==world.manifest['source_sha256']}
    except Exception:
        if (root/'server/factorio.pid').exists():print(manager(root,'stop'),flush=True)
        raise
    finally:
        if world:world.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','observe','replay','run','stop'])
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--source-save',type=Path,default=ROOT/'saves/mod_playground.zip')
    p.add_argument('--mods-source',type=Path,default=Path.home()/'.local/share/factorio-rl/deterministic/mods')
    p.add_argument('--game-port',type=int,default=34196);p.add_argument('--rcon-port',type=int,default=27016)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--option-id');p.add_argument('--recipe')
    p.add_argument('--module',type=int);p.add_argument('--side',choices=['west','east','north','south'])
    p.add_argument('--full-menu',action='store_true');p.add_argument('--measure-ticks',type=int,default=600)
    p.add_argument('--checkpoint',type=Path);p.add_argument('--decisions',type=int,default=30)
    args=p.parse_args(argv)
    if not 1<=args.decisions<=100 or not 60<=args.measure_ticks<=1800:p.error('bounded decisions/measurement required')
    if args.action=='prepare':print(json.dumps(prepare(args)),flush=True);return 0
    root=owned_root(args.root)
    if args.action=='stop':print(manager(root,'stop'));return 0
    if args.action=='run':
        if not args.checkpoint:p.error('run requires a compatible mall checkpoint')
        config=json.loads((args.checkpoint/'rl_agent_config.json').read_text())
        require_input_profile(config,{'observation_digest':{'mall':{'version':MALL_PROFILE}}})
    world=MallWorld(root);history=[];measurement=None;director=None
    try:
        for index in range(args.decisions if args.action=='run' else 1):
            if args.action!='observe':world.reconcile()
            facts=world.query()
            if args.action=='run' and facts['elapsed_ticks']>=world.scenario['max_episode_ticks']:
                print(json.dumps({'status':'episode_budget_exhausted','elapsed_ticks':facts['elapsed_ticks']}),flush=True)
                break
            candidates,pruning=world.options(facts,args.seed+index,full=args.full_menu or args.action=='replay')
            observed={'facts':facts,'candidates':candidates,'pruning':pruning,'adapter_hashes':world.code_hashes}
            content=json.dumps(observed,indent=2)+'\n'
            folder=root/'evidence';folder.mkdir(exist_ok=True)
            observation=folder/f"observation-{facts['tick']}-{facts['revision']}.json"
            observation.write_text(content);(root/'latest-observation.json').write_text(content)
            if args.action=='observe':
                print(json.dumps({'elapsed_ticks':facts['elapsed_ticks'],'profile':MALL_PROFILE,
                    'queue':facts['queue'],'served':facts['served'],'ledger':facts['ledger'],
                    'options':[{'id':c['option_id'],'summary':c['summary']} for c in candidates]},indent=2));return 0
            if args.action=='replay':
                decision=None
                selected=args.option_id
                if args.recipe:
                    choices=[c for c in candidates if c.get('product')==args.recipe and c.get('module')==args.module and c.get('side')==args.side]
                    if len(choices)!=1:raise SavedWorldError('requested mechanics slot is unavailable')
                    selected=choices[0]['option_id']
                if not selected:p.error('replay requires --option-id or --recipe/--module/--side')
                authority='mechanics_replay'
            else:
                if director is None:
                    from training.laya_runtime import LayaModelPredictor
                    from training.laya_workflow import LayaWorkflowDirector
                    director=LayaWorkflowDirector(LayaModelPredictor(args.checkpoint),budget_profile='director-2048')
                from training.laya_runner import checkpoint_model_ref
                state=decision_state(facts,candidates,root.name,checkpoint_model_ref(args.checkpoint),history,measurement)
                decision=director.decide('select_candidate',state,state['options'])
                selected=decision['selected_option']['option_id']
                authority='disposable_mall'
            record={'tick':facts['tick'],'authority':authority,'selected':selected,'status':'pending','comparison_eligible':False,
                    'profile':MALL_PROFILE,'candidates':candidates,'decision':decision,
                    'observation':str(observation.relative_to(root)),'observation_sha256':file_hash(observation)}
            journal=root/'decisions.jsonl'
            with journal.open('a') as out:out.write(json.dumps(record)+'\n')
            receipt=world.execute(selected,candidates,args.measure_ticks)
            measurement=receipt['measurement'];record.update(status=receipt['status'],measurement=measurement)
            with journal.open('a') as out:out.write(json.dumps(record)+'\n')
            history.append({'decision_id':f'mall.{index}','tick':facts['tick'],'summary':selected,
                            'outcome':'completed' if receipt['status']=='observed' else 'pending'})
            print(json.dumps({k:record[k] for k in ('tick','authority','selected','status','measurement')}),flush=True)
            if measurement['completed']:break
    finally:world.close()
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (SavedWorldError,ValueError) as error:
        print(f'mall run stopped: {error}',file=sys.stderr,flush=True);raise SystemExit(2)
