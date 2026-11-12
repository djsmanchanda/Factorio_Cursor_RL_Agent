# Path: tests/test_laya_pm_replay.py | Purpose: Reject generic or relabelled C6 results as proof of PM operation.
import json

import pytest

from tools.laya_pm_replay import consumption

pytestmark = pytest.mark.fast


def test_pm_gate_requires_machine_specific_v3_evidence_from_disposable_world(tmp_path):
    (tmp_path/'evidence').mkdir()
    manifest = tmp_path/'manifest.json'
    manifest.write_text(json.dumps({'production_authority': False}))
    journal = tmp_path/'evidence/clean-decisions.jsonl'

    def sample(recipe, version='c6-v3', count=2):
        return {'event': 'strict_current', 'tick': 640390, 'run_id': 'replay',
                'statuses': {'C6': True}, 'predicate_evidence': {'C6': {
                    'predicate_version': version, 'evidence': {'machine_consumption': {recipe: count}}}}}

    journal.write_text('\n'.join(map(json.dumps, [sample('bulk-inserter'),
        sample('productivity-module', version='c6-v2'), sample('productivity-module', count=0)])))
    assert consumption(tmp_path)['pm_consumption_verified'] is False
    journal.write_text(json.dumps(sample('productivity-module')))
    result = consumption(tmp_path)
    assert result['pm_consumption_verified'] is True
    assert result['samples'] == [{'tick': 640390, 'run_id': 'replay', 'crafts': 2}]
    assert result['placement_receipt_required'] is True
    manifest.write_text(json.dumps({'production_authority': True}))
    with pytest.raises(ValueError, match='disposable'):
        consumption(tmp_path)
