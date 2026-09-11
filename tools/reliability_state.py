# Path: tools/reliability_state.py
# Purpose: Persist same-candidate plastic acceptance streaks and failure evidence.
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import jsonschema

from orchestrator.production_acceptance import evaluate

IDENTITY_FIELDS = ('repository_revision', 'source_save_sha256', 'deployed_factorio_mod_sha256',
                   'deployed_factorio_training_lab_sha256', 'bootstrap_profile', 'surface', 'force')


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n')
    temp.replace(path)


def load(path: Path) -> dict:
    if not path.exists():
        return {'version': 1, 'phase': 'plastic', 'streak': [], 'runs': [], 'baseline': None}
    payload = json.loads(path.read_text())
    if payload.get('version') != 1 or payload.get('phase') not in ('plastic', 'research'):
        raise ValueError('invalid reliability state')
    return payload


def assess(report: dict, manifest: dict, *, seconds: int) -> tuple[bool, str]:
    """Recheck raw acceptance samples and provenance, not only an ok flag."""
    try:
        schema = json.loads((Path(__file__).resolve().parents[1] / 'schemas/production_acceptance.schema.json').read_text())
        jsonschema.validate(report, schema)
    except (jsonschema.ValidationError, TypeError):
        return False, 'invalid acceptance report schema'
    if manifest.get("reliability_replay") or manifest.get("checkpoint_source"):
        return False, "checkpoint replay is not fresh-run acceptance"
    if (report.get('schema_version') != 1 or report.get('ok') is not True
            or report.get('result') != 'passed' or not manifest.get('episode_id')
            or report.get('episode_id') != manifest['episode_id']):
        return False, 'missing, failed or stale acceptance report'
    if report.get('target') != 'plastic-bar' or any(report.get(k) != v for k, v in (('surface', 'nauvis'), ('force', 'player'))):
        return False, 'acceptance target/scope mismatch'
    provenance = report.get('provenance', {})
    if any(not manifest.get(k) or provenance.get(k) != manifest[k] for k in IDENTITY_FIELDS):
        return False, 'acceptance provenance mismatch'
    if report.get('acceptance_seconds') != seconds or report.get('sample_seconds') != 10:
        return False, 'acceptance window mismatch'
    samples = report.get('samples', [])
    try:
        if not samples or any(any(s.get(k) != report[k] for k in ('target', 'surface', 'force')) for s in samples):
            return False, 'sample scope mismatch'
        if not evaluate(samples, seconds=seconds):
            return False, 'sustained production not demonstrated'
    except (TypeError, ValueError, KeyError):
        return False, 'invalid production samples'
    return True, 'sustained plastic production verified'


def record(path: Path, manifest: dict, report: dict, *, seconds: int, required: int, code_hash: str,
           milestones: dict | None = None) -> dict:
    state = load(path)
    episode = manifest.get('episode_id')
    if not episode:
        raise ValueError('episode identity required for reliability record')
    if any(run['episode_id'] == episode for run in state['runs']):
        return state
    passed, reason = assess(report, manifest, seconds=seconds)
    identity = {k: manifest.get(k) for k in IDENTITY_FIELDS}
    identity.update(code_hash=code_hash, acceptance_seconds=seconds)
    candidate = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if candidate != state.get('candidate') or not passed:
        state['streak'] = []
    state['candidate'] = candidate
    outcome = dict(episode_id=episode, passed=passed, reason=reason, candidate=candidate,
                   milestones=milestones or {}, end_tick=report.get('end_tick'))
    state['runs'].append(outcome)
    if passed:
        state['streak'].append(episode)
    if len(state['streak']) >= required:
        state['phase'] = 'research'
        state['baseline'] = dict(identity=identity, episodes=list(state['streak']), candidate=candidate)
    atomic_json(path, state)
    return state
