# Path: tests/test_laya_segment_evidence.py | Purpose: Protect current strict evidence and honest stop classifications.
from types import SimpleNamespace

import pytest

from training.laya_segment_evidence import current_predicates, stop_classification
from tests.test_laya_restart_state import snapshot

pytestmark = pytest.mark.fast


def test_historical_latch_cannot_report_a_current_pass():
    baseline = snapshot(0, 1)
    statuses, evidence = current_predicates(SimpleNamespace(_history=[baseline], _baseline=baseline,
                                                          _evaluator=SimpleNamespace(reached={'C4'})))
    assert statuses['C4'] is False
    assert evidence['C4']['status'] == 'failed'


def test_proxy_frontier_and_budget_are_not_genuine_failures():
    assert stop_classification('phase_ladder_complete') == 'capability_limit'
    assert stop_classification('window_timeout') == 'tick_timeout'
    assert stop_classification('strict_target_reached') == 'target_success'
    assert stop_classification('research_complete') == 'target_success'
    assert stop_classification('unrecognized') == 'unknown'
