# Path: training/laya_component_lab.py | Purpose: Reuse assembly/fluid/chain realizers for explicitly fixtured circuit and plastic interaction experiments.
from __future__ import annotations
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import uuid
from jsonschema import Draft7Validator
from orchestrator.game_bridge import load_json
from planners.local_layout_planner import LocalLayoutPlanner
from planners.mall_layout import generate_paired_mall_layout
from planners.recipe_data import LINE_RECIPES
from planners.fluid_layouts import generate_fluid_machine_row, header_attachment, fluid_network_segments
from planners.fluid_routing import generate_shortest_fluid_chain_link
from planners.plan_validation import actions, validate_build_plan, entity_footprint_tiles
from training.laya_saved_world import SavedWorldAdapter, verify_instance, submission_plan, configuration_actions, measured_outcome
from tools.run_laya_smelting_test import Lab


def ghosts(plan):
    """Discard sandbox power scaffolding; turn real source-side inserters into funded ghosts."""
    placed=[deepcopy(a) for a in actions(plan) if a['action_type']=='place_ghost']
    feeds=[]
    for a in actions(plan):
        if a['action_type']=='place_entity' and a['entity']=='infinity-chest':
            feeds.append({'kind':'chest','entity':'steel-chest','position':a['position'],'item':a['infinity_filter']})
        elif a['action_type']=='place_entity' and a['entity'] in {'inserter','fast-inserter'}:
            placed.append({**a,'action_type':'place_ghost'})
    return placed,feeds


