# Path: training/laya_dataset.py
# Purpose: Convert safe Factorio transitions into provenance-carrying Laya fine-tuning cases.

from __future__ import annotations

import json
import hashlib
import math
import os
import sqlite3
import copy
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator
from referencing import Registry, Resource

from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import validate_state
from training.laya_workflow import QUESTION_INSTRUCTIONS


class LayaDatasetError(ValueError):
    """Raised when a transition cannot become an auditable Laya case."""


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_NAMES = ("laya_case.schema.json", "laya_state.schema.json")
_SCHEMA_BASE = "https://factorio-agent.local/schemas/"
_SCHEMAS = {
    name: json.loads((_ROOT / "schemas" / name).read_text(encoding="utf-8"))
    for name in _SCHEMA_NAMES
}
for _name, _schema in _SCHEMAS.items():
    _schema["$id"] = _SCHEMA_BASE + _name
_REGISTRY = Registry().with_resources(
    (_SCHEMA_BASE + name, Resource.from_contents(schema))
    for name, schema in _SCHEMAS.items()
)
_CASE_VALIDATOR = Draft7Validator(_SCHEMAS["laya_case.schema.json"], registry=_REGISTRY)
_HASH = "sha256:" + ("0" * 64)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def question_instructions(decision_kind: str) -> str:
    """Return the exact instruction text inference uses for a decision kind.

    Training must see the same question text as ``LayaWorkflowPolicy``; a
    trace-supplied instruction is never used for a known kind.  Staged
    candidate kinds (``select_candidate_stage_N``) share the candidate text.
    """
    kind = str(decision_kind)
    if kind in QUESTION_INSTRUCTIONS:
        return QUESTION_INSTRUCTIONS[kind]
    if kind.startswith("select_candidate"):
        return QUESTION_INSTRUCTIONS["select_candidate"]
    return "Choose the best validated option."


def _bounded_quality(value: Any, *, name: str = "source_quality") -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise LayaDatasetError(f"{name} must be numeric") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise LayaDatasetError(f"{name} must be finite and non-negative")
    return min(1.0, parsed)


def _ref(identifier: str, digest: str | None = None) -> dict[str, str]:
    return {"id": identifier, "version": "1.0.0", "hash": digest or _HASH}


def _state_snapshot_hash(transition: Mapping[str, Any], observation: Mapping[str, Any] | None = None) -> str:
    """Hash the facts available at the decision boundary.

    ``scenario_hash`` identifies the immutable task contract.  It is not an
    identity for the observed world and must never be used as one.  Runners
    may provide a richer ``state_snapshot``; legacy transitions at least have
    the pre-action observation, which is still a real, reproducible snapshot.
    """
    supplied = transition.get("state_snapshot")
    snapshot = supplied if isinstance(supplied, Mapping) else (observation or transition.get("observation") or {})
    declared = transition.get("state_snapshot_hash")
    digest = str(declared or "")
    computed = sha256_hash(dict(snapshot))
    if digest:
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise LayaDatasetError("state_snapshot_hash is not a valid sha256 digest")
        if digest != computed:
            raise LayaDatasetError("state_snapshot_hash does not match the pre-action state snapshot")
    return computed


def _scope(scenario: Mapping[str, Any]) -> dict[str, Any]:
    environment = scenario.get("environment") or {}
    bounds = environment.get("bounds")
    return {
        "surface": str(environment.get("surface_name", "training/unknown")),
        "force": str(environment.get("force_name", "training-unknown")),
        "bounds": dict(bounds) if isinstance(bounds, Mapping) else None,
    }


