# Path: training/laya_outcome_export.py | Purpose: Validate joined observational choice outcomes without claiming counterfactuals.
from __future__ import annotations

import hashlib
import json
import math

from orchestrator.checkpoint_milestones import DEFAULT_MILESTONES


PREDICATES = {item.checkpoint_id: item.predicate_version for item in DEFAULT_MILESTONES}


def export(choices: list[dict], receipts: list[dict], outcomes: list[dict]) -> list[dict]:
    receipt_by_id = _index(receipts, "choice_id")
    outcome_by_id = _index(outcomes, "attempt_id")
    result = []
    seen = set()
    for choice in choices:
        choice_id = choice.get("choice_id")
        attempt = choice.get("attempt_id")
        ancestor = choice.get("ancestor_sha256")
        if not choice_id or choice_id in seen or not attempt or not choice.get("lineage_id") or not isinstance(choice.get("state"), dict) or not isinstance(ancestor, str) or len(ancestor) != 64 or any(c not in "0123456789abcdef" for c in ancestor):
            raise ValueError("missing or duplicate choice or ancestry")
        seen.add(choice_id)
        receipt = receipt_by_id.get(choice_id)
        outcome = outcome_by_id.get(attempt)
        if not receipt or not outcome or receipt.get("attempt_id") != attempt or outcome.get("ancestor_sha256") != ancestor or outcome.get("lineage_id") != choice["lineage_id"]:
            raise ValueError("missing or mismatched outcome join")
        menu = choice.get("menu")
        if not isinstance(menu, list) or not menu:
            raise ValueError("missing full menu")
        menu_hash = hashlib.sha256(json.dumps(menu, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
        if choice.get("menu_sha256") != menu_hash:
            raise ValueError("menu hash mismatch")
        probabilities = {}
        for option in menu:
            if not isinstance(option, dict):
                raise ValueError("invalid menu entry")
            option_id, probability, legal = option.get("option_id"), option.get("probability"), option.get("legal")
            if not isinstance(option_id, str) or not option_id or option_id in probabilities or type(legal) is not bool or isinstance(probability, bool) or not isinstance(probability, (float, int)) or not math.isfinite(probability) or probability < 0 or (not legal and probability != 0):
                raise ValueError("invalid menu distribution")
            probabilities[option_id] = probability
        selected = choice.get("selected_option_id")
        if selected not in probabilities or probabilities[selected] <= 0 or not math.isclose(sum(probabilities.values()), 1, abs_tol=1e-9):
            raise ValueError("invalid selected propensity or distribution")
        if receipt.get("selected_option_id") != selected or not receipt.get("status"):
            raise ValueError("receipt selection or status missing")
        if outcome.get("status") not in {"completed", "functional_failure", "infrastructure_failure", "timeout", "cancelled"} or type(outcome.get("censored")) is not bool:
            raise ValueError("terminal status and censoring required")
        predicate_hash = outcome.get("predicate_sha256")
        if not isinstance(predicate_hash, str) or len(predicate_hash) != 64 or any(c not in "0123456789abcdef" for c in predicate_hash) or not isinstance(outcome.get("strict_measurements"), list):
            raise ValueError("versioned strict observations required")
        if type(choice.get("tick")) is not int or type(outcome.get("end_tick")) is not int or outcome["end_tick"] < choice["tick"]:
            raise ValueError("invalid observation horizon")
        for measurement in outcome["strict_measurements"]:
            if not isinstance(measurement, dict) or type(measurement.get("tick")) is not int or not 0 <= measurement["tick"] <= outcome["end_tick"] or "value" not in measurement or (measurement.get("value") is not None and type(measurement.get("value")) is not bool) or measurement.get("checkpoint") not in PREDICATES or measurement.get("predicate_version") != PREDICATES[measurement["checkpoint"]]:
                raise ValueError("invalid delayed strict measurement")
        delayed = [measurement for measurement in outcome["strict_measurements"] if measurement["tick"] >= choice["tick"]]
        bucket = int(hashlib.sha256(ancestor.encode()).hexdigest()[:8], 16) % 10
        result.append({"label_kind": "observational", "split": "test" if bucket == 0 else "validation" if bucket == 1 else "train",
                       "ancestor_sha256": ancestor, "choice": choice, "receipt": receipt,
                       "outcome": {**outcome, "strict_measurements": delayed},
                       "horizon_ticks": outcome["end_tick"] - choice["tick"],
                       "selected_propensity": probabilities[selected]})
    if set(receipt_by_id) != seen:
        raise ValueError("orphan receipts")
    if set(outcome_by_id) != {row["choice"]["attempt_id"] for row in result}:
        raise ValueError("orphan outcomes")
    return result


def export_journal(records: list[dict], outcome: dict) -> list[dict]:
    """Convert native guided-policy decision records and an independently recorded outcome."""
    choices, receipts = [], []
    attempt = outcome.get("attempt_id")
    for record in records:
        if record.get("version") != "laya-decision-record-v1" or record.get("run_id") != attempt:
            raise ValueError("unsupported or mismatched decision record")
        index = record.get("index")
        if type(index) is not int or index < 0:
            raise ValueError("invalid decision index")
        options, distribution = record.get("options"), record.get("distribution")
        if not isinstance(options, list) or not isinstance(distribution, list):
            raise ValueError("native menu and distribution required")
        weights = _index(distribution, "option_id")
        if len(options) != len(weights):
            raise ValueError("native menu and distribution mismatch")
        menu = []
        for option in options:
            if not isinstance(option, dict) or option.get("option_id") not in weights:
                raise ValueError("native menu and distribution mismatch")
            weight = weights[option["option_id"]]
            menu.append({**option, "probability": weight.get("probability"), "legal": True})
        selected = (record.get("choice") or {}).get("option_id")
        selected_weight = weights.get(selected, {}).get("probability")
        if (record.get("choice") or {}).get("probability") != selected_weight:
            raise ValueError("native selected probability mismatch")
        native_outcome = record.get("outcome")
        if not isinstance(native_outcome, dict) or type(native_outcome.get("ok")) is not bool:
            raise ValueError("native operation outcome required")
        choice_id = f"{attempt}:{index}"
        choices.append({"choice_id": choice_id, "attempt_id": attempt,
                        "ancestor_sha256": outcome.get("ancestor_sha256"), "lineage_id": outcome.get("lineage_id"),
                        "state": record.get("state"), "tick": record.get("tick"), "menu": menu,
                        "menu_sha256": hashlib.sha256(json.dumps(menu, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                        "selected_option_id": selected, "menu_legality": "offered_not_independently_verified"})
        receipts.append({"choice_id": choice_id, "attempt_id": attempt, "selected_option_id": selected,
                         "status": "ok" if native_outcome["ok"] else "failed", "native_outcome": native_outcome})
    return export(choices, receipts, [outcome])


def _index(rows: list[dict], key: str) -> dict:
    indexed = {}
    for row in rows:
        value = row.get(key)
        if not value or value in indexed:
            raise ValueError(f"missing or duplicate {key}")
        indexed[value] = row
    return indexed
