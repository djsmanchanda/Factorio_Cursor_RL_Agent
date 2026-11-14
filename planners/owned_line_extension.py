# Path: planners/owned_line_extension.py | Purpose: Extend paired mine collectors and stage owned production-line cutovers.
from __future__ import annotations

import json
from math import ceil

from planners.plan_validation import entity_footprint_tiles, validate_build_plan
from planners.resource_layouts import mine_substation_positions


def _ghost(entity, x, y, **extra):
    return {'action_type': 'place_ghost', 'entity': entity, 'position': {'x': x, 'y': y}, **extra}


def paired_collector_extension(drills, collector, additional_drills):
    """Grow away from the haul head, retaining its position and direction.

    This is an indivisible supply packet. The caller must validate every new
    drill's actual mining area, collision, land and coverage before admission.
    """
    if type(additional_drills) is not int or additional_drills < 1:
        raise ValueError('collector extension requires a positive drill count')
    ys = sorted({d['position']['y'] for d in drills})
    flows = {b.get('direction') for b in collector}
    if len(ys) != 2 or ys[1] - ys[0] != 4 or len(flows) != 1 or not flows <= {'east', 'west'}:
        raise ValueError('expected paired drills on one horizontal collector')
    flow = next(iter(flows))
    step = -3 if flow == 'east' else 3
    edge = (min if step < 0 else max)(d['position']['x'] for d in drills)
    columns = [edge + step * (i + 1) for i in range(ceil(additional_drills / 2))]
    belt_y = sum(ys) / 2
    actions = [_ghost('electric-mining-drill', x, y, direction=direction)
               for x in columns for y, direction in ((ys[0], 'south'), (ys[1], 'north'))][:additional_drills]
    first = min(b['position']['x'] for b in collector)
    last = max(b['position']['x'] for b in collector)
    if step < 0:
        xs = [min(columns) + i for i in range(round(first - min(columns)))]
    else:
        xs = [last + i + 1 for i in range(round(max(columns) - last))]
    actions += [_ghost('transport-belt', x, belt_y, direction=flow) for x in xs]
    actions += [_ghost('substation', x, y) for x, y in mine_substation_positions(columns, belt_y)]
    validate_build_plan({'phases': [{'name': 'collector_extension', 'actions': actions}]})
    return actions


def _signature(action):
    return json.dumps(action, sort_keys=True, separators=(',', ':'))


def split_owned_line_change(existing, desired):
    """Keep identical entities; build safe additions before replacing blockers.

    Only the supplied owned assembly may be removed. Mine/haul/shared power
    outside that assembly must never be passed as obsolete equipment.
    """
    old = {_signature(a): a for a in existing}
    new = {_signature(a): a for a in desired}
    removals = [a for key, a in old.items() if key not in new]
    additions = [a for key, a in new.items() if key not in old]
    occupied = {tile for a in removals for tile in entity_footprint_tiles(a)}
    prepare, finish = [], []
    for a in additions:
        (finish if occupied.intersection(entity_footprint_tiles(a)) else prepare).append(a)
    for group in (prepare, finish):
        if group:
            validate_build_plan({'phases': [{'name': 'owned_line_change', 'actions': group}]})
    return prepare, removals, finish


def requester_line_identity(candidate):
    """Recover old receipt metadata only by matching the complete shared plan."""
    from planners.smelter_block import generate_requester_smelting_line
    actual = [a for p in candidate['plan']['phases'] for a in p['actions']]
    keys = {_signature(a) for a in actual}
    requesters = [a for a in actual if a['entity'] == 'requester-chest']
    if len(requesters) != 1:
        return None
    chest = requesters[0]
    requests = chest.get('logistic_requests') or []
    if len(requests) != 1:
        return None
    count = requests[0]['count']
    for flow in ('south', 'west', 'north', 'east'):
        shape = generate_requester_smelting_line(candidate['product'], candidate['furnaces'],
                                                 request_count=count, flow=flow)
        template = shape['phases'][0]['actions']
        expected = next(a for a in template if a['entity'] == 'requester-chest')
        ox = chest['position']['x'] - expected['position']['x']
        oy = chest['position']['y'] - expected['position']['y']
        shifted = [{**a, 'position': {'x': a['position']['x'] + ox, 'y': a['position']['y'] + oy}}
                   for a in template]
        if all(_signature(a) in keys for a in shifted):
            return (ox, oy), flow, count
    return None
