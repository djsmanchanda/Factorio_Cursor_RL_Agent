# Path: training/laya_sequence.py
# Purpose: Build one versioned, non-truncating Laya decision sequence for training and inference.

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from orchestrator.laya_state import validate_state


SEQUENCE_VERSION = "factorio-decision-v2"
LEGACY_SEQUENCE_VERSION = "laya-upstream-truncating-v1"


class LayaSequenceError(ValueError):
    """Raised when a decision cannot be represented without losing evidence."""


def _copy_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _prune_unknown(value: Any, *, keep_empty: bool = False) -> Any:
    """Remove explicit unknowns; absence is the compact-v2 unknown marker."""
    if isinstance(value, Mapping):
        result = {
            key: _prune_unknown(item, keep_empty=key in {"history", "failures"})
            for key, item in value.items()
            if item is not None
        }
        return {
            key: item for key, item in result.items()
            if item not in ({}, []) or key in {"history", "failures"}
        }
    if isinstance(value, list):
        result = [_prune_unknown(item) for item in value]
        return result if result or keep_empty else []
    return value


def project_decision_state(state: Mapping[str, Any]) -> dict[str, Any]:
    """Project validated evidence into the stable compact-v2 model context.

    Full plans, hashes, registry manifests, and repeated scope identities remain
    in the evidence store. The model receives the facts needed to decide: the
    objective and gaps, prior outcomes, active failures, rules, budgets, and
    every presented option's description and numeric features.
    """
    validate_state(state)
    run = _copy_mapping(state.get("run"))
    transaction = state.get("current_transaction")
    projected_transaction: dict[str, Any] | None = None
    if isinstance(transaction, Mapping):
        projected_transaction = {
            "operation": transaction.get("operation_id"),
            "status": transaction.get("status"),
            "started_tick": transaction.get("started_tick"),
            "expected_effects": list(transaction.get("expected_effects") or []),
        }

    guidelines = []
    for guideline in state.get("guidelines") or []:
        item = {
            "id": guideline.get("rule_id"),
            "kind": guideline.get("kind"),
            "statement": guideline.get("statement"),
        }
        if guideline.get("rationale"):
            item["rationale"] = guideline["rationale"]
        guidelines.append(item)

    options = []
    for option in state.get("options") or []:
        options.append({
            "id": option.get("option_id"),
            "family": option.get("operation_family"),
            "tool": option.get("tool_id"),
            "summary": option.get("summary"),
            "features": dict(option.get("features") or {}),
            "risk": option.get("risk_tier"),
        })

    projected: dict[str, Any] = {
        "sequence_version": SEQUENCE_VERSION,
        "run": {
            "tick": run.get("tick"),
            "elapsed_ticks": run.get("elapsed_ticks"),
            "runtime": run.get("runtime"),
            "surface": run.get("surface"),
            "force": run.get("force"),
        },
        "objective": dict(state["objective"]),
        "goals": [dict(item) for item in state["goal_stack"]],
        "acceptance": dict(state["acceptance_gap"]),
        "transaction": projected_transaction,
        "digest": dict(state["state_digest"]),
        "history": [dict(item) for item in state["recent_history"]],
        "failures": [dict(item) for item in state["active_failures"]],
        "guidelines": guidelines,
        "options": options,
        "budget": dict(state["budget"]),
    }
    # Coach observations are already bounded by the state adapter and provide
    # facts which the normalized state digest cannot yet express.
    if isinstance(state.get("observation_digest"), Mapping):
        projected["observation"] = dict(state["observation_digest"])
    return _prune_unknown(projected)


