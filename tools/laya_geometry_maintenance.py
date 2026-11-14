# Path: tools/laya_geometry_maintenance.py | Purpose: Bridge receipt-owned geometry to shared bot recovery and additive refinery growth.
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

from orchestrator.recoverable_retirement import retire_entities_via_bots
from orchestrator.laya_operations import sha256_hash
from planners.smelter_block import generate_managed_refinery_plan, generate_requester_smelting_line
from planners.owned_line_extension import paired_collector_extension, split_owned_line_change, requester_line_identity
from training.laya_layout_stream import atomic_json
from training.laya_lines import clear_of, output_taps
from training.laya_power import POLES, world_obstacles
from training.laya_receipts import receipts
from training.laya_saved_world import target_key


def owned_targets(receipt):
    """Only completed, wholly new submissions establish recovery ownership."""
    submission = receipt.get('submission') or {}
    if receipt.get('status') != 'observed' or not submission.get('ok') \
            or submission.get('already_present_placements') != 0:
        return {}
    return {target_key(t): t['unit_number'] for t in (receipt.get('construction') or {}).get('targets', ())
            if t.get('state') == 'built' and type(t.get('unit_number')) is int}


def actions(candidate):
    return [a for phase in candidate['plan']['phases'] for a in phase['actions']]


class GeometryMaintenance:
    def __init__(self):
        self._receipts = {}

    def __call__(self, adapter, facts, emit=lambda _: None):
        path = Path(adapter.root, 'evidence', 'geometry-maintenance.json')
        ledger = json.loads(path.read_text()) if path.exists() else {'retired': [], 'delivered': {}}
        rows = [(p, r) for p, r in receipts(adapter.root)
                if (r['candidate'].get('family') in ('starter', 'line') or r['candidate'].get('shared_feed'))
                and r['submission'].get('ok')]
        retired = {tuple(r['key']) for r in ledger['retired']}
        raw = {}
        for p, slim in rows:
            if slim['status'] == 'observed':
                stamp = (p.stat().st_mtime_ns, p.stat().st_size)
                if p not in self._receipts or self._receipts[p][0] != stamp:
                    full = json.loads(p.read_text())
                    self._receipts[p] = (stamp, {key: full[key] for key in ('candidate', 'status', 'submission', 'construction')})
                raw[p] = self._receipts[p][1]
        # Delivery is proved at the exact replacement provider, plus real furnace crafts.
        for p, r in raw.items():
            c = r['candidate']
            if c['family'] != 'line' or c.get('repair_of'):
                continue
            owned = owned_targets(r)
            if not owned or p.name in ledger['delivered']:
                continue
            observed = adapter.query(inspect=c['plan'])['targets']
            exact = [t for t in observed if owned.get(target_key(t)) == t.get('unit_number')]
            crafts = any(t.get('products_finished', 0) > 0 for t in exact)
            providers = [t for t in exact if (t.get('inventory') or {}).get(c['product'], 0) > 0]
            if crafts and providers:
                ledger['delivered'][p.name] = {'product': c['product'], 'tick': facts['tick'],
                                               'provider_units': [t['unit_number'] for t in providers]}
        recovered = []
        # Pause only across identity recheck and ordering; bots work after resuming.
        for p, r in raw.items():
            c = r['candidate']
            owned = owned_targets(r)
            if not owned:
                continue
            if c['family'] == 'starter':
                if not any(proof['product'] == c['product'] for proof in ledger['delivered'].values()):
                    continue
                remove = [a for phase in c['plan']['phases'] for a in phase['actions'] if a['entity'] not in POLES]
            else:
                depleted = {('electric-mining-drill', m['position']['x'], m['position']['y'])
                            for m in facts.get('machines', ()) if m.get('resource_remaining') == 0
                            and m.get('unit_number') in owned.values()}
                remove = [a for phase in c['plan']['phases'] for a in phase['actions'] if target_key(a) in depleted]
            remove = [a for a in remove if target_key(a) not in retired and target_key(a) in owned]
            if not remove:
                continue
            with adapter.clock_paused() if hasattr(adapter, 'clock_paused') else nullcontext():
                adapter._check_instance()
                targets = adapter.query(inspect={'phases': [{'actions': remove}]})['targets']
                # A rebuilt same-name occupant is never the entity this receipt owns.
                if any(t.get('state') != 'built' or owned.get(target_key(t)) != t.get('unit_number') for t in targets):
                    continue
                # Keep bulk output available to logistics instead of dumping it into storage.
                # The receipt remains eligible on later passes, including after a restart.
                by_key = {target_key(t): t for t in targets}
                remove = [a for a in remove if a['entity'] != 'passive-provider-chest'
                          or (type(by_key[target_key(a)].get('inventory_slots_used')) is int
                              and 0 <= by_key[target_key(a)]['inventory_slots_used'] < 5)]
                if not remove:
                    continue
                plan = {'phases': [{'actions': [{**a, 'action_type': 'remove_entity'} for a in remove]}]}
                for phase in plan['phases']:
                    phase['actions'] = [{k: a[k] for k in ('action_type', 'entity', 'position')} for a in phase['actions']]
                retire_entities_via_bots(adapter.bridge, adapter.bridge, adapter.surface, adapter.force,
                                        plan, p.stem, emit, wait=False)
                for a in remove:
                    ledger['retired'].append({'owner': p.name, 'key': list(target_key(a)),
                                              'unit_number': owned[target_key(a)]})
                    retired.add(target_key(a))
                    recovered.append(target_key(a))
                atomic_json(path, ledger)
        finishes, pending = self._transitions(adapter, rows, raw, ledger, retired, emit)
        atomic_json(path, ledger)
        proposals = finishes + self._growth(rows, facts, retired, {p.name for p, r in raw.items() if owned_targets(r)},
                                            {r['candidate'].get('line_owner') for p, r in rows if p.name in pending})
        return {'candidates': proposals, 'retired': recovered,
                'managed_products': sorted({r['candidate']['product'] for _, r in rows
                                            if r['candidate'].get('shared_feed')}),
                'constructing_products': sorted({r['candidate']['product'] for p, r in rows
                                                if (r['candidate']['family'] == 'line' or r['candidate'].get('shared_feed'))
                                                and (r['status'] != 'observed' or p.name in pending)})}

    def _transitions(self, adapter, rows, raw, ledger, retired, emit):
        """Resume exact owned cutovers from receipts; removals never enter execute()."""
        proposals, pending = [], set()
        done = ledger.setdefault('transitions_done', [])
        by_name = {p.name: r for p, r in raw.items()}
        for p, r in rows:
            c = r['candidate']
            transition = c.get('line_transition')
            if not transition or p.name in done:
                continue
            pending.add(p.name)
            full = by_name.get(p.name)
            if not full or not owned_targets(full):
                continue
            refs = transition['retire']
            expected = [(ref, owned_targets(by_name.get(ref['owner'], {})).get(target_key(ref['action'])))
                        for ref in refs]
            if any(unit is None for _, unit in expected):
                continue
            # Recheck prepared construction and old unit identities in the same paused interval.
            with adapter.clock_paused() if hasattr(adapter, 'clock_paused') else nullcontext():
                adapter._check_instance()
                ready = adapter.query(inspect=c['plan'])['targets']
                owned = owned_targets(full)
                if not ready or any(t.get('state') != 'built' or owned.get(target_key(t)) != t.get('unit_number')
                                    for t in ready):
                    continue
                targets = adapter.query(inspect={'phases': [{'actions': [ref['action'] for ref in refs]}]})['targets'] if refs else []
                observed = {target_key(t): t for t in targets}
                if any(target_key(ref['action']) not in observed or
                       (observed[target_key(ref['action'])].get('state') == 'built' and
                        observed[target_key(ref['action'])].get('unit_number') != unit) or
                       (observed[target_key(ref['action'])].get('state') != 'built' and
                        target_key(ref['action']) not in retired) for ref, unit in expected):
                    continue
                remove = []
                for ref, unit in expected:
                    a = ref['action']
                    key = target_key(a)
                    if key in retired:
                        continue
                    slots = observed[key].get('inventory_slots_used')
                    if a['entity'] == 'passive-provider-chest' and not (type(slots) is int and 0 <= slots < 5):
                        continue
                    remove.append((ref, unit))
                if remove:
                    plan = {'phases': [{'actions': [{'action_type': 'remove_entity', 'entity': ref['action']['entity'],
                                                    'position': ref['action']['position']} for ref, _unit in remove]}]}
                    retire_entities_via_bots(adapter.bridge, adapter.bridge, adapter.surface, adapter.force,
                                            plan, p.stem, emit, wait=False)
                    for ref, unit in remove:
                        key = target_key(ref['action'])
                        ledger['retired'].append({'owner': ref['owner'], 'key': list(key), 'unit_number': unit})
                        retired.add(key)
                    atomic_json(Path(adapter.root, 'evidence', 'geometry-maintenance.json'), ledger)
                    continue  # Observe actual bot removal on a later pass.
                if any(t.get('state') != 'missing' for t in targets):
                    continue
            finish = transition['finish']
            completions = [other for _, other in rows if other['candidate'].get('cutover_of') == p.name]
            if not finish or any(other['status'] == 'observed' and owned_targets(by_name.get(op.name, {}))
                                 for op, other in rows if other['candidate'].get('cutover_of') == p.name):
                done.append(p.name)
                pending.remove(p.name)
            elif not completions:
                candidate = self._candidate(c, finish, c['line_owner'], repair=True)
                candidate.pop('repair_of')
                candidate.update(cutover_of=p.name, target_furnaces=c['target_furnaces'],
                                 summary='Finish owned line after observed bot cutover')
                proposals.append(candidate)
        return proposals, pending

    def _growth(self, rows, facts, retired, owners, pending=()):
        proposals = []
        obstacles = world_obstacles(facts)
        submitted = [a for _, r in rows for phase in r['candidate']['plan']['phases'] for a in phase['actions']
                     if target_key(a) not in retired]
        obstacles += submitted
        for p, r in rows:
            c = r['candidate']
            if p.name not in owners:
                continue
            if (c['family'] != 'line' and not c.get('shared_feed')) or any(c.get(k) for k in ('growth_of', 'repair_of', 'cutover_of')):
                continue
            extensions = [(op, other) for op, other in rows if other['candidate'].get('line_owner') == p.name
                          or other['candidate'].get('growth_of') == p.name or other['candidate'].get('repair_of') == p.name]
            if p.name in pending or any(other['status'] != 'observed' or op.name not in owners for op, other in extensions):
                continue
            if r['status'] != 'observed':
                continue
            current = max([c.get('furnaces', 0)] + [other['candidate'].get('target_furnaces', 0) for _, other in extensions])
            lineage = [(p, r), *extensions]
            existing = {target_key(a): (op.name, a) for op, other in lineage for a in actions(other['candidate'])
                        if target_key(a) not in retired}
            supply = []
            if c.get('shared_feed'):
                identity = requester_line_identity(c)
                if identity is None:
                    continue
                origin, flow, count = identity
                target = current + 2
                desired = actions({'plan': generate_requester_smelting_line(c['product'], target, origin_x=origin[0],
                                                                          origin_y=origin[1], request_count=count, flow=flow)})
                assembly_keys = {target_key(a) for a in actions({'plan': generate_requester_smelting_line(
                    c['product'], current, origin_x=origin[0], origin_y=origin[1], request_count=count, flow=flow)})}
                repair = False
            else:
                origin = c.get('line_origin')
                drills = [a for _, a in existing.values() if a['entity'] == 'electric-mining-drill']
                ys = sorted({a['position']['y'] for a in drills})
                if origin is None or len(ys) != 2 or c.get('furnaces') != 6:
                    continue
                belt_y = sum(ys) / 2
                collector = [a for _, a in existing.values() if a['entity'] == 'transport-belt'
                             and a['position']['y'] == belt_y and a.get('direction') in ('east', 'west')]
                if not collector:
                    continue
                # First repair previously submitted partial growth; never compound its capacity deficit.
                output = generate_managed_refinery_plan(c['product'], current, origin_x=origin[0], origin_y=origin[1], variant='basic')
                endpoint = output['phases'][-1]['actions']
                repair = len(drills) < current or any(target_key(a) not in existing for a in endpoint if a['entity'] not in POLES)
                target = current if repair else current + 6
                if len(drills) < target:
                    supply = paired_collector_extension(drills, collector, target - len(drills))
                desired = actions({'plan': generate_managed_refinery_plan(c['product'], target, origin_x=origin[0], origin_y=origin[1], variant='basic')})
                assembly_keys = {target_key(a) for a in actions({'plan': output})}
                # Original geometry used a different tap-power location. Retain shared power.
                legacy = output_taps(1, *origin, taps=c.get('output_taps', 1), inserter=c.get('tap_inserter', 'fast-inserter'))
                assembly_keys.update(target_key(a) for a in legacy if a['entity'] not in POLES)
                desired_keys = {target_key(a) for a in desired}
                desired += [a for _, a in existing.values() if a['entity'] in POLES and target_key(a) in assembly_keys
                            and target_key(a) not in desired_keys]
            assembly = [a for key, (_, a) in existing.items() if key in assembly_keys]
            prepare, remove, finish = split_owned_line_change(assembly, desired)
            additions = prepare + supply
            if not additions or not clear_of(additions, obstacles):
                continue
            # Finish may replace only owned blockers, never foreign infrastructure.
            removed_keys = {target_key(a) for a in remove}
            other_obstacles = [a for a in obstacles if target_key(a) not in removed_keys]
            if finish and not clear_of(finish, other_obstacles + additions):
                continue
            from planners.plan_validation import validate_build_plan
            try:
                validate_build_plan({'phases': [{'name': 'owned_line_growth', 'actions': additions}]})
            except ValueError:
                continue
            growth = self._candidate(c, additions, p.name, repair=repair)
            growth.update(target_furnaces=target, shared_feed=bool(c.get('shared_feed')),
                          summary=(f'Repair owned {current}-furnace supply and output' if repair else f'Extend owned line to {target} furnaces'),
                          line_transition={'retire': [{'owner': existing[target_key(a)][0], 'action': a} for a in remove],
                                           'finish': finish})
            proposals.append(growth)
        return proposals

    @staticmethod
    def _candidate(parent, actions, owner, repair=False):
        plan = {'surface': 'nauvis', 'force': 'player', 'atomic': True,
                'phases': [{'name': 'owned_mine_infill' if repair else 'owned_refinery_growth', 'actions': actions}]}
        digest = sha256_hash(plan)[7:19]
        return {'option_id': f'line.g{digest}', 'family': 'line', 'product': parent['product'],
                'summary': ('Replace depleted mining capacity on existing collector' if repair else 'Extend owned refinery to 12 furnaces'),
                'plan': plan, 'network': parent['network'], 'network_anchor': parent['network_anchor'],
                'repair_of' if repair else 'growth_of': owner, 'line_owner': owner, 'maintenance': repair,
                'reuses_infrastructure': True, 'internal_power': any(a['entity'] in POLES for a in actions),
                'next_slot': True,
                'furnaces': sum(a['entity'] == 'electric-furnace' for a in actions)}