def component_spec(family, *, transport='belt', producers=3, consumers=2, input_share=1.0, delay_ticks=0, request_multiplier=1,
                   supply_distance=20, spacing=0, logistic_robots=1100, interruption_ticks=0, interruption_source='all',
                   sink_pause_ticks=0, fault='none'):
    if not 1<=producers<=6 or not 1<=consumers<=4 or not 0<input_share<=2:raise ValueError('invalid component scale/share')
    if not 6<=supply_distance<=48 or not 0<=spacing<=24 or not 11<=logistic_robots<=2200:raise ValueError('invalid logistics fixture')
    if fault not in {'none','belt','machine','pole'}:raise ValueError('invalid component fault')
    groups={};sources=[];sinks=[];fluid_segments=[]
    if family=='circuit':
        recipe='electronic-circuit';target=3.0;planner=LocalLayoutPlanner()
        if transport=='belt':
            cable=planner.generate_line_layout('copper-cable',producers,belt_type='fast-transport-belt',feed_style='chained',chained_ingredients={0},terminal_collector=False)
            cable_actions,_=ghosts(cable);groups['copper-cable']=cable_actions
            x=producers*3+5;y=18+spacing
            circuit=planner.generate_line_layout(recipe,consumers,x,y,belt_type='fast-transport-belt',feed_style='chained',chained_ingredients={0},terminal_collector=False)
            circuit_actions,feeds=ghosts(circuit);groups[recipe]=circuit_actions
            sources.append({'kind':'belt','entity':'fast-transport-belt','position':{'x':-2.5,'y':.5},'item':'copper-plate','rate':4.5*input_share})
            for f in feeds:sources.append({**f,'rate':3.0*input_share/len(feeds)})
            link=planner.generate_chain_link((0,0),'copper-cable',producers,(x,y),recipe,consumers,belt_type='fast-transport-belt',consumer_feed_style='chained')
            groups['transport']=[a for a in actions(link) if a['action_type']=='place_ghost']
            sinks=[{'kind':'belt','entity':'fast-transport-belt','position':{'x':x+consumers*3-.5,'y':y+6.5},'item':recipe}]
        elif transport=='bots':
            providers=[]
            for product,count,origin_y in [('copper-cable',producers,0),(recipe,consumers,18+spacing)]:
                group=[]
                spec=LINE_RECIPES[product]
                for i in range(count):
                    plan=generate_paired_mall_layout(product,'assembling-machine-2',spec['ingredients'],spec['amounts'],(i*12,origin_y),'left',
                         craft_time=spec['craft_time'],product_amount=spec['product_amount'],request_multiplier_override=request_multiplier,inserter_type='fast-inserter',include_substation=False)
                    for a in actions(plan):
                        a={**a,'action_type':'place_ghost'};a.pop('clear_logistic_groups',None);a.pop('inventory_limit',None)
                        group.append(a)
                    group.append({'action_type':'place_ghost','entity':'medium-electric-pole','position':{'x':i*12+3.5,'y':origin_y+3.5}})
                groups[product]=group
            sources=[{'kind':'provider','entity':'passive-provider-chest','position':{'x':-supply_distance-.5,'y':-10.5},'item':'copper-plate','rate':4.5*input_share},
                     {'kind':'provider','entity':'passive-provider-chest','position':{'x':-supply_distance+1.5,'y':-10.5},'item':'iron-plate','rate':3*input_share}]
            sinks=[{'kind':'provider','position':a['position'],'entity':a['entity'],'item':recipe} for a in groups[recipe] if a['entity']=='passive-provider-chest']
        else:raise ValueError('transport must be belt or bots')
    elif family=='plastic':
        recipe='plastic-bar';target=1.8
        refinery=generate_fluid_machine_row('basic-oil-processing',producers,0,0,terminal_collector=False)
        px=40+spacing
        plant=generate_fluid_machine_row(recipe,consumers,px,24,chained_items={'coal'},terminal_collector=False)
        groups['petroleum-gas'],_=ghosts(refinery);groups[recipe],_=ghosts(plant)
        crude=header_attachment('basic-oil-processing','crude-oil',producers,0,0)
        gas=header_attachment('basic-oil-processing','petroleum-gas',producers,0,0)
        plant_gas=header_attachment(recipe,'petroleum-gas',consumers,px,24)
        fluid_segments=fluid_network_segments('basic-oil-processing',producers,0,0)+fluid_network_segments(recipe,consumers,px,24)
        occupied=set().union(*(entity_footprint_tiles(a) for group in groups.values() for a in group))
        existing={tuple(t) for s in fluid_segments if s['fluid']=='petroleum-gas' for t in s['tiles']}
        link=generate_shortest_fluid_chain_link(gas['attach'],[plant_gas['attach']],'petroleum-gas',foreign=fluid_segments,
              hard_tiles=occupied-existing,existing_tiles=existing,mixing_margin=True)
        groups['transport']=list(actions(link))+[{'action_type':'place_ghost','entity':'pipe','position':{'x':a[0]+.5,'y':a[1]+.5}} for a in [gas['attach'],plant_gas['attach']]]
        sources=[{'kind':'fluid','entity':'infinity-pipe','position':{'x':crude['west'][0]+.5,'y':crude['west'][1]+1.5},'fluid':'crude-oil'},
                 {'kind':'belt','entity':'transport-belt','position':{'x':px-2.5,'y':24.5},'item':'coal','rate':.9*input_share}]
        sinks=[{'kind':'belt','entity':'transport-belt','position':{'x':px+consumers*3-.5,'y':30.5},'item':recipe}]
    else:raise ValueError('family must be circuit or plastic')
    # Link realizers include their header endpoints; identical existing tiles are not duplicate builds.
    seen={};clean={}
    for name,group in groups.items():
        clean[name]=[]
        for a in group:
            key=(a['entity'],a['position']['x'],a['position']['y'])
            if key in seen:
                if seen[key]!=a:raise ValueError('conflicting component endpoint')
                continue
            seen[key]=a;clean[name].append(a)
    plan={'phases':[{'name':n,'actions':a} for n,a in clean.items()]};validate_build_plan(plan)
    counts=dict(Counter(a['entity'] for a in actions(plan)));counts['medium-electric-pole']=counts.get('medium-electric-pole',0)+100
    fault_action=None;alternative=None
    if fault!='none':
        names={'belt':{'transport-belt','fast-transport-belt'},'machine':{'assembling-machine-2','chemical-plant'},'pole':{'medium-electric-pole','substation'}}[fault]
        # Select a production-path entity, never a fixture or a source/sink endpoint.
        endpoints={tuple(s['position'][k] for k in ['x','y']) for s in sources+sinks}
        choices=[a for a in actions(plan) if a['entity'] in names and tuple(a['position'][k] for k in ['x','y']) not in endpoints]
        if not choices:raise ValueError('fault has no production entity in this template')
        fault_action=deepcopy(choices[len(choices)//2]);counts[fault_action['entity']]+=1
        alternative=deepcopy(next(a for a in actions(plan) if a['entity'] in {'assembling-machine-2','chemical-plant'} and a.get('recipe')==recipe))
        alternative['position']={'x':96.5,'y':8.5}
        counts[alternative['entity']]+=1
    for f in sources:
        if f.get('rate') is not None:f['delay_ticks']=delay_ticks
    return {'family':family,'transport':transport,'producers':producers,'consumers':consumers,'input_share':input_share,
            'delay_ticks':delay_ticks,'request_multiplier':request_multiplier,'recipe':recipe,'target':target,'groups':clean,'sources':sources,'sinks':sinks,'budget':counts,
            'fluid_segments':fluid_segments,'fixture_funded':True,'supply_distance':supply_distance,'spacing':spacing,
            'logistic_robots':logistic_robots,'interruption_ticks':interruption_ticks,'interruption_source':interruption_source,
            'sink_pause_ticks':sink_pause_ticks,'fault':fault,'fault_action':fault_action,'recovery_alternative':alternative}


class ComponentWorld(SavedWorldAdapter):
    """Exact paused mechanics; fixture RPC owns only its paired training scope."""
    def __init__(self,root,spec):
        super().__init__(root);self.spec=spec
        for required in ['game.surfaces.nauvis','game.forces.player','surface="nauvis", force="player"','if solid then']:
            if required not in self.lua_source:raise RuntimeError('shared observation helper changed; component scope adaptation requires review')
        self.surface='training/mechanics-'+root.name;self.force='training-mechanics-'+root.name
        self.lab=Lab(root);self.lab.host.close();self.lab.host=self;self.lab.bridge=self.bridge;self.lab.surface=self.surface;self.lab.force=self.force
        self.lab.rpc=self.rpc
        self.rpc('init',surface=self.surface,force=self.force,spec=spec)
        self._base=self.lua_source.replace('game.surfaces.nauvis','game.surfaces[request.surface]').replace('game.forces.player','game.forces[request.force]')
        self._base=self._base.replace('surface="nauvis", force="player"','surface=request.surface, force=request.force')
        self._base=self._base.replace('"chemical-plant","oil-refinery","pumpjack"}', '"chemical-plant","oil-refinery","pumpjack","plastic-bar"}')
        self._base=self._base.replace('if solid then','if true then')
        self._base=self._base.replace('then return "collision" end','then return "collision:"..a.entity..":"..p.x..","..p.y end')
        placement='surface.can_place_entity{name=a.entity,position=p,direction=defines.direction[a.direction or "north"],force=force}'
        if placement not in self._base:raise RuntimeError('component placement validator changed')
        # Match layout_executor's real collision check; transient ground items are bot-clearable.
        self._base=self._base.replace(placement,placement[:-1]+',build_check_type=defines.build_check_type.manual}')
        schema=json.loads((ROOT/'schemas/laya_saved_world.schema.json').read_text());schema['properties']['surface']={'const':self.surface};schema['properties']['force']={'const':self.force}
        self.validator=Draft7Validator(schema)
    def rpc(self,op,**fields):
        verify_instance(self.root)
        name=f'production-fixture/{uuid.uuid4().hex}.json'
        self.bridge.command('/rl_production_fixture '+json.dumps({'op':op,'report':name,**fields},separators=(',',':')))
        r=load_json(self.bridge.script_output/name)
        if not r['ok']:raise RuntimeError(r['error'])
        return r['value']
    def query(self,candidates=None,inspect=None):
        verify_instance(self.root)
        name=f'production-fixture/{uuid.uuid4().hex}.json'
        req=json.dumps({'surface':self.surface,'force':self.force,'bounds':{'x_min':-64,'y_min':-64,'x_max_exclusive':128,'y_max_exclusive':96},'candidates':candidates,'inspect':inspect})
        source='local request=helpers.json_to_table([=['+req+']=]); '+self._base
        source=' '.join(l.split('--',1)[0] for l in source.splitlines())
        self.bridge.command(f'/sc local ok,v=pcall(function() {source} end);helpers.write_file("{name}",helpers.table_to_json({{ok=ok,value=ok and v or nil,error=not ok and tostring(v)}}),false)')
        report=load_json(self.bridge.script_output/name)
        if not report['ok']:raise RuntimeError(report['error'])
        value=report['value']
        if candidates is None and inspect is None:
            fixture=self.rpc('snapshot')
            value['production'].update(fixture['fluid_production']);value['consumption'].update(fixture['fluid_consumption'])
            for recipe,bonus in fixture['recipe_productivity'].items():
                if recipe in value['recipes']:value['recipes'][recipe]['productivity_bonus']=bonus
            for k in ['ports','poles','machines','resources']:
                if value.get(k)=={}:value[k]=[]
            self.validator.validate(value)
            value['fixture']=fixture
            value['layout_obstacles']=fixture['protected_entities']
        return value
    def advance(self,ticks):return self.lab.advance(ticks)
    def execute(self,selected,presented,measure_ticks=300):
        matches=[c for c in presented if c['option_id']==selected]
        if len(matches)!=1:raise ValueError('component choice must identify one offered action')
        candidate=matches[0]
        if candidate.get('family')!='clear-ground':return super().execute(selected,presented,measure_ticks)
        before=self.query()
        if not before['fixture'].get('perturbations'):raise ValueError('cleanup requires a declared damaged site')
        receipt={'candidate':candidate,'status':'pending','before':before}
        path=self.root/'evidence'/f'transaction-{uuid.uuid4().hex}.json'
        path.write_text(json.dumps(receipt)+'\n')
        receipt['cleanup']=self.rpc('clear-ground');self.advance(measure_ticks);after=self.query()
        receipt.update(status='observed',after=after,measurement=measured_outcome(before,after))
        path.write_text(json.dumps(receipt)+'\n');return receipt

ROOT=Path(__file__).resolve().parents[1]