def _state_from_transition(transition: Mapping[str, Any], scenario: Mapping[str, Any]) -> dict[str, Any]:
    observation = transition.get("observation") or {}
    objective = scenario.get("objective") or {}
    target = objective.get("target_rate_per_tick")
    current = observation.get("delivered_rate_per_tick")
    gap = None if target is None or current is None else max(0.0, float(target) - float(current))
    scope = _scope(scenario)
    candidates = transition.get("candidates") or []
    options = []
    for candidate in candidates[:20]:
        action_id = str(candidate.get("action_id") or candidate.get("option_id") or "")
        if not action_id:
            raise LayaDatasetError("transition candidate has no action_id")
        plan_hash = str(candidate.get("plan_hash", _HASH))
        if not plan_hash.startswith("sha256:"):
            plan_hash = sha256_hash({"candidate": action_id, "plan_hash": plan_hash})
        features = candidate.get("features") or {}
        normalized_features: dict[str, float | None] = {}
        for key, value in features.items():
            if value is None:
                normalized_features[str(key)] = None
            else:
                normalized_features[str(key)] = float(value)
        options.append({
            "option_id": action_id,
            "operation_family": "mine" if scenario.get("family") == "mining_delivery" else "smelt",
            "tool_id": f"training.{scenario.get('family', 'unknown')}",
            "signature": plan_hash,
            "plan_hash": plan_hash,
            "summary": ", ".join(f"{key}={features[key]}" for key in sorted(features)[:8]) or action_id,
            "features": normalized_features,
            "scope": scope,
            "risk_tier": "ghost_submit",
        })
    if not options:
        raise LayaDatasetError("transition has no candidate options")
    state = {
        "version": "1.0.0",
        "run": {
            "run_id": str(transition.get("episode_id", "episode-unknown")),
            "episode_id": str(transition.get("episode_id", "episode-unknown")),
            "tick": int(transition.get("started_tick", 0)),
            "elapsed_ticks": 0,
            "bootstrap_profile": str(scenario.get("bootstrap_profile", "training-v1")),
            "surface": scope["surface"],
            "force": scope["force"],
            "runtime": "disposable_training",
        },
        "objective": {
            "family": str(scenario.get("family", "unknown")),
            "description": str(objective.get("kind", objective.get("item", "training objective"))),
            "target": None if target is None else float(target),
            "unit": "per_tick",
        },
        "goal_stack": [{
            "goal_id": f"goal.{scenario.get('scenario_id', 'unknown')}",
            "kind": "objective",
            "objective": str(objective.get("item", objective.get("kind", "training objective"))),
            "status": "active",
            "gap": gap,
            "age_ticks": 0,
            "retry_count": 0,
            # The terminal result is future information and must never leak
            # into the prompt for this decision.  A runner may explicitly
            # report an already-active failure in the observation/state.
            "last_failure_kind": str(observation.get("last_failure_kind", "none")),
        }],
        "acceptance_gap": {
            "metric": "delivered_rate_per_tick",
            "current": None if current is None else float(current),
            "target": None if target is None else float(target),
            "remaining": gap,
            "unit": "per_tick",
            "sustained_ticks": int(observation.get("sustained_ticks", 0)),
            "required_sustained_ticks": int(objective.get("sustain_ticks", 0)),
        },
        "current_transaction": None,
        "state_digest": {
            "power": {"generation_kw": None, "firm_generation_kw": None, "accumulator_mj": None, "unpowered_consumers": None, "poles": None},
            "logistics": {"robots": None, "providers_available": None, "pending_ghosts": None, "ghost_blockages_top3": []},
            "stock": {"total": None, "accessible": None, "allocated": None, "top_shortages": []},
            "production": {"rate_now": None if current is None else float(current), "rate_target": None if target is None else float(target), "sustained_ticks": int(observation.get("sustained_ticks", 0)), "bottleneck_hint": None},
            "resource": {"remaining": observation.get("resource_remaining"), "patch_bounds_hint": None},
            "space": {"free_area_tiles": None, "reserved_area_tiles": None, "occupied_footprint_tiles": None, "candidate_site_count": len(options)},
        },
        "recent_history": [],
        "active_failures": [],
        "guidelines": [{"rule_id": "rule.training.isolation", "kind": "hard", "statement": "Stay within the declared disposable training scope."}],
        "options": options,
        "provenance": {
            "renderer_version": "laya-transition-adapter-1.0.0",
            "state_snapshot_hash": _state_snapshot_hash(transition, observation),
            "guideline_pack": _ref("pack.training-isolation"),
            "tool_registry": _ref("registry.training-candidates"),
            "model": {"model_id": "dataset.adapter", "model_hash": _HASH},
        },
        "budget": {"materials_remaining": None, "construction_budget_remaining": None, "action_count_remaining": None, "elapsed_ticks_remaining": None},
    }
    validate_state(state)
    return state


def _counterfactual_label(evidence: Any, options: list[str] | None = None) -> str | None:
    """Return an independently evaluated preferred option, if one exists."""
    if isinstance(evidence, Mapping):
        rows = evidence.get("evaluations") or evidence.get("alternatives")
        # An `evaluated: true` assertion is not measured comparison evidence.
        if not isinstance(rows, list) or len(rows) < 2:
            return None
    else:
        rows = evidence
    if isinstance(rows, list):
        if len(rows) < 2 or any(not isinstance(row, Mapping) or not isinstance(row.get("metrics"), Mapping)
                               or not row["metrics"] or not row.get("evidence_hash") or not row.get("initial_state_hash")
                               for row in rows):
            return None
        if len({row["initial_state_hash"] for row in rows}) != 1 or len({row["evidence_hash"] for row in rows}) != len(rows):
            return None
        if len({row.get("option_id", row.get("action_id")) for row in rows}) != len(rows):
            return None
        preferred = [row for row in rows if isinstance(row, Mapping) and row.get("preferred") is True]
        if len(preferred) == 1:
            value = str(preferred[0].get("option_id") or preferred[0].get("action_id") or "").strip()
            if value and (options is None or value in options):
                return value
    return None


def _validated_label(
    chosen: str,
    *,
    failure_kind: str,
    label_source: Any,
    evidence: Any,
    options: list[str] | None = None,
) -> str:
    """Validate that a positive label is supported by causal evidence."""
    source = str(label_source or "").strip().lower()
    if failure_kind == "none":
        return chosen
    if source in {"reviewer", "expert"}:
        return chosen
    if source == "counterfactual":
        label = _counterfactual_label(evidence, options)
        if label:
            return label
    raise LayaDatasetError(
        f"unsupported failed-action label: failure_kind={failure_kind}; "
        "requires reviewer or evaluated counterfactual evidence"
    )


