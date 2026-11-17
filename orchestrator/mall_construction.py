# Path: orchestrator/mall_construction.py | Purpose: Observe and finish the recipe phase of an owned submitted mall cell.
"""Resume selected construction, never infer ownership from an empty assembler."""
from __future__ import annotations

import copy

from orchestrator import live_base, mall_control
from orchestrator.configuration_preconditions import supports_unconfigured_recipe_preconditions
from orchestrator.mall_builder import locate_mall_cell, _configure_built_mall_recipes
from planners.stock_gating import gate_matches


def _position(action):
    return tuple(action['position'][axis] for axis in ('x', 'y'))


def _acknowledged(entry):
    report = entry.get('report') or {}
    return (isinstance(report, dict) and report.get('ok') is True and type(report.get('tick')) is int
            and all(type(report.get(key)) is int and report[key] == 0
                    for key in ('failed_placements', 'recipe_failures')))


def selected_cells(invocations, reference_point):
    """Read mixed selected plans even when their enclosing operation returned a shortage.

    An acknowledged later recipe/loan configuration closes this construction
    phase permanently. A finite release cannot resurrect an old empty-cell claim.
    """
    entries = [e for e in invocations.returned_plans() if _acknowledged(e)]
    configured = {}
    for entry in entries:
        for phase in entry['plan']['phases']:
            for action in phase['actions']:
                if action.get('action_type') == 'configure_entity' and (
                        'recipe' in action or 'release_cell' in action):
                    pos = _position(action)
                    configured[pos] = max(configured.get(pos, -1), entry['report']['tick'])
    result = {}
    for entry in entries:
        for phase in entry['plan']['phases']:
            if not phase['name'].startswith('paired_mall_'):
                continue
            item = phase['name'][len('paired_mall_'):]
            machines = [a for a in phase['actions'] if a.get('entity') in live_base.ASSEMBLER_TIERS
                        and a.get('action_type') in {'place_ghost', 'place_entity'} and 'recipe' not in a]
            if len(machines) != 1:
                continue
            machine, = machines
            pos = _position(machine)
            located = locate_mall_cell(pos, reference_point)
            if located is None or configured.get(pos, -1) >= entry['report']['tick']:
                continue
            origin, side = located
            requester = (origin[0] + 4.5, origin[1] + 1.5)
            provider = (requester[0], requester[1] + (-1 if side == 'left' else 1))
            group = f'mall:{item}:{side}'
            if not any(a.get('entity') == 'requester-chest' and _position(a) == requester
                       and any(s.get('group') == group for s in a.get('logistic_sections', []))
                       for a in phase['actions']):
                continue
            if not any(a.get('entity') == 'passive-provider-chest' and _position(a) == provider
                       for a in phase['actions']):
                continue
            gate = machine.get('logistic_condition', False)
            if gate is not False and (not isinstance(gate, dict)
                                     or not gate_matches(gate, item, gate.get('constant'))):
                continue
            hardware = {'surface': 'nauvis', 'force': 'player', 'phases': [{'name': phase['name'], 'actions': [
                {'action_type': 'place_ghost', 'entity': a['entity'], 'position': copy.deepcopy(a['position'])}
                for a in phase['actions'] if 'entity' in a]}]}
            candidate = {'claim': f"{entry['operation_id']}:{entry['submission']}",
                         'item': item, 'position': list(pos), 'machine': machine['entity'],
                         'requester_position': list(requester), 'provider_position': list(provider),
                         'group': group, 'side': side, 'gate': gate, 'hardware': hardware,
                         'submitted_tick': entry['report']['tick']}
            if pos not in result or candidate['submitted_tick'] > result[pos]['submitted_tick']:
                result[pos] = candidate
    return list(result.values())


def _guard(cell):
    return {'unit_number': cell['unit_number'], 'recipe': cell['recipe'],
            'logistic_condition': cell.get('gate', False), 'circuit_enabled': cell['circuit_enabled'],
            'requester': {'entity': 'requester-chest',
                          'position': dict(zip(('x', 'y'), cell['requester_position'])),
                          'unit_number': cell['requester_unit_number'], 'groups': cell['groups']}}


