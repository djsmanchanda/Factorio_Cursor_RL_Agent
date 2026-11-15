# Path: training/laya_mission_capabilities.py | Purpose: Advertise the executable mixed runner's versioned mission interface.
import json
import re
import sys

ELIGIBILITY_VERSION = 'initial-research-eligibility-v1'
FAMILIES = ['research_select', 'research_labs', 'scale_production']
MANIFEST = {'version': 1, 'runners': {'tools/run_laya_mixed.py': {
    'objective_modes': ['measurement_only', 'research_completion'],
    'operation_families': FAMILIES, 'actions': 'deterministic'}}}


def validate_mode(mode, target):
    if mode is None:
        return
    if mode not in ('measurement_only', 'research_completion'):
        raise ValueError('invalid objective_mode')
    if mode == 'measurement_only' and target is not None:
        raise ValueError('measurement_only cannot activate research')
    if mode == 'research_completion' and (not isinstance(target, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,127}', target)):
        raise ValueError('research_completion requires a target')


def initial_eligibility(observation, target, facts):
    tech = observation.get('technology') or {}
    if (observation.get('available') is not True or observation.get('requested') != target
            or type(tech.get('target_completed')) is not bool or observation.get('tick') != facts.get('tick')
            or (facts.get('surface'), facts.get('force')) != ('nauvis', 'player') or not facts.get('mods')):
        return {'state': 'unknown', 'target': target}
    return {'state': 'inherited' if tech['target_completed'] else 'eligible', 'target': target,
            'canonical_name': tech.get('canonical_name'), 'requested_level': tech.get('requested_level'),
            'current_level': tech.get('current_level'), 'tick': observation.get('tick')}


def completed_target_refusal(target, initial_state, manifest):
    """Refusal message when the prepared save already completed ``target``, else None.

    Reads only the exact technology name in the recorded ``researched`` map
    (every researched prototype, written paused when the world is prepared).
    A ranged level such as mining-productivity-4 has no entry of its own, so
    it is not judged here; the runner's first live observation decides it.
    """
    researched = (initial_state or {}).get('researched')
    if not target or not isinstance(researched, dict) or researched.get(target) is not True:
        return None
    return refusal_message(target, manifest, tick=(initial_state or {}).get('tick'), evidence='initial-state.json')


def refusal_message(target, manifest, *, tick, evidence):
    manifest = manifest or {}
    return (f"research target {target!r} is already complete in save {manifest.get('source_save', 'unknown')} "
            f"(sha256 {str(manifest.get('source_sha256', 'unknown'))[:12]}, tick {tick}, from {evidence}); "
            "refusing before any decision")


def observe_start_and_research(objective, observation, facts, *, target, service_ok, clean):
    """Latch the first observed eligibility and apply it to earned evidence."""
    first = "initial_eligibility" not in objective.state
    if first:
        objective.state["initial_eligibility"] = initial_eligibility(observation, target, facts)
    objective.state['eligibility_version'] = ELIGIBILITY_VERSION
    evidence = objective.observe(observation, facts, service_ok=service_ok, clean=clean)
    eligibility = objective.state["initial_eligibility"]
    if eligibility["state"] != "eligible":
        objective.state["earned"] = evidence["earned"] = False
        objective.state["reason"] = evidence["reason"] = "initial_technology_" + eligibility["state"]
    return first, eligibility, evidence


if __name__ == '__main__':
    if sys.argv[1:] != ['--manifest']:
        raise SystemExit('usage: laya_mission_capabilities.py --manifest')
    print(json.dumps(MANIFEST, sort_keys=True))
