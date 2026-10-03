# Path: training/laya_smelting_array.py | Purpose: Belt-only parallel arrays composed from Laya's approved serial smelting lines.
from __future__ import annotations

from collections import Counter
from planners.plan_validation import entity_footprint_tiles, validate_build_plan
from training.laya_lines import LINE_PITCH, line_units, line_output
from training.laya_saved_world import ghost

PALETTE = {'transport-belt', 'express-transport-belt', 'splitter', 'express-splitter',
           'electric-furnace', 'inserter', 'fast-inserter', 'medium-electric-pole'}


def route(points, tier='express-transport-belt', terminal='south'):
    """Manhattan belt route; the tile at a corner faces the next segment."""
    tiles = []
    for a, b in zip(points, points[1:]):
        if a[0] != b[0] and a[1] != b[1]:
            raise ValueError('belt segments must be cardinal')
        dx, dy = (b[0] > a[0]) - (b[0] < a[0]), (b[1] > a[1]) - (b[1] < a[1])
        n = int(abs(b[0]-a[0]) + abs(b[1]-a[1]))
        tiles += [(a[0]+dx*i, a[1]+dy*i) for i in range(n)]
    tiles.append(points[-1])
    result = []
    for i, (x, y) in enumerate(tiles):
        if i == len(tiles)-1:
            direction = terminal
        else:
            nx, ny = tiles[i+1]
            direction = 'east' if nx>x else 'west' if nx<x else 'south' if ny>y else 'north'
        result.append(ghost(tier, x, y, direction))
    return result


def headers(group):
    """A two-stage 1:4 splitter fanout and reverse 4:1 merge, all express."""
    x = group * 64
    a = route([(x+21.5,-25.5),(x+21.5,-21.5)])
    a += [ghost('express-splitter',x+22,-20.5,'south')]
    for root, child in [(21.5,5.5),(22.5,37.5)]:
        a += route([(x+root,-19.5),(x+root,-16.5),(x+child,-16.5),(x+child,-11.5)])
        a += [ghost('express-splitter',x+child+.5,-10.5,'south')]
        for lane, leaf in [(child,child-8),(child+1,child+8)]:
            a += route([(x+lane,-9.5),(x+lane,-6.5),(x+leaf,-6.5),(x+leaf,1.5),
                        (x+leaf+1,1.5)],terminal='east')
    for i in range(4):
        end = line_output(3,x+i*LINE_PITCH,0)
        lane = x+15.5+i*LINE_PITCH
        merge_lane = x+(22.5 if i==0 else 23.5 if i==1 else 54.5 if i==2 else 55.5)
        a += route([end,(lane,end[1]),(lane,38.5),(merge_lane,38.5),(merge_lane,43.5)])
    for merge in (23,55):
        a += [ghost('express-splitter',x+merge,44.5,'south')]
    for src, dst in [(22.5,38.5),(54.5,39.5)]:
        a += route([(x+src,45.5),(x+src,48.5),(x+dst,48.5),(x+dst,51.5)])
    a += [ghost('express-splitter',x+39,52.5,'south')]
    a += route([(x+38.5,53.5),(x+38.5,58.5)])
    return a


def serial_line(recipe, index, *, belt_tier='basic', fast_arms=False):
    """Keep approved geometry/priorities; optional explicit throughput tier swap."""
    if belt_tier not in {'basic','express'}:
        raise ValueError('unsupported line belt tier')
    a = line_units(recipe,3,index*LINE_PITCH,0)
    if belt_tier == 'express':
        a = [{**v,'entity':{'transport-belt':'express-transport-belt','splitter':'express-splitter'}.get(v['entity'],v['entity'])} for v in a]
    if fast_arms:
        a = [{**v,'entity':'fast-inserter' if v['entity']=='inserter' else v['entity']} for v in a]
    return a


def checked_plan(surface, force, label, placements):
    if not surface.startswith('training/smelting-') or not force.startswith('training-smelting-'):
        raise ValueError('smelting mechanics require dedicated lab identity')
    if any(a['entity'] not in PALETTE or a['action_type'] != 'place_ghost' for a in placements):
        raise ValueError('production path permits only funded belt/furnace/inserter/pole ghosts')
    plan = {'surface':surface,'force':force,'atomic':True,'phases':[{'name':label,'actions':placements}]}
    validate_build_plan(plan)
    return plan


def validate_union(plans):
    """Shared footprint model, indexed for large arrays rather than quadratic pairs."""
    occupied = {}
    for plan in plans:
        for phase in plan['phases']:
            for a in phase['actions']:
                for tile in entity_footprint_tiles(a):
                    if tile in occupied:
                        raise ValueError(f'array collision at {tile}: {occupied[tile]} versus {a}')
                    occupied[tile] = a


def bill(plans):
    return dict(Counter(a['entity'] for p in plans for ph in p['phases'] for a in ph['actions']))


def measurement(before, after, lines):
    ticks = after['tick']-before['tick']
    if ticks < 7200:
        raise ValueError('acceptance requires at least two game minutes')
    def delta(key):
        values = [b-a for a,b in zip(before[key],after[key])]
        if any(v<0 for v in values): raise ValueError('measurement counters reset')
        return [v*60/ticks for v in values]
    produced = (after['produced']-before['produced'])*60/ticks
    consumed = (after['consumed']-before['consumed'])*60/ticks
    outputs = delta('delivered'); inputs = delta('supplied'); per_line = delta('line_finished')[:lines]
    target = lines*18*.625
    group_targets = [min(4,max(0,lines-g*4))*11.25 for g in range(4)]
    return {'window_ticks':ticks,'ore_consumed_per_s':consumed,'plates_produced_per_s':produced,
            'input_belts_per_s':inputs,'output_belts_per_s':outputs,'lines_per_s':per_line,
            'fixture_denied':sum(after['denied'])-sum(before['denied']),
            'ghosts':after['ghosts'],'path_chests':after['path_chests'],
            'target_per_s':target,
            'belt_ore_change':after.get('belt_ore',0)-before.get('belt_ore',0),
            'accepted':abs(produced-target)<.02 and abs(consumed-target)<.02
                       and abs(sum(outputs)-target)<.02 and all(abs(v-11.25)<.02 for v in per_line)
                       and all(abs(v-t)<.02 for v,t in zip(inputs,group_targets))
                       and all(abs(v-t)<.02 for v,t in zip(outputs,group_targets))
                       and abs(after.get('belt_ore',0)-before.get('belt_ore',0))<=4
                       and after['ghosts']==0 and after['path_chests']==0
                       and sum(after['denied'])==sum(before['denied'])}