def normalize_decision_question(question: Mapping[str, Any]) -> dict[str, Any]:
    """Accept the repository or upstream question spelling and validate it."""
    if not isinstance(question, Mapping):
        raise LayaSequenceError("Laya question must be an object")
    qtype = question.get("type", question.get("t"))
    instructions = question.get("instructions", question.get("ins", ""))
    criteria = question.get("criteria", question.get("crit"))
    if qtype not in {"choice", "score", "noul"}:
        raise LayaSequenceError(f"unsupported Laya question type: {qtype!r}")
    if not isinstance(instructions, str) or not instructions.strip():
        raise LayaSequenceError("Laya question instructions must be non-empty text")
    if qtype == "choice":
        if isinstance(criteria, Sequence) and not isinstance(criteria, (str, bytes)):
            criteria = {str(item): None for item in criteria}
        if not isinstance(criteria, Mapping) or not criteria:
            raise LayaSequenceError("choice questions require at least one criterion")
        criteria = dict(criteria)
        if any(not isinstance(key, str) or not key for key in criteria):
            raise LayaSequenceError("choice option IDs must be non-empty strings")
    elif qtype == "score":
        if not isinstance(criteria, Sequence) or isinstance(criteria, (str, bytes)) or not criteria:
            raise LayaSequenceError("score questions require a non-empty criteria list")
        criteria = list(criteria)
    else:
        if criteria is None:
            criteria = {}
        if not isinstance(criteria, Mapping):
            raise LayaSequenceError("noul criteria must be an object")
        criteria = dict(criteria)
    return {"t": qtype, "ins": instructions, "crit": criteria}


def _criterion_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(", ", ": "), default=str)


def render_decision_options(question: Mapping[str, Any]) -> list[str]:
    """Render options in decision-head order without shortening descriptions."""
    normalized = normalize_decision_question(question)
    qtype = normalized["t"]
    criteria = normalized["crit"]
    if qtype == "choice":
        return [
            key if value is None or value == "" else f"{key}: {_criterion_text(value)}"
            for key, value in criteria.items()
        ]
    if qtype == "score":
        return [f"level {index}: {_criterion_text(value)}" for index, value in enumerate(criteria)]
    false_value = criteria.get("false")
    true_value = criteria.get("true")
    return [
        "false: " + (
            _criterion_text(false_value)
            if false_value not in (None, "")
            else "no, the statement does not hold"
        ),
        "true: " + (
            _criterion_text(true_value)
            if true_value not in (None, "")
            else "yes, the statement holds"
        ),
    ]


def _encode(tokenizer: Any, text: str) -> list[int]:
    try:
        encoded = tokenizer(text, add_special_tokens=False, truncation=False)
    except TypeError:
        encoded = tokenizer(text, add_special_tokens=False)
    except Exception as error:
        raise LayaSequenceError(f"Laya tokenizer failed: {error}") from error
    if isinstance(encoded, Mapping):
        encoded = encoded.get("input_ids")
    if (
        isinstance(encoded, Sequence)
        and encoded
        and isinstance(encoded[0], Sequence)
        and not isinstance(encoded[0], (str, bytes))
    ):
        if len(encoded) != 1:
            raise LayaSequenceError("Laya tokenizer returned more than one sequence")
        encoded = encoded[0]
    if not isinstance(encoded, Sequence) or isinstance(encoded, (str, bytes)):
        raise LayaSequenceError("Laya tokenizer did not return token IDs")
    try:
        return [int(item) for item in encoded]
    except (TypeError, ValueError) as error:
        raise LayaSequenceError("Laya tokenizer returned invalid token IDs") from error


def _token_id(tokenizer: Any, name: str) -> int:
    value = getattr(tokenizer, name, None)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LayaSequenceError(f"tokenizer has no valid {name}")
    return value


