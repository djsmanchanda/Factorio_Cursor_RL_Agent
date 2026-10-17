# Path: orchestrator/mall_control.py | Purpose: Preview and verify bill-backed configuration of owned standing mall gates.
from __future__ import annotations

import json

from orchestrator import live_base
from orchestrator.mall_builder import locate_mall_cell
from orchestrator.power_district import plan_footprint_tiles, reservation_log_path, reservations_path
from planners.mall_layout import generate_mall_stock_gate_update, recipe_group_name
from planners.stock_gating import gate_matches


def observe_cells(client, script_output, surface, force, reference_point, anchors):
    """Require recorded placement ownership, declared topology and live requester tags.

    Reservation parsing is deliberately strict: a damaged ownership record makes
    this component unknown, rather than granting authority from geometry alone.
    """
    entries = []
    path = reservation_log_path(script_output)
    if path.exists():
        entries.extend(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    path = reservations_path(script_output)
    if path.exists():
        entries.extend(json.loads(path.read_text()))
    cells = []
    for machine in live_base.mall_gate_survey(client, surface, force):
        item = machine['recipe']
        located = locate_mall_cell(tuple(machine['position']), reference_point)
        if located is None:
            continue
        origin, side = located
        requester = (origin[0] + 4.5, origin[1] + 1.5)
        machine = dict(machine)
        requesters = machine.pop('requesters')
        chest = next((c for c in requesters if tuple(c['position']) == requester
                      and c['force'] == force), None)
        if chest is None:
            continue
        groups = chest['groups']
        footprint = plan_footprint_tiles({'phases': [{'actions': [{
            'entity': machine['entity'], 'position': dict(zip(('x', 'y'), machine['position']))}]}]})
        provider = (requester[0], requester[1] + (-1 if side == 'left' else 1))
        cell_footprint = footprint | plan_footprint_tiles({'phases': [{'actions': [
            {'entity': 'requester-chest', 'position': dict(zip(('x', 'y'), requester))},
            {'entity': 'passive-provider-chest', 'position': dict(zip(('x', 'y'), provider))},
        ]}]})
        owner = f'paired_mall_{item}'
        owned_tiles = {tuple(tile) for entry in entries if entry['name'] == owner
                       and entry.get('configuration_only') is not True for tile in entry['tiles']}
        configuration_scope = owned_tiles | cell_footprint

        def configuration_receipt(entry):
            if 'configuration_only' in entry:
                return entry['configuration_only'] is True
            # Legacy logs lack the fact above. Recognize the old configuration
            # operations within this cell or its owner's placement footprint.
            # Refreshes can batch several already-owned machines in one receipt.
            name = entry['name']
            known = name in {f'mall_recipe_{item}', f'mall_stock_gate_{item}',
                             f'mall_provider_limit_{item}', f'compact_mall_requests_{item}',
                             f'paired_mall_requests_{item}'} or any(
                name.startswith(prefix) and len(name) > len(prefix)
                for prefix in ('bootstrap_loan_', 'restore_bootstrap_loan_', 'promote_bootstrap_loan_'))
            return known and {tuple(tile) for tile in entry['tiles']} <= configuration_scope

        claims = [entry for entry in entries if not configuration_receipt(entry)]
        owners = {entry['name'] for entry in claims
                  if footprint.intersection(tuple(tile) for tile in entry['tiles'])}
        cells.append({**machine, 'owner': owner if footprint <= owned_tiles and owners <= {owner} else None,
                      'reservation_owners': sorted(owners), 'requester_position': list(requester),
                      'groups': list(groups), 'requester_unit_number': chest['unit_number'],
                      'side': side, 'anchor': item in anchors,
                      'loaned': any(g.startswith('mall-bootstrap:') for g in groups),
                      'standing': recipe_group_name(item) in groups or
                                  recipe_group_name(item, side) in groups})
    return cells


def raise_options(economics):
    """No inferred bill, stock, ownership or condition can authorize a raise."""
    economics = economics or {}
    projects, cells = economics.get('projects'), economics.get('cells')
    stock = (economics.get('stock') or {}).get('network')
    if (projects is None or cells is None or stock is None or economics.get('loans') is None
            or economics.get('guarded_configuration') is not True):
        return []
    projects = {owner: p for owner, p in projects.items()
                if p.get('active') is True}
    options = []
    for cell in cells:
        item, gate = cell['recipe'], cell.get('gate')
        if (not cell.get('owner') or not cell.get('standing') or cell.get('anchor')
                or cell.get('loaned') or cell.get('circuit_enabled') is not False
                or cell.get('entity') not in live_base.ASSEMBLER_TIERS
                or not cell.get('unit_number') or not gate):
            continue
        current = gate.get('constant')
        if (set(gate) != {'signal', 'comparator', 'constant'} or not isinstance(current, int)
                or current < 1 or not gate_matches(gate, item, current)):
            continue
        if any(loan['requester_position'] == cell['requester_position'] for loan in economics['loans']):
            continue
        target = sum(p['required'].get(item, 0) for p in projects.values())
        if target <= current or stock.get(item, 0) >= target:
            continue
        for owner, project in projects.items():
            if project['required'].get(item, 0) <= project['reserved'].get(item, 0):
                continue
            preview = {'cells': [cell], 'current_gate': gate,
                       'target_gate': {'signal': item, 'comparator': '<', 'constant': target},
                       'bill_owner': owner, 'bill': project,
                       'scope': {'surface': cell['surface'], 'force': cell['force'],
                                 'stock': 'absolute_network_target'},
                       'cost': {}, 'side_effects': ['raise production stop threshold; inputs may be consumed',
                                                  'retain per-run item floor for future same-recipe maintain/ensure/loan restore']}
            options.append({'option_id': f"op.raise_stock_gate.{cell['unit_number']}.{owner}",
                            'family': 'raise_stock_gate', 'product': item, 'need_id': f'stock:{item}',
                            'summary': f'raise {item} gate {current} to {target} for {owner}',
                            'params': {'item': item, 'preview': preview}, 'preview': preview,
                            'features': {}})
    return options


def execute_raise(item, preview, *, observe, submit, floors):
    """Revalidate the selected snapshot, submit once, and separately prove effect."""
    evidence = {'submitted': False, 'verified': False, 'preview': preview}
    try:
        candidate = next((o for o in raise_options(observe())
                          if o['product'] == item and o['preview'] == preview), None)
    except Exception as error:
        return {'ok': False, 'code': 'stock_gate_observation_unknown', 'state': 'blocked',
                **evidence, 'error': str(error)[:200]}
    if candidate is None:
        return {'ok': False, 'code': 'stock_gate_snapshot_changed', 'state': 'blocked', **evidence}
    cell = preview['cells'][0]
    scope = preview['scope']
    target = preview['target_gate']['constant']
    plan = generate_mall_stock_gate_update(item, cell['entity'], [tuple(cell['position'])], target)
    # Recipe is a precondition, never a configuration side effect of this operation.
    for action in plan['phases'][0]['actions']:
        action.pop('recipe')
        action['configuration_preconditions'] = {
            'unit_number': cell['unit_number'], 'recipe': cell['recipe'],
            'logistic_condition': cell['gate'], 'circuit_enabled': False,
            'requester': {'entity': 'requester-chest',
                          'position': dict(zip(('x', 'y'), cell['requester_position'])),
                          'unit_number': cell['requester_unit_number'], 'groups': cell['groups']},
        }
    plan.update(surface=scope['surface'], force=scope['force'])
    try:
        submit(plan, f'mall_stock_gate_{item}')
    except Exception as error:
        return {'ok': False, 'code': 'stock_gate_submission_failed', 'state': 'failed',
                **evidence, 'submission_attempted': True, 'error': str(error)[:200]}
    evidence['submitted'] = True
    # Preserve a possibly applied target even if readback becomes unavailable.
    key = (scope['surface'], scope['force'], item)
    floors[key] = max(floors.get(key, 0), target)
    try:
        after = (observe() or {}).get('cells') or []
        actual = next((c for c in after if c.get('unit_number') == cell['unit_number']), None)
        evidence['observed'] = actual
        expected = dict(cell, gate=preview['target_gate'])
        verified = actual == expected and gate_matches(actual.get('gate'), item, target)
    except Exception as error:
        verified = False
        evidence['error'] = str(error)[:200]
    return {'ok': verified, 'code': 'stock_gate_verified' if verified else 'stock_gate_unverified',
            'state': 'verified' if verified else 'unverified', **evidence, 'verified': verified}
