# Path: orchestrator/stage_research.py | Purpose: Build real bot-funded labs and deliver the live research's science types.
from __future__ import annotations

import copy
import math

from orchestrator import live_base
from orchestrator.execution_slice import checkpoint, persist_checkpoint
from orchestrator.extraction_transport import planned_footprint_tiles
from orchestrator.stage_services import StuckError, _submit, extend_power, extend_roboport_coverage
from planners.infrastructure import strip_local_power
from planners.local_layout_planner import LocalLayoutPlanner


def build_research_labs(client, bridge, surface, force, reference_point, science_packs, emit, *, minimum_labs=4):
    """Persist one legal lab row; configuration waits for normal bot construction."""
    packs = tuple(sorted(set(science_packs)))
    if not packs or minimum_labs <= 0:
        raise ValueError('Research labs need science types and a positive count')
    state = checkpoint()
    saved = state.get('research_lab_plan') if state is not None else None
    planner = LocalLayoutPlanner()
    if saved is None:
        probe = strip_local_power(planner.generate_lab_row(minimum_labs, science_packs=packs))
        points = [a['position'] for p in probe['phases'] for a in p['actions']]
        left = math.floor(min(p['x'] for p in points))-2
        top = math.floor(min(p['y'] for p in points))-2
        right = math.ceil(max(p['x'] for p in points))+2
        bottom = math.ceil(max(p['y'] for p in points))+2
        area = live_base.find_clear_area(client, surface, reference_point, right-left, bottom-top,
                                         avoid_resources=True, resource_clearance=5)
        if area is None:
            raise StuckError('No clear research lab site', code='research_labs_unbuildable', state='failed')
        origin = (round(area[0])-left, round(area[1])-top)
        plan = strip_local_power(planner.generate_lab_row(minimum_labs, *origin, science_packs=packs))
        plan['surface'], plan['force'] = surface, force
        saved = {'origin':list(origin), 'plan':plan}
        if state is not None:
            state['research_lab_plan'] = saved
            persist_checkpoint()
    plan = copy.deepcopy(saved['plan'])
    actions = [a for p in plan['phases'] for a in p['actions']]
    requests = []
    for action in actions:
        if action.get('entity') == 'requester-chest':
            requests.append({'action_type':'configure_entity','entity':'requester-chest',
                             'position':action['position'], 'logistic_sections':action.pop('logistic_sections')})
    positions = [(a['position']['x'],a['position']['y']) for a in actions]
    names = live_base.entity_names_at(client, surface, positions)
    for action, point in zip(actions, positions, strict=True):
        if names.get(point) == action['entity']:
            action['action_type'] = 'configure_entity'
    reserved = planned_footprint_tiles(plan)
    origin = tuple(saved['origin'])
    extend_roboport_coverage(client,bridge,surface,force,origin,emit,reserved_tiles=reserved)
    _submit(client,bridge,surface,plan,'research_labs',emit,require_funded=True)
    labs = [(a['position']['x'],a['position']['y']) for a in actions if a['entity']=='lab']
    for point in labs:
        if not extend_power(client,bridge,surface,force,point,emit,reserved_tiles=reserved):
            raise StuckError('Research labs need connected power',code='research_labs_power_wait',
                             classification='intended_difficulty',state='power_wait')
    for request in requests:
        point = (request['position']['x'],request['position']['y'])
        extend_roboport_coverage(client,bridge,surface,force,point,emit,purpose='logistic',reserved_tiles=reserved)
    names = live_base.entity_names_at(client,surface,positions)
    pending = [{'entity':a['entity'],'position':list(p)} for a,p in zip(actions,positions,strict=True)
               if names.get(p)!=a['entity']]
    if pending:
        raise StuckError('Research labs wait for bot construction',code='infrastructure_construction_wait',
                         classification='intended_difficulty',state='constructing',
                         details={'owner':'research_labs','pending':pending})
    chest_positions = [(r['position']['x'],r['position']['y']) for r in requests]
    networks = live_base.logistic_network_ids(client,surface,chest_positions)
    if any(networks.get(point) is None for point in chest_positions):
        raise StuckError('Research lab science requesters need a logistic network',
                         code='research_labs_logistic_wait',classification='intended_difficulty',state='coverage_wait')
    _submit(client,bridge,surface,{'surface':surface,'force':force,
            'phases':[{'name':'research_science_requests','actions':requests}]},'research_science_requests',emit)
    emit(f'RESEARCH LABS: {len(labs)} built; requesting '+', '.join(packs))
    return {'labs':len(labs),'science_packs':list(packs),'origin':list(origin)}
