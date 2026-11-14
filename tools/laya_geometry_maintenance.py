# Path: tools/laya_geometry_maintenance.py | Purpose: Bridge receipt-owned geometry to shared bot recovery and additive refinery growth.
from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

from orchestrator.recoverable_retirement import retire_entities_via_bots
from orchestrator.laya_operations import sha256_hash
from planners.smelter_block import generate_refinery_extension_plan
from planners.resource_layouts import collector_drill_infill
from training.laya_layout_stream import atomic_json
from training.laya_lines import clear_of
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


class GeometryMaintenance:
    def __init__(self):
        self._receipts = {}

    def __call__(self, adapter, facts, emit=lambda _: None):
        path = Path(adapter.root, 'evidence', 'geometry-maintenance.json')
        ledger = json.loads(path.read_text()) if path.exists() else {'retired': [], 'delivered': {}}
        rows = [(p, r) for p, r in receipts(adapter.root)
                if r['candidate'].get('family') in ('starter', 'line') and r['submission'].get('ok')]
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
        atomic_json(path, ledger)
        proposals = self._growth(rows, facts, retired, {p.name for p, r in raw.items() if owned_targets(r)})
        return {'candidates': proposals, 'retired': recovered,
                'constructing_products': sorted({r['candidate']['product'] for _, r in rows
                                                if r['candidate']['family'] == 'line' and r['status'] != 'observed'})}

    def _growth(self, rows, facts, retired, owners):
        proposals = []
        obstacles = world_obstacles(facts)
        submitted = [a for _, r in rows for phase in r['candidate']['plan']['phases'] for a in phase['actions']
                     if target_key(a) not in retired]
        obstacles += submitted
        for p, r in rows:
            c = r['candidate']
            if p.name not in owners:
                continue
            if c['family'] != 'line' or c.get('growth_of') or c.get('repair_of'):
                continue
            extensions = [other for _, other in rows if other['candidate'].get('growth_of') == p.name]
            original = [a for phase in c['plan']['phases'] for a in phase['actions']]
            drills = [a for a in original if a['entity'] == 'electric-mining-drill']
            if not drills:
                continue
            ys = sorted({a['position']['y'] for a in drills})
            if len(ys) != 2:
                continue
            belt_y = (ys[0] + ys[1]) / 2
            collector = [a for a in original if a['entity'] == 'transport-belt' and a['position']['y'] == belt_y
                         and a.get('direction') in ('east', 'west')]
            # Infill uses the already connected collector; never another haul or provider.
            repairs = [a for a in collector_drill_infill(collector) if clear_of([a], obstacles)]
            lineage_drills = drills + [a for other in extensions for phase in other['candidate']['plan']['phases']
                                      for a in phase['actions'] if a['entity'] == 'electric-mining-drill']
            lost = sum(target_key(a) in retired for a in lineage_drills)
            for a in repairs[:12] if lost else ():
                proposals.append(self._candidate(c, [a], p.name, repair=True))
            if r['status'] != 'observed' or c.get('furnaces') != 6:
                continue
            if any(other['status'] != 'observed' for other in extensions):
                continue
            current = max([6] + [other['candidate'].get('target_furnaces', 12) for other in extensions])
            # The approved basic template's first furnace offset is part of its interface.
            # New receipts record the origin; old receipts cannot authorize a guessed expansion.
            origin = c.get('line_origin')
            if origin is None:
                continue
            plan = generate_refinery_extension_plan(c['product'], current, current + 6, origin_x=origin[0], origin_y=origin[1],
                                                    current_variant='basic')
            additions = [a for phase in plan['phases'] for a in phase['actions']]
            if any(a['action_type'] != 'place_ghost' for a in additions):
                continue
            existing = original + [a for other in extensions for phase in other['candidate']['plan']['phases']
                                   for a in phase['actions']]
            known = {target_key(a): a for a in existing}
            # Shared boundary tiles are reused only when their direction agrees.
            if any(target_key(a) in known and a.get('direction', 'north') != known[target_key(a)].get('direction', 'north')
                   for a in additions):
                continue
            additions = [a for a in additions if target_key(a) not in known]
            if not clear_of(additions, obstacles):
                continue
            # Existing collector infill supplies growth; if no drill site is free, expansion is not ready.
            if not repairs:
                continue
            infill = []
            for a in repairs:
                if clear_of([a], infill + additions):
                    infill.append(a)
                    if len(infill) == 6:
                        break
            if infill:
                growth = self._candidate(c, additions + infill, p.name)
                growth['target_furnaces'] = current + 6
                growth['summary'] = f"Extend owned refinery to {current + 6} furnaces"
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
                'repair_of' if repair else 'growth_of': owner, 'maintenance': repair,
                'reuses_infrastructure': True, 'internal_power': not repair,
                'next_slot': True,
                'furnaces': 0 if repair else 6}
