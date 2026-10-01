# Path: tests/test_laya_input_profile.py | Purpose: Prevent unrecorded health inputs from silently changing preserved policies.

import pytest

from training.laya_input_profile import input_profile, require_input_profile
from training.laya_runtime import LayaModelPredictor, LayaRuntimeError


def health_state(version='workflow-health-v1'):
    return {'observation_digest': {'factory_health': {'version': version}, 'work_branches': {}}}


def test_preserved_checkpoint_rejects_new_health_but_keeps_core_compatibility():
    assert require_input_profile({}, {'observation_digest': {'work_branches': {}}}) == 'workflow-core-v1'
    with pytest.raises(ValueError, match='no recorded'):
        require_input_profile({}, health_state())
    assert require_input_profile({}, health_state(), allow_unrecorded=True) == 'workflow-health-v1'


def test_trained_profiles_are_explicit_and_do_not_hide_unknown_health():
    config = {'input_profiles': ['workflow-health-v1', 'workflow-core-v1']}
    assert require_input_profile(config, health_state()) == 'workflow-health-v1'
    with pytest.raises(ValueError, match='not trained'):
        require_input_profile({'input_profiles': ['workflow-core-v1']}, health_state(), allow_unrecorded=True)
    with pytest.raises(ValueError, match='unsupported'):
        input_profile(health_state('future'))
    with pytest.raises(ValueError, match='invalid'):
        require_input_profile({'input_profiles': 'workflow-health-v1'}, health_state())


def test_predictor_rejects_profile_before_model_execution():
    predictor = object.__new__(LayaModelPredictor)
    predictor.config = {}
    predictor.allow_unrecorded_input_profile = False
    with pytest.raises(LayaRuntimeError, match='no recorded'):
        predictor.predict(health_state(), {'serve_goal': {}})


def test_unrecorded_profile_evaluation_is_diagnostic_only(monkeypatch, tmp_path):
    from tools import evaluate_laya
    seen = {}

    class Predictor:
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)

        def predict(self, *args):
            return {'answers': {'serve_goal': {'choice': 'serve.iron', 'probabilities': {'serve.iron': 1.0}}}}

    case = {'state': health_state(), 'questions': {'serve_goal': {'type': 'choice', 'criteria': {'serve.iron': 'Iron'}}},
            'gold': {'serve_goal': {'probabilities': {'serve.iron': 1.0}}}}
    monkeypatch.setattr(evaluate_laya, 'load_cases', lambda *args, **kwargs: [case])
    monkeypatch.setattr(evaluate_laya, 'LayaModelPredictor', Predictor)
    report = evaluate_laya.evaluate(tmp_path, tmp_path, tmp_path, 'held_out', 'cpu',
                                    allow_unrecorded_input_profile=True)
    assert seen['allow_unrecorded_input_profile'] is True
    assert report['status'] == 'ready' and report['input_profile_diagnostic'] is True
    assert report['promotion']['eligible_for_disposable_shadow'] is False