def transition_to_case(
    transition: Mapping[str, Any],
    scenario: Mapping[str, Any],
    *,
    split: str = "train",
    source_quality: float = 1.0,
) -> dict[str, Any]:
    """Convert one safe transition into a single dynamic-choice Laya case."""
    source_quality = _bounded_quality(source_quality)
    result = transition.get("result") or {}
    failure_kind = str(result.get("failure_kind", "none"))
    if failure_kind in {"safety", "identity", "budget", "fixture", "capability", "execution", "power_unconnected"}:
        raise LayaDatasetError(f"unsafe or non-policy transition excluded: {failure_kind}")
    state = _state_from_transition(transition, scenario)
    candidates = transition["candidates"]
    chosen = str(transition["chosen_action_id"])
    ids = [str(candidate["action_id"]) for candidate in candidates]
    if chosen not in ids:
        raise LayaDatasetError("chosen action is absent from candidate set")
    primary_label = _validated_label(
        chosen,
        failure_kind=failure_kind,
        label_source=transition.get("label_source"),
        evidence=transition.get("counterfactual_evidence"),
        options=ids,
    )
    probabilities = {identifier: (1.0 if identifier == primary_label else 0.0) for identifier in ids}
    questions = {
        "select_candidate": {
            "type": "choice",
            "instructions": question_instructions("select_candidate"),
            "criteria": {str(candidate["action_id"]): str(candidate["action_id"]) for candidate in candidates[:20]},
        }
    }
    gold = {
        "select_candidate": {
            "label": primary_label,
            "probabilities": probabilities,
            "label_source": "outcome" if failure_kind == "none" else "counterfactual",
            "confidence": 1.0 if failure_kind == "none" else 0.5,
        }
    }
    question_weights = {"select_candidate": max(0.0, float(source_quality))}
    # Newer workflow runners may attach multiple typed decision boundaries to
    # one measured transition. Preserve those labels without inventing them for
    # legacy mining-only transitions.
    for index, decision in enumerate(transition.get("workflow_decisions") or []):
        if not isinstance(decision, Mapping):
            continue
        decision_kind = str(decision.get("decision_kind", "")).strip()
        options = decision.get("options") or []
        chosen_option = str(decision.get("chosen_option_id", "")).strip()
        if not decision_kind or decision_kind in questions or not isinstance(options, list) or not chosen_option:
            continue
        normalized: list[dict[str, Any]] = []
        for option in options[:20]:
            if not isinstance(option, Mapping):
                continue
            option_id = str(option.get("option_id") or option.get("action_id") or "").strip()
            if not option_id or option_id in {item["option_id"] for item in normalized}:
                continue
            normalized.append({
                "option_id": option_id,
                "summary": str(option.get("summary") or option_id),
            })
        option_ids = [item["option_id"] for item in normalized]
        if not normalized or chosen_option not in option_ids:
            continue
        question_id = decision_kind[:64]
        if question_id in questions:
            question_id = f"{question_id}_{index}"[:64]
        questions[question_id] = {
            "type": "choice",
            "instructions": question_instructions(decision_kind),
            "criteria": {item["option_id"]: item["summary"] for item in normalized},
            "option_ids": option_ids,
            "option_count": len(option_ids),
        }
        confidence = _bounded_quality(decision.get("confidence", 1.0), name="decision confidence")
        decision_source = str(decision.get("label_source") or "").strip()
        decision_label = _validated_label(
            chosen_option,
            failure_kind=failure_kind,
            label_source=decision_source,
            evidence=decision.get("counterfactual_evidence") or transition.get("counterfactual_evidence"),
            options=option_ids,
        )
        gold[question_id] = {
            "label": decision_label,
            "probabilities": {option_id: (1.0 if option_id == decision_label else 0.0) for option_id in option_ids},
            "label_source": "reviewer" if decision_source in {"reviewer", "expert"} else ("counterfactual" if decision_source == "counterfactual" else "outcome"),
            "confidence": confidence,
        }
        question_weights[question_id] = max(0.0, float(source_quality)) * confidence
    source_hash = str(transition.get("scenario_hash", _HASH))
    if not source_hash.startswith("sha256:") or len(source_hash) != 71:
        source_hash = _HASH
    snapshot_hash = _state_snapshot_hash(transition, transition.get("observation") or {})
    case = {
        "version": "1.0.0",
        "case_id": f"{transition['episode_id']}.{transition.get('started_tick', 0)}",
        "source": {
            "kind": "rl_transition",
            "run_id": str(transition["episode_id"]),
            "episode_id": str(transition["episode_id"]),
            "scenario_id": str(transition["scenario_id"]),
            "scenario_seed": int(transition["scenario_seed"]),
            "scenario_hash": source_hash,
            "provenance_hash": snapshot_hash,
        },
        "state": state,
        "questions": questions,
        "gold": gold,
        "weights": {
            "case_weight": max(0.0, float(source_quality)),
            "question_weights": question_weights,
            "source_quality": max(0.0, min(1.0, float(source_quality))),
            "temporal_decay": 1.0,
        },
        "created_at": _now(),
        "dataset_version": "1.0.0",
        "split": split,
        "label_provenance": {
            "method": "direct_outcome" if failure_kind == "none" else "counterfactual",
            "evidence_hash": snapshot_hash,
            "notes": "weak_imitation: observed historical action; no counterfactual superiority claim" if failure_kind == "none" else "evaluated counterfactual or reviewer label",
        },
    }
    errors = sorted(_CASE_VALIDATOR.iter_errors(case), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(error.message for error in errors[:4])
        raise LayaDatasetError(f"generated Laya case failed validation: {detail}")
    return case


_UNSAFE_FAILURES = {
    "safety", "identity", "budget", "fixture", "capability", "execution",
    "power_unconnected",
}
_RISK_TIERS = {
    "read_only", "ghost_submit", "reversible_mutation", "destructive_mutation",
    "human_authorized",
}


def _case_id_part(value: Any, fallback: str) -> str:
    """Return an identifier safe for the versioned case schemas."""
    text = str(value or "").strip()
    normalized = "".join(char if char.isalnum() or char in "._:-" else "-" for char in text)
    return normalized[:128] or fallback


def _option_features(raw: Any) -> dict[str, float | None]:
    if not isinstance(raw, Mapping) or not raw:
        return {"workflow_option": 1.0}
    features: dict[str, float | None] = {}
    for key, value in raw.items():
        name = str(key).strip()
        if not name:
            continue
        if value is None:
            features[name[:64]] = None
        else:
            try:
                features[name[:64]] = float(value)
            except (TypeError, ValueError) as error:
                raise LayaDatasetError(f"option feature {name!r} is not numeric") from error
    return features or {"workflow_option": 1.0}


def _normalize_workflow_options(
    raw_options: Any,
    *,
    scenario: Mapping[str, Any],
    scope: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Normalize a typed decision option list to the ``laya_state`` option shape."""
    if not isinstance(raw_options, list):
        raise LayaDatasetError("workflow decision options must be a list")
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    default_family = str(scenario.get("family") or "observe")
    for index, raw in enumerate(raw_options[:20]):
        if isinstance(raw, str):
            option_id = raw.strip()
            summary = option_id
            source: Mapping[str, Any] = {}
        elif isinstance(raw, Mapping):
            source = raw
            option_id = str(raw.get("option_id") or raw.get("action_id") or raw.get("id") or "").strip()
            summary = str(raw.get("summary") or raw.get("label") or option_id).strip()
        else:
            raise LayaDatasetError(f"workflow option {index} must be an object or string")
        if not option_id:
            raise LayaDatasetError(f"workflow option {index} has no option_id")
        if option_id in seen:
            raise LayaDatasetError(f"duplicate workflow option: {option_id}")
        if not summary:
            raise LayaDatasetError(f"workflow option {option_id!r} has no summary")
        plan_hash = str(source.get("plan_hash") or source.get("signature") or "")
        if not plan_hash.startswith("sha256:") or len(plan_hash) != 71:
            plan_hash = sha256_hash({"option_id": option_id, "summary": summary, "source": dict(source)})
        signature = str(source.get("signature") or plan_hash)
        if not signature.startswith("sha256:") or len(signature) != 71:
            signature = sha256_hash({"option_id": option_id, "summary": summary, "signature": signature})
        operation_family = _case_id_part(source.get("operation_family") or default_family, "observe")
        tool_id = _case_id_part(source.get("tool_id") or f"training.{operation_family}", "training.observe")
        risk_tier = str(source.get("risk_tier") or "ghost_submit")
        if risk_tier not in _RISK_TIERS:
            risk_tier = "ghost_submit"
        option_scope = dict(scope)
        if isinstance(source.get("scope"), Mapping):
            # The scenario scope is authoritative.  A trace may provide a
            # narrower scope, but must never widen the training boundary.
            candidate_scope = source["scope"]
            if candidate_scope.get("surface") == scope.get("surface") and candidate_scope.get("force") == scope.get("force"):
                option_scope = dict(scope)
        options.append({
            "option_id": _case_id_part(option_id, f"workflow-option-{index + 1}"),
            "operation_family": operation_family,
            "tool_id": tool_id,
            "signature": signature,
            "plan_hash": plan_hash,
            "summary": summary[:2048],
            "features": _option_features(source.get("features")),
            "scope": option_scope,
            "risk_tier": risk_tier,
        })
        seen.add(option_id)
    if not options:
        raise LayaDatasetError("workflow decision options are empty")
    return options


def _observation_map(raw: Any, fallback: Mapping[str, Any]) -> dict[str, float]:
    """Keep only numeric observation facts while preserving absent values."""
    source = raw if isinstance(raw, Mapping) else fallback
    result: dict[str, float] = {}
    for key, value in source.items():
        if isinstance(value, bool):
            result[str(key)] = float(value)
        elif isinstance(value, (int, float)):
            result[str(key)] = float(value)
    return result


def _label_method(label_source: Any, failure_kind: str) -> str:
    source = str(label_source or "").strip()
    if source in {"counterfactual", "hindsight"}:
        return "counterfactual"
    if source in {"expert", "reviewer"}:
        return "reviewer"
    return "direct_outcome" if failure_kind == "none" else "hindsight"


def _workflow_steps(
    transition: Mapping[str, Any],
    scenario: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Collect staged and typed workflow decisions in execution order.

    ``stage_action_trail`` and ``workflow_decisions`` intentionally overlap
    for staged candidate choices.  The staged trace is richer, so a matching
    typed decision enriches it rather than producing a duplicate case.
    """
    fallback_pre = transition.get("observation") or {}
    fallback_post = transition.get("next_observation") or fallback_pre
    scope = _scope(scenario)
    typed = [item for item in (transition.get("workflow_decisions") or []) if isinstance(item, Mapping)]
    typed_by_kind: dict[str, Mapping[str, Any]] = {}
    for item in typed:
        kind = str(item.get("decision_kind") or "").strip()
        if kind and kind not in typed_by_kind:
            typed_by_kind[kind] = item
    steps: list[dict[str, Any]] = []
    matched: set[int] = set()
    trail = transition.get("stage_action_trail") or []
    if isinstance(trail, list):
        for stage_index, item in enumerate(trail):
            if not isinstance(item, Mapping):
                continue
            try:
                actual_stage = int(item.get("stage_index", stage_index + 1))
            except (TypeError, ValueError):
                actual_stage = stage_index + 1
            kind = "select_candidate" if actual_stage == 1 else f"select_candidate_stage_{actual_stage}"
            metadata = typed_by_kind.get(kind)
            if metadata is None and actual_stage == 1:
                metadata = typed_by_kind.get("select_candidate")
            if metadata is not None:
                matched.add(id(metadata))
            selected = item.get("selected_candidate") or {}
            raw_options = item.get("available_candidates") or [selected]
            chosen = str(selected.get("action_id") or selected.get("option_id") or "").strip()
            if not chosen:
                continue
            steps.append({
                "decision_kind": kind,
                "step_hint": max(0, actual_stage - 1),
                "observed_tick": int(item.get("observed_tick", transition.get("started_tick", 0))),
                "pre_observation": _observation_map(item.get("pre_action_observation"), fallback_pre),
                "post_observation": _observation_map(item.get("post_action_observation"), fallback_post),
                "options": raw_options,
                "chosen_option_id": chosen,
                "executed_action": selected,
                "execution": copy.deepcopy(item.get("execution") or {}),
                "instructions": (metadata or {}).get("instructions") if metadata else None,
                "label_source": (metadata or {}).get("label_source") if metadata else None,
                "confidence": (metadata or {}).get("confidence", 1.0) if metadata else 1.0,
            })
    # If no stage-1 trace exists, preserve the transition's primary decision.
    if not any(step["decision_kind"] == "select_candidate" for step in steps):
        steps.insert(0, {
            "decision_kind": "select_candidate",
            "step_hint": 0,
            "observed_tick": int(transition.get("started_tick", 0)),
            "pre_observation": _observation_map(fallback_pre, fallback_pre),
            "post_observation": _observation_map(fallback_post, fallback_post),
            "options": transition.get("candidates") or [],
            "chosen_option_id": str(transition.get("chosen_action_id") or ""),
            "executed_action": transition.get("chosen_action") or {"action_id": transition.get("chosen_action_id")},
            "execution": copy.deepcopy(transition.get("execution") or {}),
            "instructions": None,
            "label_source": None,
            "confidence": 1.0,
        })
    for item in typed:
        if id(item) in matched:
            continue
        decision_kind = str(item.get("decision_kind") or "").strip()
        chosen = str(item.get("chosen_option_id") or "").strip()
        if not decision_kind or not chosen:
            continue
        steps.append({
            "decision_kind": decision_kind,
            "step_hint": len(steps),
            "observed_tick": int(item.get("tick", transition.get("started_tick", 0))),
            "pre_observation": _observation_map(item.get("pre_observation"), fallback_pre),
            "post_observation": _observation_map(item.get("post_observation") or item.get("next_observation"), fallback_post),
            "options": item.get("options") or [],
            "chosen_option_id": chosen,
            "executed_action": item.get("executed_action") or {"option_id": chosen},
            "execution": copy.deepcopy(item.get("execution") or item.get("execution_report") or {}),
            "instructions": item.get("instructions"),
            "label_source": item.get("label_source"),
            "confidence": item.get("confidence", 1.0),
        })
    if not steps:
        raise LayaDatasetError("transition contains no auditable decisions")
    return steps


def _step_case(
    transition: Mapping[str, Any],
    scenario: Mapping[str, Any],
    step: Mapping[str, Any],
    *,
    trajectory_id: str,
    step_index: int,
    step_count: int,
    split: str,
    source_quality: float,
    source_hash: str,
) -> dict[str, Any]:
    source_quality = _bounded_quality(source_quality)
    result = dict(transition.get("result") or {})
    failure_kind = str(result.get("failure_kind") or "none")
    options = _normalize_workflow_options(step.get("options"), scenario=scenario, scope=_scope(scenario))
    chosen = _case_id_part(step.get("chosen_option_id"), "")
    option_ids = [option["option_id"] for option in options]
    if not chosen or chosen not in option_ids:
        raise LayaDatasetError("workflow chosen option is absent from candidate set")
    decision_kind = _case_id_part(step.get("decision_kind"), "select_candidate").lower()
    if not decision_kind[0].isalpha():
        decision_kind = f"decision-{decision_kind}"
    decision_kind = decision_kind[:64]
    pre = dict(step.get("pre_observation") or {})
    post = dict(step.get("post_observation") or {})
    state_transition = dict(transition)
    state_transition["observation"] = pre
    state_transition["next_observation"] = post
    state_transition["state_snapshot"] = pre
    state_transition["candidates"] = options
    state_transition["chosen_action_id"] = chosen
    state_transition["started_tick"] = int(step.get("observed_tick", transition.get("started_tick", 0)))
    state = _state_from_transition(state_transition, scenario)
    state["options"] = options
    state["run"]["tick"] = max(0, int(step.get("observed_tick", state["run"]["tick"])))
    state["state_digest"]["space"]["candidate_site_count"] = len(options)
    validate_state(state)
    executed = step.get("executed_action")
    if isinstance(executed, Mapping):
        executed_id = str(executed.get("action_id") or executed.get("option_id") or chosen)
        executed_snapshot = copy.deepcopy(dict(executed))
    else:
        executed_id = str(executed or chosen)
        executed_snapshot = {"option_id": executed_id}
    execution = copy.deepcopy(step.get("execution") or {})
    step_failure = execution.get("failure_kind")
    if not step_failure and isinstance(execution.get("failure"), Mapping):
        step_failure = execution["failure"].get("kind")
    effective_failure = str(step_failure or failure_kind)
    trace = {
        "trajectory_id": trajectory_id,
        "step_index": step_index,
        "step_count": step_count,
        "decision_kind": decision_kind,
        "pre_observation": pre,
        "post_observation": post,
        "options": copy.deepcopy(options),
        "chosen_action": {"option_id": chosen, "option": copy.deepcopy(next(option for option in options if option["option_id"] == chosen))},
        "executed_action": {"option_id": executed_id, "action": executed_snapshot, "execution": execution},
        "outcome": {"result": copy.deepcopy(result), "execution": execution},
        "outcome_ref": {"trajectory_id": trajectory_id, "step_index": step_index, "source_hash": source_hash},
    }
    evidence_hash = sha256_hash(trace)
    try:
        confidence = max(0.0, min(1.0, float(step.get("confidence", 1.0))))
    except (TypeError, ValueError):
        raise LayaDatasetError("workflow decision confidence must be numeric")
    if not math.isfinite(confidence) or confidence < 0:
        raise LayaDatasetError("workflow decision confidence must be finite and non-negative")
    confidence = min(1.0, confidence)
    questions = {
        decision_kind: {
            "type": "choice",
            "instructions": question_instructions(decision_kind),
            "criteria": {option["option_id"]: option["summary"] for option in options},
            "option_ids": option_ids,
            "option_count": len(option_ids),
        }
    }
    label_source = str(step.get("label_source") or "").strip()
    method = _label_method(label_source, effective_failure)
    label = _validated_label(
        chosen,
        failure_kind=effective_failure,
        label_source=label_source,
        evidence=step.get("counterfactual_evidence") or transition.get("counterfactual_evidence"),
        options=option_ids,
    )
    snapshot_hash = _state_snapshot_hash(state_transition, pre)
    source = {
        "kind": "rl_transition",
        "run_id": _case_id_part(transition.get("episode_id"), "episode-unknown"),
        "episode_id": _case_id_part(transition.get("episode_id"), "episode-unknown"),
        "trajectory_id": trajectory_id,
        "step_index": step_index,
        "decision_kind": decision_kind,
        "scenario_id": _case_id_part(transition.get("scenario_id"), "scenario-unknown"),
        "scenario_seed": int(transition.get("scenario_seed", 0)),
        "scenario_hash": source_hash,
        "provenance_hash": snapshot_hash,
    }
    case = {
        "version": "1.0.0",
        "case_id": f"{trajectory_id}.{step_index}.{decision_kind}",
        "source": source,
        "state": state,
        "questions": questions,
        "gold": {
            decision_kind: {
                "label": label,
                "probabilities": {option_id: (1.0 if option_id == label else 0.0) for option_id in option_ids},
                "label_source": "counterfactual" if method == "counterfactual" else ("reviewer" if method == "reviewer" else "outcome"),
                "confidence": confidence,
            }
        },
        "weights": {
            "case_weight": max(0.0, float(source_quality)),
            "question_weights": {decision_kind: max(0.0, float(source_quality)) * confidence},
            "source_quality": max(0.0, min(1.0, float(source_quality))),
            "temporal_decay": 1.0,
        },
        "decision_trace": trace,
        "created_at": _now(),
        "dataset_version": "1.0.0",
        "split": split,
        "label_provenance": {
            "method": method,
            "evidence_hash": evidence_hash,
            "notes": (
                f"trajectory={trajectory_id};step={step_index};outcome={effective_failure};"
                + ("weak_imitation" if effective_failure == "none" else "causal_evidence_required")
            ),
        },
    }
    errors = sorted(_CASE_VALIDATOR.iter_errors(case), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(error.message for error in errors[:4])
        raise LayaDatasetError(f"generated step Laya case failed validation: {detail}")
    return case


def transition_to_step_cases(
    transition: Mapping[str, Any],
    scenario: Mapping[str, Any],
    *,
    split: str = "train",
    source_quality: float = 1.0,
) -> list[dict[str, Any]]:
    """Split one trajectory into one provenance-rich case per decision.

    The legacy :func:`transition_to_case` keeps its grouped-question shape.
    This conversion is the step-level path: staged actions and typed workflow
    decisions become independent cases linked by ``source.trajectory_id`` and
    ``decision_trace.outcome_ref``.
    """
    if split not in {"train", "calibration", "held_out", "test"}:
        raise LayaDatasetError(f"unknown dataset split: {split}")
    result = transition.get("result") or {}
    failure_kind = result.get("failure_kind", "none")
    if failure_kind in _UNSAFE_FAILURES:
        raise LayaDatasetError(f"unsafe or non-policy transition excluded: {failure_kind}")
    source_hash = str(transition.get("scenario_hash", _HASH))
    if not source_hash.startswith("sha256:") or len(source_hash) != 71:
        source_hash = _HASH
    trajectory_id = _case_id_part(transition.get("trajectory_id") or transition.get("episode_id"), "trajectory-unknown")
    steps = _workflow_steps(transition, scenario)
    cases: list[dict[str, Any]] = []
    for index, step in enumerate(steps):
        cases.append(_step_case(
            transition, scenario, step,
            trajectory_id=trajectory_id,
            step_index=index,
            step_count=len(steps),
            split=split,
            source_quality=source_quality,
            source_hash=source_hash,
        ))
    return cases


# Explicit aliases make the conversion discoverable for callers that refer to
# a trajectory rather than the TrainingStore's transition terminology.
trajectory_to_cases = transition_to_step_cases
transition_to_cases = transition_to_step_cases


def seed_split(seed: int) -> str:
    """Derive a stable split from scenario seed when a store has only ``train`` rows."""
    bucket = int.from_bytes(hashlib.sha256(str(int(seed)).encode("ascii")).digest()[:2], "big") % 100
    if bucket < 10:
        return "calibration"
    if bucket < 20:
        return "held_out"
    if bucket < 25:
        return "test"
    return "train"


def iter_cases_from_store(
    path: Path | str,
    *,
    split: str | None = None,
    derive_seed_splits: bool = False,
    step_level: bool = False,
    quality_report: dict[str, Any] | None = None,
) -> Iterable[dict[str, Any]]:
    """Stream policy-usable cases from an existing TrainingStore database.

    ``step_level`` opts into one case per staged/typed decision.  The default
    remains the historical grouped transition shape for existing datasets.
    """
    report = quality_report if quality_report is not None else {}
    report.setdefault("rows", 0)
    report.setdefault("emitted", 0)
    report.setdefault("excluded", 0)
    report.setdefault("exclusions", {})
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            "SELECT transitions.payload_json, scenarios.payload_json AS scenario_json, scenarios.split "
            "FROM transitions JOIN episodes USING(episode_id) JOIN scenarios USING(scenario_id) "
            "ORDER BY transitions.episode_id, transitions.step_index"
        )
        for row in rows:
            report["rows"] += 1
            transition = json.loads(row["payload_json"])
            scenario = json.loads(row["scenario_json"])
            declared_split = row["split"]
            row_split = seed_split(transition["scenario_seed"]) if derive_seed_splits else declared_split
            if split is not None and row_split != split:
                continue
            try:
                if step_level:
                    generated = transition_to_step_cases(transition, scenario, split=row_split)
                else:
                    generated = [transition_to_case(transition, scenario, split=row_split)]
            except (KeyError, TypeError, ValueError, LayaDatasetError) as error:
                report["excluded"] += 1
                reason = str(error).split(":", 1)[0][:120] or type(error).__name__
                exclusions = report["exclusions"]
                exclusions[reason] = int(exclusions.get(reason, 0)) + 1
                continue
            report["emitted"] += len(generated)
            yield from generated
    finally:
        connection.close()


def _validate_case_targets(case: Mapping[str, Any]) -> None:
    """Require every gold distribution to be a usable target over real options.

    Explicit soft targets (for example outcome-weighted ``serve_goal`` choices
    from exploration journals) stay as distributions; only their support and
    mass are checked here.
    """
    questions = case.get("questions") or {}
    for question_id, gold in (case.get("gold") or {}).items():
        question = questions.get(question_id)
        if not isinstance(question, Mapping):
            raise LayaDatasetError(f"gold answer {question_id!r} has no question")
        probabilities = gold.get("probabilities") if isinstance(gold, Mapping) else None
        if probabilities is None:
            continue
        criteria = question.get("criteria")
        if question.get("type") == "choice":
            allowed = set(criteria) if isinstance(criteria, Mapping) else set()
            unknown = set(probabilities) - allowed
            if unknown:
                raise LayaDatasetError(f"gold target {question_id!r} names unknown options: {sorted(unknown)[:3]}")
        values = [float(value) for value in probabilities.values()]
        if any(not math.isfinite(value) or value < 0 for value in values) or sum(values) <= 0:
            raise LayaDatasetError(f"gold target {question_id!r} is not a probability distribution")


def iter_cases_from_jsonl(
    path: Path | str,
    *,
    split: str | None = None,
    quality_report: dict[str, Any] | None = None,
    seen_state_hashes: set[str] | None = None,
    required_sampling_version: str | None = None,
) -> Iterable[dict[str, Any]]:
    """Validate and pass through pre-built Laya cases (e.g. exploration journals).

    Cases are schema-validated, deduplicated by (state hash, question ids),
    and re-split from ``source.scenario_seed`` so one scenario never spans
    splits.  Soft ``gold`` distributions are preserved verbatim.
    """
    report = quality_report if quality_report is not None else {}
    for key in ("rows", "emitted", "excluded"):
        report.setdefault(key, 0)
    report.setdefault("exclusions", {})
    seen = seen_state_hashes if seen_state_hashes is not None else set()

    def exclude(reason: str) -> None:
        report["excluded"] += 1
        report["exclusions"][reason] = int(report["exclusions"].get(reason, 0)) + 1

    from training.laya_counterfactual import needs_outcome_relabel

    path = Path(path)
    corrected = path.parent / "relabel-v4" / path.name
    # Old snapshot drivers pass original filenames to the current ETL. Prefer
    # a separately verified export without rewriting their source evidence.
    if corrected.is_file():
        path = corrected

    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            report["rows"] += 1
            try:
                case = json.loads(line)
            except json.JSONDecodeError:
                exclude("invalid JSON")
                continue
            if not isinstance(case, dict):
                exclude("case is not an object")
                continue
            if needs_outcome_relabel(case):
                exclude("legacy counterfactual case requires outcome relabelling")
                continue
            provenance = case.get('label_provenance')
            if required_sampling_version is not None and (
                not isinstance(provenance, Mapping)
                or provenance.get('sampling_version') != required_sampling_version
            ):
                exclude('case requires current sampling evidence')
                continue
            seed = (case.get("source") or {}).get("scenario_seed") if isinstance(case.get("source"), Mapping) else None
            if isinstance(seed, bool) or not isinstance(seed, int):
                exclude("case has no scenario_seed for split assignment")
                continue
            case["split"] = seed_split(seed)
            errors = sorted(_CASE_VALIDATOR.iter_errors(case), key=lambda error: list(error.path))
            if errors:
                exclude("schema: " + errors[0].message[:100])
                continue
            try:
                _validate_case_targets(case)
            except (LayaDatasetError, TypeError, ValueError) as error:
                exclude(str(error).split(":", 1)[0][:120])
                continue
            if split is not None and case["split"] != split:
                continue
            key = sha256_hash({"state": case["state"], "questions": sorted(case["questions"])})
            if key in seen:
                exclude("duplicate state")
                continue
            seen.add(key)
            report["emitted"] += 1
            yield case


def write_cases(cases: Iterable[Mapping[str, Any]], output: Path | str) -> int:
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, sort_keys=True, separators=(",", ":"), allow_nan=False))
            handle.write("\n")
            count += 1
    return count


