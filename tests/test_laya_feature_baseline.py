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