def observe(client, invocations, reference_point, *, item=None):
    """Selected intent plus current ownership, construction and loan facts; unknown stays explicit."""
    selected = selected_cells(invocations, reference_point)
    if item is not None:
        selected = [c for c in selected if c['item'] == item]
    if not selected:
        return []
    from orchestrator.mall_bootstrap import active_bootstrap_loans
    try:
        cells = mall_control.observe_cells(client, invocations.directory.parent, 'nauvis', 'player',
                                           reference_point, (), intents={tuple(c['position']): c['item']
                                                                        for c in selected})
        loans = active_bootstrap_loans(client, 'nauvis', 'player')
    except Exception:
        cells, loans = None, None
    facts = []
    for intent in selected:
        fact = {k: copy.deepcopy(v) for k, v in intent.items() if k != 'hardware'}
        fact.update(reason='observation_unknown', construction=None, guard=None)
        cell = next((c for c in cells or [] if c['position'] == intent['position']), None)
        if cells is not None and loans is not None:
            if cell is None or cell['entity'] != intent['machine']:
                fact['reason'] = 'machine_missing_or_changed'
            elif cell['recipe']:
                continue
            elif (cell.get('owner') != f"paired_mall_{intent['item']}" or not cell.get('standing')
                  or intent['group'] not in cell['groups'] or cell.get('loaned')
                  or any(g.endswith(':' + intent['side']) and g != intent['group'] for g in cell['groups'])
                  or any(list(l.requester_position) == intent['requester_position'] for l in loans)):
                fact['reason'] = 'ownership_or_loan_changed'
            elif cell.get('circuit_enabled') is not False or cell.get('gate') != intent['gate']:
                fact['reason'] = 'configuration_changed'
            else:
                try:
                    survey = live_base.plan_construction_status(client, 'nauvis', 'player', intent['hardware'])
                    for key in ('required', 'installed', 'pending', 'unplaced'):
                        if not isinstance(survey[key], dict) or any(
                                type(n) is not int or n < 0 for n in survey[key].values()):
                            raise ValueError('invalid construction survey')
                    names = set().union(*(survey[k] for k in ('required', 'installed', 'pending', 'unplaced')))
                    if any(survey['required'].get(name, 0) != sum(survey[k].get(name, 0)
                            for k in ('installed', 'pending', 'unplaced')) for name in names):
                        raise ValueError('inconsistent construction survey')
                    required, installed, pending, missing = (sum(survey[k].values())
                        for k in ('required', 'installed', 'pending', 'unplaced'))
                    if not required or required != installed + pending + missing:
                        raise ValueError('incomplete construction survey')
                    fact.update(construction=survey, guard=_guard(cell))
                    fact['reason'] = ('support_missing' if missing else 'construction_pending' if pending else 'ready')
                except Exception:
                    fact['reason'] = 'construction_unknown'
        facts.append(fact)
    return facts


def options(economics):
    if (economics or {}).get('owned_mall_resume_guarded') is not True:
        return []
    return [{'option_id': 'op.resume_mall_cell.' + f['claim'], 'family': 'resume_mall_cell',
             'product': f['item'], 'need_id': 'stock:' + f['item'],
             'summary': 'finish owned ' + f['item'] + ' cell configuration',
             'params': {'item': f['item'], 'claim': f['claim'], 'expected': f['guard']},
             'features': {'owned_configuration_pending': True}, 'bill': {}}
            for f in (economics or {}).get('pending_mall_cells') or [] if f['reason'] == 'ready']


def resume(client, bridge, invocations, reference_point, emit, *, item, claim, expected):
    """Reobserve the selected claim, then let the executor guard exact identities atomically."""
    evidence = {'configured': False, 'claim': claim}
    fact = next((f for f in observe(client, invocations, reference_point, item=item)
                 if f['item'] == item and f['claim'] == claim), None)
    if fact is None or fact['reason'] != 'ready' or fact['guard'] != expected:
        return {'ok': False, 'code': 'owned_mall_resume_changed', 'state': 'planned', **evidence}
    if not supports_unconfigured_recipe_preconditions(client):
        return {'ok': False, 'code': 'owned_mall_resume_guard_unavailable', 'state': 'planned', **evidence}
    from orchestrator.construction_coverage import StuckError
    try:
        _configure_built_mall_recipes(client, bridge, 'nauvis', 'player', item, fact['machine'],
                                    [tuple(fact['position'])],
                                    fact['gate']['constant'] if fact['gate'] else None, emit,
                                    preconditions={tuple(fact['position']): expected})
    except StuckError as error:
        blockers = (error.details or {}).get('blockers', [])
        if not blockers or not all(str(b.get('reason', '')).startswith(
                'configuration_precondition_failed:') for b in blockers):
            raise
        return {'ok': False, 'code': 'owned_mall_resume_changed', 'state': 'planned', **evidence}
    evidence['configured'] = True
    try:
        cells = mall_control.observe_cells(client, invocations.directory.parent, 'nauvis', 'player',
                                           reference_point, ())
        after = next((c for c in cells if c['unit_number'] == expected['unit_number']), None)
        verified = (after is not None and after['recipe'] == item
                    and after['owner'] == f'paired_mall_{item}'
                    and after['requester_unit_number'] == expected['requester']['unit_number']
                    and after['groups'] == expected['requester']['groups']
                    and after.get('gate') == (fact['gate'] or expected['logistic_condition']))
    except Exception:
        verified = False
    return {'ok': verified, 'code': 'owned_mall_resumed' if verified else 'owned_mall_resume_unverified',
            'state': 'producing' if verified else 'planned', **evidence}


def resume_for_item(client, bridge, surface, force, item, reference_point, emit):
    """A repeated ensure finishes its own pending phase before requesting another slot."""
    invocations = getattr(bridge, 'operation_invocations', None)
    if invocations is None or (surface, force) != ('nauvis', 'player'):
        return
    from orchestrator.autonomous_builder import ProductionPrerequisiteDeferred
    facts = observe(client, invocations, reference_point, item=item)
    if not facts:
        return
    fact = facts[0]
    if fact['reason'] != 'ready':
        raise ProductionPrerequisiteDeferred('owned mall construction requires reconciliation',
            code='owned_mall_resume_wait', state='constructing', details=fact)
    result = resume(client, bridge, invocations, reference_point, emit,
                    item=item, claim=fact['claim'], expected=fact['guard'])
    if not result['ok']:
        raise ProductionPrerequisiteDeferred('owned mall recipe configuration is not verified',
            code=result['code'], state='constructing', details=result)
