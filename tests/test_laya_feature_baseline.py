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
