# Path: training/laya_repair.py | Purpose: Compile unranked restoration choices for previously built disposable cells.

from collections import Counter
from collections.abc import Mapping
import copy

from training.candidates.connected_workflow import _ITEM_COST
from training.isolation import assert_training_identity


def restoration_candidates(candidates, report: Mapping, executed_plan_hashes, remaining_budget):
    """Replay only a known plan with observed losses, charging the missing bill.

    Counts describe phase-owned survivors, not a proposed diagnosis. The lab
    rechecks exact positions, collisions and total commitments before replay;
    already present entities are retained. Unknown ownership never implies loss.
    """
    assert_training_identity(report['surface'], report['force'])
    if report.get('status') not in {'ready','running'}:
        return []
    owned = (report.get('metrics') or {}).get('live_entities_by_phase')
    if not isinstance(owned, Mapping):
        return []
    result = []
    for candidate in candidates:
        if candidate['plan_hash'] not in executed_plan_hashes:
            continue
        plan = candidate['plan']
        if (plan['surface'], plan['force']) != (report['surface'], report['force']):
            raise ValueError('restoration plan belongs to another training scope')
        missing = Counter()
        valid = True
        for phase in plan['phases']:
            counts = owned.get(phase['name'])
            if not isinstance(counts, Mapping) or any(type(n) is not int or n < 0 for n in counts.values()):
                valid = False
                break
            expected = Counter(action['entity'] for action in phase['actions'])
            if any(n > expected.get(name, 0) for name, n in counts.items()):
                valid = False
                break
            missing.update({name: n-counts.get(name, 0) for name, n in expected.items()})
        if not valid or not sum(missing.values()) or any(n > remaining_budget.get(name, 0) for name, n in missing.items()):
            continue
        restored = copy.deepcopy(candidate)
        restored['action_id'] = 'repair.' + candidate['action_id']
        restored['repair'] = True
        restored['missing_entities'] = dict(missing)
        restored['features'].update(
            entity_count=float(sum(missing.values())),
            material_cost=float(sum(_ITEM_COST.get(name, 0)*n for name, n in missing.items())),
            pole_count=float(sum(n for name, n in missing.items() if name.endswith('electric-pole'))),
            repair=1.0,
        )
        result.append(restored)
    return result
