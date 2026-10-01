# Path: tests/test_laya_feature_baseline.py
# Purpose: Verify the features-only baseline learns a feature preference and acts as a predictor.

from __future__ import annotations

from training.laya_feature_baseline import FeaturePredictor, accuracy, train


def _case(cheap_first: bool):
    options = [
        {"option_id": "iron.basic", "features": {"material_cost": 80.0}},
        {"option_id": "iron.fast", "features": {"material_cost": 85.0}},
    ]
    if not cheap_first:
        options.reverse()
    ids = [option["option_id"] for option in options]
    return {"state": {"options": options}, "questions": {"select_candidate": {"option_ids": ids, "criteria": {i: i for i in ids}}},
            "gold": {"select_candidate": {"label": "iron.basic", "probabilities": {"iron.basic": 0.9, "iron.fast": 0.1}}}}


def test_learns_cheaper_variant_regardless_of_order() -> None:
    cases = [_case(index % 2 == 0) for index in range(20)]
    model = train(cases, epochs=300)
    assert accuracy(model, cases) == 1.0
    answer = FeaturePredictor(model).predict(cases[1]["state"], cases[1]["questions"])
    assert answer["answers"]["select_candidate"]["choice"] == "iron.basic"


def test_demand_context_lets_expansion_depend_on_target() -> None:
    def case(target_per_second: float):
        options = [{"option_id": "serve.iron2", "features": {"material_cost": 90.0}},
                   {"option_id": "wait.5s", "features": {"wait_seconds": 5.0}}]
        ids = [option["option_id"] for option in options]
        expand = target_per_second > 0.5
        state = {"options": options,
                 "acceptance_gap": {"target": target_per_second / 60.0, "current": 0.4 / 60.0},
                 "observation_digest": {"item_supply": {"transport-belt": {"output_rate": 0.4 / 60.0}}}}
        gold = "serve.iron2" if expand else "wait.5s"
        return {"state": state, "questions": {"serve_goal": {"option_ids": ids, "criteria": {i: i for i in ids}}},
                "gold": {"serve_goal": {"label": gold, "probabilities": {i: float(i == gold) for i in ids}}}}

    cases = [case(target) for target in (0.1, 0.2, 0.3, 0.6, 0.65, 0.7) * 4]
    assert accuracy(train(cases, epochs=400), cases) == 1.0


def test_consecutive_waits_change_the_features() -> None:
    from training.laya_feature_baseline import option_vector

    option = {"option_id": "wait.1s", "features": {"wait_seconds": 1.0}}
    fresh = {"run": {"elapsed_ticks": 60}, "recent_history": []}
    looped = {"run": {"elapsed_ticks": 60}, "recent_history": [{"summary": "Wait 1 second."}] * 3}
    assert (option_vector(option, fresh) != option_vector(option, looped)).any()


def test_weighted_evidence_dominates_conflicting_low_weight_labels():
    important = _case(True)
    important["weights"] = {"case_weight": 1.0, "question_weights": {"select_candidate": 1.0}}
    weak = _case(True)
    weak["gold"]["select_candidate"]["probabilities"] = {"iron.basic": 0.0, "iron.fast": 1.0}
    weak["weights"] = {"case_weight": 0.01}
    model = train([important] + [weak] * 10, epochs=300)
    answer = FeaturePredictor(model).predict(important["state"], important["questions"])
    assert answer["answers"]["select_candidate"]["choice"] == "iron.basic"


def test_variant_tradeoff_can_change_with_demand_and_legacy_width_is_preserved():
    import numpy as np
    from training.laya_feature_baseline import option_vector

    def case(target):
        row = _case(True)
        row['state']['acceptance_gap'] = {'target': target / 60, 'current': 0}
        row['state']['options'][0]['features'].update(machine_speed=0.5)
        row['state']['options'][1]['features'].update(material_cost=160, machine_speed=1.5)
        choice = 'iron.basic' if target < 0.5 else 'iron.fast'
        row['gold']['select_candidate'] = {'label': choice, 'probabilities': {i: float(i == choice)
                                             for i in row['questions']['select_candidate']['option_ids']}}
        return row

    low, high = case(0.1), case(0.9)
    def delta(row, version):
        options = row['state']['options']
        return option_vector(options[1], row['state'], version) - option_vector(options[0], row['state'], version)
    assert np.allclose(delta(low, 6), delta(high, 6))
    assert not np.allclose(delta(low, 7), delta(high, 7))
    legacy = option_vector(low['state']['options'][0], low['state'], 6)
    assert np.array_equal(option_vector(low['state']['options'][0], low['state'], 7)[:len(legacy)], legacy)
    cases = [case(t) for t in (0.1, 0.2, 0.3, 0.7, 0.8, 0.9) * 4]
    model = train(cases, epochs=400)
    predictor = FeaturePredictor(model)
    for t, expected in ((0.25, 'iron.basic'), (0.85, 'iron.fast')):
        row = case(t)
        assert predictor.predict(row['state'], row['questions'])['answers']['select_candidate']['choice'] == expected