def _limit(value: Any, name: str, *, minimum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise LayaSequenceError(f"{name} must be an integer of at least {minimum}")
    return value


def build_decision_sequence(
    tokenizer: Any,
    state: Mapping[str, Any],
    question: Mapping[str, Any],
    max_len: int,
    head_max_len: int,
) -> tuple[list[int], list[int]]:
    """Encode the exact model input and reject overflow without truncation."""
    max_len = _limit(max_len, "max_len", minimum=16)
    head_max_len = _limit(head_max_len, "head_max_len", minimum=8)
    if head_max_len >= max_len:
        raise LayaSequenceError("head_max_len must leave room for decision state")
    normalized = normalize_decision_question(question)
    projected = project_decision_state(state)
    if normalized["t"] == "choice":
        state_option_ids = [str(item["id"]) for item in projected["options"]]
        question_option_ids = list(normalized["crit"])
        if state_option_ids != question_option_ids:
            raise LayaSequenceError(
                "choice criteria must match projected state options in the same order"
            )
        # The choice head already carries every real option description. Avoid
        # repeating it in state, while retaining summaries for legacy cases
        # whose criteria contain only an ID or no description.
        descriptions = normalized["crit"]
        if all(
            value not in (None, "") and str(value) != key
            for key, value in descriptions.items()
        ):
            for option in projected["options"]:
                option.pop("summary", None)

    mask_token = getattr(tokenizer, "mask_token", None)
    if not isinstance(mask_token, str) or not mask_token:
        raise LayaSequenceError("tokenizer has no mask_token")
    safe_instructions = normalized["ins"].replace(mask_token, " ")
    head_ids = _encode(tokenizer, f"{normalized['t']} question: {safe_instructions}")
    option_ids = [
        [_token_id(tokenizer, "mask_token_id")]
        + _encode(tokenizer, " " + text.replace(mask_token, " "))
        for text in render_decision_options(normalized)
    ]
    head_payload_tokens = len(head_ids) + sum(len(item) for item in option_ids)
    if head_payload_tokens > head_max_len:
        raise LayaSequenceError(
            f"Laya decision head requires {head_payload_tokens} tokens; "
            f"configured head_max_len is {head_max_len}; shorten upstream option evidence"
        )

    cls_id = _token_id(tokenizer, "cls_token_id")
    sep_id = _token_id(tokenizer, "sep_token_id")
    ids = [cls_id, *head_ids, sep_id]
    markers: list[int] = []
    for option in option_ids:
        markers.append(len(ids))
        ids.extend(option)
    ids.append(sep_id)
    # Stable artifact identifiers are retained in the journal and full state.
    # Rule text and kind carry the meaning; only option IDs need model output.
    for rule in projected.get("guidelines", []):
        rule.pop("id", None)
    for option in projected.get("options", []):
        option.pop("tool", None)
    # Factor shared numeric facts once; differing facts stay with each option.
    # All values remain recoverable, including when options share most geometry.
    option_rows = projected.get("options", [])
    if len(option_rows) > 1:
        common = dict(option_rows[0].get("features", {}))
        for row in option_rows[1:]:
            features = row.get("features", {})
            common = {key: value for key, value in common.items() if key in features and features[key] == value}
        if common:
            projected["shared_option_features"] = common
            for row in option_rows:
                row["features"] = {key: value for key, value in row.get("features", {}).items() if key not in common}
    # Columnar lists preserve every value while avoiding repeated JSON keys.
    # This is part of v2 for both training and serving, never budget-dependent.
    for field in ("guidelines", "history", "options", "goals"):
        rows = projected.get(field)
        if isinstance(rows, list) and rows:
            columns = sorted({key for row in rows for key in row})
            projected[field] = {"columns": columns, "rows": [[row.get(key) for key in columns] for row in rows]}
    state_text = json.dumps(
        projected, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )
    state_ids = _encode(tokenizer, state_text.replace(mask_token, " "))
    required = len(ids) + len(state_ids) + 1
    if required > max_len:
        raise LayaSequenceError(
            f"Laya decision sequence requires {required} tokens; configured max_len is "
            f"{max_len} (head={len(ids)}, state={len(state_ids)}, final_sep=1); "
            "reduce upstream evidence or use a separately trained longer-context profile"
        )
    ids.extend(state_ids)
    ids.append(sep_id)
    return ids, markers


__all__ = [
    "LEGACY_SEQUENCE_VERSION",
    "SEQUENCE_VERSION",
    "LayaSequenceError",
    "build_decision_sequence",
    "normalize_decision_question",
    "project_decision_state",
    "render_decision_options",
]
