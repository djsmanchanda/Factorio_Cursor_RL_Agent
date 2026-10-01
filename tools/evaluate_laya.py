# Path: tools/evaluate_laya.py
# Purpose: Score a local Laya checkpoint on an independent seed-held-out JSONL split.

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from training.laya_finetune import load_cases
from training.laya_runtime import LayaModelPredictor, LayaRuntimeError


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _ece(confidences: list[float], correct: list[bool], bins: int = 10) -> float:
    if not confidences:
        return 0.0
    total = len(confidences)
    error = 0.0
    for index in range(bins):
        low = index / bins
        high = (index + 1) / bins
        selected = [
            position for position, confidence in enumerate(confidences)
            if (confidence > low or index == 0 and confidence == low) and confidence <= high
        ]
        if selected:
            mean_confidence = sum(confidences[position] for position in selected) / len(selected)
            mean_correct = sum(bool(correct[position]) for position in selected) / len(selected)
            error += len(selected) / total * abs(mean_confidence - mean_correct)
    return error


def _in_option_order(questions: dict[str, Any]) -> dict[str, Any]:
    """Dataset writers sort JSON keys; option_ids is the model's option order (as in fine-tuning)."""
    result = {}
    for question_id, question in questions.items():
        order, criteria = question.get("option_ids"), question.get("criteria")
        if isinstance(order, list) and isinstance(criteria, dict) and set(order) == set(criteria):
            question = {**question, "criteria": {key: criteria[key] for key in order}}
        result[question_id] = question
    return result


def evaluate(cases_path: Path, checkpoint_dir: Path, base_model_dir: Path, split: str, device: str | None, *, allow_legacy_sequence: bool = False, allow_unrecorded_input_profile: bool = False) -> dict[str, Any]:
    cases = load_cases(cases_path, split=split)
    extra = {"allow_legacy_sequence": True} if allow_legacy_sequence else {}
    if allow_unrecorded_input_profile:
        extra['allow_unrecorded_input_profile'] = True
    predictor = LayaModelPredictor(checkpoint_dir, base_model_dir=base_model_dir, device=device, **extra)
    exact = 0
    questions = 0
    nll = 0.0
    brier = 0.0
    confidences: list[float] = []
    correct: list[bool] = []
    latencies: list[float] = []
    failures: list[str] = []
    for case in cases:
        started = time.perf_counter()
        questions_in_order = _in_option_order(case["questions"])
        try:
            result = predictor.predict(case["state"], questions_in_order)
        except Exception as error:  # a malformed case must not become silent credit
            failures.append(f"{case.get('case_id', 'unknown')}: {type(error).__name__}: {error}")
            continue
        latencies.append(time.perf_counter() - started)
        for question_id, question in questions_in_order.items():
            gold = (case.get("gold") or {}).get(question_id) or {}
            answer = (result.get("answers") or {}).get(question_id) or {}
            criteria = question.get("criteria") or {}
            if question.get("type") != "choice" or not isinstance(criteria, dict):
                continue
            keys = list(criteria)
            target = [float((gold.get("probabilities") or {}).get(key, 0.0)) for key in keys]
            total = sum(target)
            if total <= 0.0:
                continue
            target = [value / total for value in target]
            probabilities = [float((answer.get("probabilities") or {}).get(key, 0.0)) for key in keys]
            probability_total = sum(probabilities)
            if probability_total <= 0.0:
                probabilities = [1.0 / len(keys)] * len(keys)
            else:
                probabilities = [value / probability_total for value in probabilities]
            selected = answer.get("choice")
            gold_label = keys[target.index(max(target))]
            is_correct = selected == gold_label
            exact += int(is_correct)
            questions += 1
            nll -= sum(weight * math.log(max(probability, 1e-9)) for weight, probability in zip(target, probabilities))
            brier += sum((probability - weight) ** 2 for probability, weight in zip(probabilities, target))
            confidences.append(float(answer.get("confidence", max(probabilities))))
            correct.append(is_correct)
    report = {
        "version": "1.0.0",
        "legacy_diagnostic": allow_legacy_sequence,
        "input_profile_diagnostic": allow_unrecorded_input_profile,
        "sequence_version": getattr(predictor, "sequence_version", "unknown"),
        "status": "ready" if not failures and questions else "blocked",
        "split": split,
        "cases": len(cases),
        "questions": questions,
        "exact_accuracy": exact / questions if questions else 0.0,
        "nll": nll / questions if questions else None,
        "brier": brier / questions if questions else None,
        "ece": _ece(confidences, correct),
        "mean_latency_ms": (sum(latencies) / len(latencies) * 1000.0) if latencies else None,
        "p95_latency_ms": sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)] * 1000.0 if latencies else None,
        "failures": failures[:20],
        "checkpoint": str(checkpoint_dir),
        "checkpoint_hash": _file_hash(checkpoint_dir / "model.safetensors") or _file_hash(checkpoint_dir / "adapter_model.safetensors"),
        "promotion": {
            "zero_inference_failures": not failures,
            "held_out_evidence_present": bool(questions),
            "eligible_for_disposable_shadow": not failures and bool(questions) and not (
                allow_legacy_sequence or allow_unrecorded_input_profile),
            "eligible_for_real_nauvis": False,
        },
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--base-model", required=True, type=Path)
    parser.add_argument("--split", default="held_out", choices=("held_out", "test", "calibration", "train"))
    parser.add_argument("--device")
    parser.add_argument("--allow-legacy-sequence", action="store_true", help="Explicit diagnostic scoring of a preserved legacy checkpoint")
    parser.add_argument('--allow-unrecorded-input-profile', action='store_true',
                        help='Diagnostic replay of preserved checkpoints with unrecorded observations; grants no shadow eligibility')
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = evaluate(args.cases, args.checkpoint, args.base_model, args.split, args.device,
                          allow_legacy_sequence=args.allow_legacy_sequence,
                          allow_unrecorded_input_profile=args.allow_unrecorded_input_profile)
    except (LayaRuntimeError, ValueError, OSError) as error:
        report = {"version": "1.0.0", "status": "blocked", "reason": str(error)}
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report.get("status") == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
