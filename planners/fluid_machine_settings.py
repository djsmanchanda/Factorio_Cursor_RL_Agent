# Path: planners/fluid_machine_settings.py | Purpose: Restore physical mirroring settings implied by owned fluid geometry.
from __future__ import annotations

import copy

from core.fluid_systems import rotate_offset
from planners.fluid_layouts import VERIFIED_PIPE_TILES
from planners.plan_validation import actions


def complete_fluid_mirroring_settings(source: dict) -> dict:
    """Complete older plans without moving their reserved entities or pipes.

    A reflection is an entity setting, not just a reflection of pipe tiles.
    Infer it only when every recipe port matches one unambiguous orientation
    in the owned plan. Explicit settings and ambiguous geometry stay intact.
    """
    plan = copy.deepcopy(source)
    pipes = {(a['position']['x'], a['position']['y']) for a in actions(plan)
             if a.get('entity') in {'pipe', 'pipe-to-ground'}
             and a.get('action_type') in {'place_ghost', 'place_entity'}}
    for action in actions(plan):
        if 'mirroring' in action or action.get('action_type') not in {'place_ghost', 'place_entity'}:
            continue
        entity = action.get('entity')
        if entity == 'pumpjack':
            # Native north-facing unmirrored output, verified on Factorio2.1.
            offsets = [(1, -2)]
        elif entity in {'oil-refinery', 'chemical-plant', 'assembling-machine-2', 'assembling-machine-3'}:
            ports = VERIFIED_PIPE_TILES.get(action.get('recipe'))
            if not ports:
                continue
            offsets = list(ports['inputs'].values()) + list(ports['outputs'].values())
        else:
            continue
        if not offsets:
            continue
        x, y = action['position']['x'], action['position']['y']
        matches = []
        for mirrored in (False, True):
            targets = [rotate_offset((-dx if mirrored else dx, dy), action.get('direction', 'north'))
                       for dx, dy in offsets]
            if all((x+dx, y+dy) in pipes for dx, dy in targets):
                matches.append(mirrored)
        if len(matches) == 1:
            action['mirroring'] = matches[0]
    return plan