DEFAULT_DATASET_DIR = _ROOT / "data" / "laya-datasets"
LATEST_DATASET_NAME = "latest.jsonl"


def file_sha256(path: Path | str) -> str:
    """Return the hex SHA-256 of a dataset file."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_cases_content_addressed(
    cases: Iterable[Mapping[str, Any]],
    directory: Path | str = DEFAULT_DATASET_DIR,
) -> tuple[Path, str, int]:
    """Write cases to ``<directory>/<sha-prefix>.jsonl`` and repoint ``latest.jsonl``.

    Durable, content-addressed datasets survive reboots and let a resumed
    fine-tune prove it is reading the exact bytes it started with.
    """
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / f".building-{os.getpid()}.jsonl"
    try:
        count = write_cases(cases, temporary)
        digest = file_sha256(temporary)
        final = root / f"{digest[:16]}.jsonl"
        temporary.replace(final)
    finally:
        temporary.unlink(missing_ok=True)
    latest = root / LATEST_DATASET_NAME
    link = root / f".latest-{os.getpid()}"
    link.unlink(missing_ok=True)
    link.symlink_to(final.name)
    link.replace(latest)
    return final, digest, count


__all__ = [
    "DEFAULT_DATASET_DIR",
    "LayaDatasetError",
    "file_sha256",
    "iter_cases_from_jsonl",
    "iter_cases_from_store",
    "question_instructions",
    "seed_split",
    "trajectory_to_cases",
    "transition_to_case",
    "transition_to_cases",
    "transition_to_step_cases",
    "write_cases",
    "write_cases_content_addressed",
]
