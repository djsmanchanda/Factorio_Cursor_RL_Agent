# Path: training/laya_mall_world.py | Purpose: Execute guarded mall decisions with deterministic input quanta and durable engine-owned receipts.

from copy import deepcopy
import json
from pathlib import Path
import time
import uuid
from jsonschema import Draft7Validator

from orchestrator.game_bridge import GameBridge, load_json
from orchestrator.laya_operations import sha256_hash
from planners.laya_quad_cell import quad_reservation_reason
from training.laya_input_profile import MALL_PROFILE
from training.laya_mall import compile_candidates, bounded_menu, outcome
from training.laya_mall_supply import new_supply, accrue_supply, record_delivery, commit_upgrade
from training.laya_saved_world import SavedWorldAdapter, SavedWorldError, owned_root, verify_instance, file_hash

FACTS_VALIDATOR=Draft7Validator(json.loads((Path(__file__).resolve().parents[1]/'schemas/laya_mall_world.schema.json').read_text()))


class MallWorld(SavedWorldAdapter):
    runtime = 'disposable_mall'

    def __init__(self, root):
        self.root = owned_root(Path(root))
        self.manifest = verify_instance(self.root, runtime=self.runtime)
        self.scenario = self.manifest['mall']
        identity=sha256_hash({k:v for k,v in self.scenario.items() if k not in {'seed','surface','force','scenario_hash'}})
        if self.manifest['version']!=MALL_PROFILE or identity!=self.scenario['scenario_hash']:
            raise SavedWorldError('mall configuration hash mismatch')
        self.surface, self.force = self.scenario['surface'], self.scenario['force']
        if not self.surface.startswith('laya-mall/') or not self.force.startswith('laya-mall-'):
            raise SavedWorldError('mall requires its own training surface/force')
        self.lua_source = Path(__file__).with_suffix('.lua').read_text()
        repo=Path(__file__).resolve().parents[1]
        paths = [Path(__file__), Path(__file__).with_suffix('.lua'), Path(__file__).with_name('laya_mall.py'),
                 Path(__file__).with_name('laya_mall_supply.py'), Path(__file__).with_name('laya_saved_world.py'),
                 Path(__file__).with_name('laya_input_profile.py'),repo/'schemas/laya_mall_world.schema.json',
                 Path(__file__).with_name('laya_mall_view.py'),
                 repo/'tools/run_laya_mall.py',repo/'tools/run_laya_saved_world.py',repo/'planners/laya_quad_cell.py',
                 repo/'planners/quad_mall_layout.py',repo/'planners/mall_layout.py']
        self.code_hashes = {str(p): file_hash(p) for p in paths}
        server = self.root/'server'
        self.bridge = GameBridge(server/'script-output', host='127.0.0.1', port=self.manifest['rcon_port'],
                                 password=(server/'rcon-password').read_text().strip(), command_timeout=30)

    def require_clear_mutations(self, retry_nonce=None):
        for path in (self.root/'rpc').glob('*.json'):
            if path.stem!=retry_nonce and load_json(path)['status'] in {'pending','ambiguous'}:
                raise SavedWorldError('interrupted mall RPC needs review: '+path.name)

    def rpc(self, op, **fields):
        verify_instance(self.root, runtime=self.runtime)
        request = {'op':op, 'scenario':self.scenario, **fields}
        nonce = request.get('nonce')
        path = None
        if nonce:
            self.require_clear_mutations(nonce)
            request['operation_hash'] = sha256_hash({k:v for k,v in request.items() if k!='operation_hash'})
            path=self.root/'rpc'/f'{nonce}.json'; path.parent.mkdir(exist_ok=True)
            if path.exists():
                stored=load_json(path)
                if stored['request']!=request: raise SavedWorldError('mutation nonce reused')
            else:path.write_text(json.dumps({'status':'pending','request':request})+'\n')
        serialized=json.dumps(request,separators=(',',':'))
        delim='='
        while f']{delim}]' in serialized:delim+='='
        source=' '.join(line.split('--',1)[0] for line in self.lua_source.splitlines())
        body=f'local request=helpers.json_to_table([{delim}[{serialized}]{delim}]); '+source
        name=f'laya_mall/{uuid.uuid4().hex}.json'
        self.bridge.command(f'/sc local ok,v=pcall(function() {body} end); helpers.write_file("{name}",helpers.table_to_json({{ok=ok,value=ok and v or nil,error=not ok and tostring(v) or nil,pending_operation=storage.laya_mall and storage.laya_mall.pending_operation}}),false); rcon.print("mall-report")')
        result=load_json(self.bridge.script_output/name)
        if path:path.write_text(json.dumps({'status':'ambiguous' if result.get('pending_operation') else ('observed' if result['ok'] else 'failed'),'request':request,'result':result})+'\n')
        if not result['ok']: raise SavedWorldError(result['error'])
        return result['value']

    def initialize(self):
        self.bridge.command('/sc game.tick_paused=true; game.ticks_to_run=0; rcon.print("mall-paused")')
        return self.rpc('initialize',initial_ledger=new_supply(0,{}))

    def query(self, candidates=None, inspect=None):
        if candidates is not None:
            result=self.rpc('validate',candidates=candidates)
            for c in candidates:
                reason=quad_reservation_reason(c['plan'],self.scenario['centers'])
                if reason:result['validations'][c['option_id']]={'reason':reason}
            return result
        if inspect is not None:return self.rpc('inspect',plan=inspect)
        facts=self.rpc('observe')
        if facts.get('machines')=={}:facts['machines']=[]
        if facts['ledger'].get('consumption_receipts')=={}:facts['ledger']['consumption_receipts']=[]
        if facts['version']!=MALL_PROFILE or facts['surface']!=self.surface or facts['force']!=self.force:
            raise SavedWorldError('mall facts identity mismatch')
        errors=list(FACTS_VALIDATOR.iter_errors(facts))
        if errors:raise SavedWorldError('mall facts contract: '+errors[0].message)
        return facts

    def options(self, facts, seed=0, *, full=False):
        proposed=compile_candidates(facts)
        builds=[c for c in proposed if c.get('plan')]
        results=self.query(builds)['validations'] if builds else {}
        legal=[]
        for c in proposed:
            if not c.get('plan'):legal.append(c)
            elif not results[c['option_id']].get('reason'):legal.append({**c,'bill':results[c['option_id']].get('bill') or {}})
        return (legal if full else bounded_menu(legal,seed)), {'proposed':proposed,'validations':results}

    def advance(self, ticks, *, speed=16):
        if type(ticks) is not int or not 1<=ticks<=18000:raise SavedWorldError('mall step must be 1..18000 ticks')
        if type(speed) is not int or speed not in (1,16):raise SavedWorldError('mall speed must be 1 for viewing or 16 for training')
        self.require_clear_mutations()
        before=self.query()
        if before['elapsed_ticks']+ticks>self.scenario['max_episode_ticks']:raise SavedWorldError('mall episode tick budget exhausted')
        for offset in range(0,ticks,60):
            quantum=min(60,ticks-offset)
            start=self.query()
            self.bridge.command(f'/sc game.tick_paused=true; game.speed={speed}; game.ticks_to_run={quantum}')
            deadline=time.monotonic()+30
            while time.monotonic()<deadline:
                state=json.loads(self.bridge.command('/sc rcon.print(helpers.table_to_json({tick=game.tick,paused=game.tick_paused,remaining=game.ticks_to_run}))'))
                if state['paused'] and state['remaining']==0:
                    if state['tick']!=start['tick']+quantum:raise SavedWorldError('mall clock drift')
                    break
                time.sleep(.05)
            else:raise SavedWorldError('mall tick step timed out')
            predicted=deepcopy(start['ledger'])
            accrue_supply(predicted,state['tick'])
            receipt=self.rpc('tick',nonce=uuid.uuid4().hex,revision=start['revision'],tick=state['tick'])
            record_delivery(predicted,receipt['inserted'])
            after=self.query()
            if predicted!=after['ledger']:raise SavedWorldError('engine/Python supply ledger mismatch')

    def execute(self, selected, presented, measure_ticks=600):
        self.require_clear_mutations()
        if type(measure_ticks) is not int or not 1<=measure_ticks<=18000:
            raise SavedWorldError('mall measurement must be 1..18000 ticks')
        choices=[c for c in presented if c['option_id']==selected]
        if len(choices)!=1:raise SavedWorldError('mall choice must match one presented option')
        c=choices[0]; before=self.query()
        if c['at_revision']!=before['revision'] or c['at_tick']!=before['tick']:
            raise SavedWorldError('stale mall selection')
        registered={k:v for k,v in c.items() if k!='bill'}
        if registered not in compile_candidates(before):raise SavedWorldError('mall action is not currently registered')
        cost=2*measure_ticks if c['family']=='assemble' else c.get('wait_ticks',60)
        if before['elapsed_ticks']+cost>self.scenario['max_episode_ticks']:
            raise SavedWorldError('mall action exceeds remaining episode ticks')
        if c['family'] in {'assemble','wait'}:
            receipt=super().execute(selected,presented,measure_ticks)
            receipt['measurement']=outcome(before,self.query())
            # Persist the mall outcome alongside the inherited funded-build evidence.
            path=self.root/'evidence'/f"mall-outcome-{uuid.uuid4().hex}.json"
            path.write_text(json.dumps(receipt,indent=2)+'\n')
            return receipt
        if c['family'] not in {'priority','deliver','upgrade','toggle'}:raise SavedWorldError('unsupported mall decision')
        nonce=uuid.uuid4().hex
        fields={'nonce':nonce,'revision':before['revision'],'tick':before['tick']}
        if c['family']=='upgrade':fields.update(upgrade=c['upgrade'],plan_hash=sha256_hash(c['upgrade']))
        elif c['family']=='toggle':fields.update(position=c['position'],active=c['active'])
        else:fields['goal']=c['goal']
        if c['family']=='deliver':fields['count']=c['count']
        result=self.rpc(c['family'],**fields)
        after=self.query()
        if c['family']=='upgrade':
            expected=deepcopy(before['ledger']); commit_upgrade(expected,c['upgrade'],result)
            if expected!=after['ledger']:raise SavedWorldError('engine/Python consumed-upgrade ledger mismatch')
        self.advance(60)
        after=self.query()
        receipt={'status':'observed','candidate':c,'before':before,'after':after,
                 'operation':result,'measurement':outcome(before,after),'adapter_hashes':self.code_hashes}
        path=self.root/'evidence'/f'transaction-mall-{nonce}.json';path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(receipt,indent=2)+'\n')
        return receipt

    def checkpoint(self):
        prefix=super().checkpoint()
        prefix.update(mall=self.scenario,scenario_hash=self.scenario['scenario_hash'])
        return prefix
