# Path: tests/test_laya_evaluate.py
# Purpose: Verify held-out evaluation metrics without loading the 421M model.

from __future__ import annotations

import json

from tools import evaluate_laya


class _FakePredictor:
    def __init__(self, checkpoint, *, base_model_dir, device):
        del checkpoint, base_model_dir, device

    def predict(self, state, questions):
        del state, questions
        return {
            "answers": {
                "select_candidate": {
                    "type": "choice",
                    "choice": "a",
                    "confidence": 0.8,
                    "probabilities": {"a": 0.8, "b": 0.2},
                }
            }
        }


def test_evaluate_reports_choice_metrics(monkeypatch, tmp_path) -> None:
    cases = tmp_path / "cases.jsonl"
    cases.write_text(json.dumps({
        "case_id": "case-1",
        "split": "held_out",
        "state": {},
        "questions": {"select_candidate": {"type": "choice", "instructions": "choose", "criteria": {"a": "A", "b": "B"}}},
        "gold": {"select_candidate": {"probabilities": {"a": 1.0, "b": 0.0}}},
    }) + "\n", encoding="utf-8")
    monkeypatch.setattr(evaluate_laya, "LayaModelPredictor", _FakePredictor)
    report = evaluate_laya.evaluate(cases, tmp_path / "checkpoint", tmp_path / "base", "held_out", None)
    assert report["status"] == "ready"
    assert report["questions"] == 1
    assert report["exact_accuracy"] == 1.0
    assert report["promotion"]["eligible_for_disposable_shadow"] is True
