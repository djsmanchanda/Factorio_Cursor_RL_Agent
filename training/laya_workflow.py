# Path: training/laya_workflow.py
# Purpose: Normalize full-loop Laya decision traces and human coaching records.

from __future__ import annotations

import math

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft7Validator

from orchestrator.laya_operations import sha256_hash
from orchestrator.laya_state import (
    DEFAULT_BUDGET_PROFILE,
    LayaBudgetProfileLike,
    get_budget_profile,
    validate_state,
)


class LayaWorkflowError(ValueError):
    """Raised when a workflow decision cannot become auditable training data."""


ROOT = Path(__file__).resolve().parents[1]
COACH_SCHEMA = json.loads(
    (ROOT / "schemas" / "laya_coach_record.schema.json").read_text(encoding="utf-8")
)
COACH_VALIDATOR = Draft7Validator(COACH_SCHEMA)

DECISION_KINDS: tuple[str, ...] = (
    "serve_goal",
    "choose_operation",
    "select_site",
    "select_scale",
    "select_route",
    "select_research",
    "select_upgrade",
    "recovery",
    "wait_or_act",
    "select_candidate",
)

QUESTION_INSTRUCTIONS = {
    "serve_goal": "Choose the highest-priority goal to serve next.",
    "choose_operation": "Choose the next validated operation family.",
    "select_site": "Choose the legal site for the selected operation.",
    "select_scale": "Choose the scale or expansion size for the operation.",
    "select_route": "Choose the route and topology for the operation.",
    "select_research": "Choose the next research target for the current goals.",
    "select_upgrade": "Choose whether and how to upgrade existing capacity.",
    "recovery": "Choose the best recovery action for the active failure.",
    "wait_or_act": "Choose whether to wait for the current transaction or act again.",
    "select_candidate": "Choose the next validated operation candidate.",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: Mapping[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _id(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if not text:
        text = fallback
    # Keep identifiers compatible with the Laya schemas and Factorio filenames.
    normalized = "".join(char if char.isalnum() or char in "._:-" else "-" for char in text)
    return normalized[:128] or fallback


def _scope(record: Mapping[str, Any]) -> dict[str, Any]:
    source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
    return {
        "surface": str(source.get("surface") or "training/coach"),
        "force": str(source.get("force") or "training-coach"),
        "bounds": source.get("bounds") if isinstance(source.get("bounds"), Mapping) else None,
    }


def _normalize_options(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_options = record.get("options")
    if not isinstance(raw_options, Sequence) or isinstance(raw_options, (str, bytes)):
        raise LayaWorkflowError("coach record must include a non-empty options array")
    options: list[dict[str, Any]] = []
    seen: set[str] = set()
    default_scope = _scope(record)
    for index, raw in enumerate(raw_options):
        if isinstance(raw, str):
            option_id, summary = raw.strip(), raw.strip()
            operation_family = "observe"
            features: dict[str, float | None] = {"human_label": 1.0}
            tool_id = "coach.human_label"
        elif isinstance(raw, Mapping):
            option_id = str(raw.get("option_id") or raw.get("id") or "").strip()
            summary = str(raw.get("summary") or raw.get("label") or option_id).strip()
            operation_family = str(raw.get("operation_family") or "observe").strip()
            tool_id = str(raw.get("tool_id") or f"coach.{operation_family}").strip()
            source_features = raw.get("features") or {"human_label": 1.0}
            if not isinstance(source_features, Mapping):
                raise LayaWorkflowError(f"option {index} features must be an object")
            features = {}
            for key, value in source_features.items():
                if not isinstance(key, str) or not key:
                    raise LayaWorkflowError("option feature names must be non-empty strings")
                if value is not None and not isinstance(value, (int, float)):
                    raise LayaWorkflowError(f"option {option_id!r} feature {key!r} is not numeric")
                features[key] = None if value is None else float(value)
        else:
            raise LayaWorkflowError(f"option {index} must be a string or object")
        option_id = _id(option_id, f"coach.option-{index + 1}")
        if option_id in seen:
            raise LayaWorkflowError(f"duplicate coach option: {option_id}")
        if not summary:
            raise LayaWorkflowError(f"coach option {option_id!r} has no summary")
        raw_scope = raw.get("scope") if isinstance(raw, Mapping) else None
        scope = {
            "surface": str((raw_scope or {}).get("surface") or default_scope["surface"]),
            "force": str((raw_scope or {}).get("force") or default_scope["force"]),
            "bounds": (raw_scope or {}).get("bounds", default_scope["bounds"]),
        }
        risk_tier = str(
            (raw.get("risk_tier") if isinstance(raw, Mapping) else None) or "read_only"
        )
        identity = {
            "option_id": option_id,
            "operation_family": operation_family,
            "tool_id": tool_id,
            "scope": scope,
            "summary": summary,
            "features": features,
        }
        options.append({
            "option_id": option_id,
            "operation_family": operation_family,
            "tool_id": tool_id,
            "signature": str((raw.get("signature") if isinstance(raw, Mapping) else None) or sha256_hash(identity)),
            "plan_hash": (raw.get("plan_hash") if isinstance(raw, Mapping) else None),
            "summary": summary[:512],
            "features": features,
            "scope": dict(scope),
            "risk_tier": risk_tier,
        })
        seen.add(option_id)
    if not options:
        raise LayaWorkflowError("coach record options array is empty")
    return options[:20]


def _default_state(record: Mapping[str, Any], options: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
    guidance = record.get("guidance") if isinstance(record.get("guidance"), Mapping) else {}
    decision_kind = _id(record.get("decision_kind"), "observe")
    run_id = _id(source.get("run_id") or record.get("record_id"), "coach-run")
    episode_id = _id(source.get("episode_id") or run_id, run_id)
    tick = int(source.get("tick") or 0)
    runtime = str(source.get("runtime") or "disposable_training")
    if runtime not in {"disposable_training", "real_nauvis", "legacy_retired"}:
        runtime = "disposable_training"
    scope = _scope(record)
    instruction = str(guidance.get("instruction") or guidance.get("goal") or decision_kind)
    goal = {
        "goal_id": _id(guidance.get("goal_id"), f"goal.{decision_kind}"),
        "kind": decision_kind,
        "objective": instruction[:512],
        "status": "active",
        "gap": None,
        "age_ticks": 0,
        "retry_count": 0,
        "last_failure_kind": "none",
    }
    state = {
        "version": "1.0.0",
        "run": {
            "run_id": run_id,
            "episode_id": episode_id,
            "tick": max(0, tick),
            "elapsed_ticks": max(0, int(source.get("elapsed_ticks") or 0)),
            "bootstrap_profile": str(source.get("bootstrap_profile") or "coach-capture"),
            "surface": scope["surface"],
            "force": scope["force"],
            "runtime": runtime,
        },
        "objective": {
            "family": decision_kind,
            "description": instruction[:512],
            "target": None,
            "unit": "human_guidance",
        },
        "goal_stack": [goal],
        "acceptance_gap": {
            "metric": "human_guidance",
            "current": None,
            "target": None,
            "remaining": None,
            "unit": "human_guidance",
            "sustained_ticks": 0,
            "required_sustained_ticks": 0,
        },
        "current_transaction": None,
        "state_digest": {
            "power": {"generation_kw": None, "firm_generation_kw": None, "accumulator_mj": None, "unpowered_consumers": None, "poles": None},
            "logistics": {"robots": None, "providers_available": None, "pending_ghosts": None, "ghost_blockages_top3": []},
            "stock": {"total": None, "accessible": None, "allocated": None, "top_shortages": []},
            "production": {"rate_now": None, "rate_target": None, "sustained_ticks": None, "bottleneck_hint": None},
            "resource": {"remaining": None, "patch_bounds_hint": None},
            "space": {"free_area_tiles": None, "reserved_area_tiles": None, "occupied_footprint_tiles": None, "candidate_site_count": len(options)},
        },
        "recent_history": [],
        "active_failures": [],
        "guidelines": [
            {"rule_id": "rule.coach.capture", "kind": "soft", "statement": instruction[:512]},
            {"rule_id": "rule.runtime.isolation", "kind": "hard", "statement": "Respect the declared runtime and validation boundary."},
        ],
        "options": [dict(option) for option in options],
        "provenance": {
            "renderer_version": "laya-coach-adapter-1.0.0",
            "state_snapshot_hash": _hash({"record_id": record.get("record_id"), "source": source}),
            "guideline_pack": {"id": "pack.human-coach", "version": "1.0.0", "hash": _hash({"focus": guidance.get("focus", "general")})},
            "tool_registry": {"id": "registry.coach", "version": "1.0.0", "hash": _hash({"options": [option["option_id"] for option in options]})},
            "model": {"model_id": "human-coach", "model_hash": _hash({"record_id": record.get("record_id")})},
        },
        "budget": {"materials_remaining": None, "construction_budget_remaining": None, "action_count_remaining": None, "elapsed_ticks_remaining": None},
    }
    observation = record.get("observation")
    if isinstance(observation, Mapping):
        state["observation_digest"] = _observation_digest(observation)
    return state


def _observation_digest(observation: Mapping[str, Any]) -> dict[str, Any]:
    """Keep useful coach observations while bounding model context and storage."""
    entities = observation.get("entities")
    compact_entities: list[dict[str, Any]] = []
    if isinstance(entities, Sequence) and not isinstance(entities, (str, bytes)):
        for entity in entities[:16]:
            if not isinstance(entity, Mapping):
                continue
            compact_entities.append({
                "name": str(entity.get("name", ""))[:96],
                "type": str(entity.get("type", ""))[:64],
                "position": entity.get("position"),
            })
    inventory = observation.get("inventory")
    compact_inventory = {}
    if isinstance(inventory, Mapping):
        for name, count in list(inventory.items())[:64]:
            if isinstance(name, str) and isinstance(count, (int, float)):
                compact_inventory[name[:96]] = float(count)
    counts = observation.get("entity_counts_sampled")
    compact_counts = {
        str(name)[:96]: int(value)
        for name, value in (counts.items() if isinstance(counts, Mapping) else ())
        if isinstance(name, str) and isinstance(value, (int, float))
    }
    research = observation.get("research") if isinstance(observation.get("research"), Mapping) else {}
    queue = research.get("queue") if isinstance(research.get("queue"), Sequence) and not isinstance(research.get("queue"), (str, bytes)) else []
    return {
        "tick": max(0, int(observation.get("tick") or 0)),
        "surface": str(observation.get("surface") or "")[:128],
        "force": str(observation.get("force") or "")[:128],
        "player_position": observation.get("player_position"),
        "entity_counts": compact_counts,
        "entities": compact_entities,
        "inventory": compact_inventory,
        "research": {
            "current": research.get("current"),
            "queue": [str(item)[:96] for item in queue[:32]],
        },
    }


def validate_coach_record(record: Mapping[str, Any]) -> None:
    errors = sorted(COACH_VALIDATOR.iter_errors(dict(record)), key=lambda error: list(error.path))
    if errors:
        detail = "; ".join(f"{'/'.join(map(str, error.path)) or '<root>'}: {error.message}" for error in errors[:4])
        raise LayaWorkflowError(f"invalid Laya coach record: {detail}")


def coach_record_to_case(record: Mapping[str, Any], *, split: str = "train") -> dict[str, Any]:
    """Convert one in-game coaching record to a typed, auditable Laya case.

    Guidance without an explicit option list and selected label is retained as
    human context but is deliberately not converted into a fabricated target.
    """
    validate_coach_record(record)
    if split not in {"train", "calibration", "held_out", "test"}:
        raise LayaWorkflowError(f"unknown dataset split: {split}")
    options = _normalize_options(record)
    decision_kind = str(record["decision_kind"])
    chosen = str(record.get("chosen_option_id") or "").strip()
    if not chosen:
        raise LayaWorkflowError("coach record has no chosen_option_id; guidance-only records are not labels")
    chosen = _id(chosen, chosen)
    option_ids = [str(option["option_id"]) for option in options]
    if chosen not in option_ids:
        raise LayaWorkflowError("chosen_option_id is not present in options")
    state = copy.deepcopy(record.get("state")) if isinstance(record.get("state"), Mapping) else _default_state(record, options)
    state["options"] = [dict(option) for option in options]
    if isinstance(record.get("observation"), Mapping):
        state["observation_digest"] = _observation_digest(record["observation"])
    validate_state(state)
    guidance = record.get("guidance") if isinstance(record.get("guidance"), Mapping) else {}
    confidence = guidance.get("confidence", record.get("confidence", 1.0))
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 1.0
    source = record.get("source") if isinstance(record.get("source"), Mapping) else {}
    evidence_hash = str(record.get("evidence_hash") or _hash({"record_id": record.get("record_id"), "state": state}))
    return {
        "version": "1.0.0",
        "case_id": _id(record.get("record_id"), f"coach-{decision_kind}"),
        "source": {
            "kind": "human_coach",
            "run_id": _id(source.get("run_id") or record.get("record_id"), "coach-run"),
            "episode_id": _id(source.get("episode_id") or source.get("run_id") or record.get("record_id"), "coach-episode"),
            "trajectory_id": _id(source.get("trajectory_id") or record.get("record_id"), "coach-trajectory"),
            "scenario_id": _id(source.get("scenario_id") or "coach-capture", "coach-capture"),
            "scenario_seed": max(0, int(source.get("seed") or 0)),
            "scenario_hash": str(source.get("scenario_hash") or _hash({"record_id": record.get("record_id")})),
            "provenance_hash": evidence_hash,
        },
        "state": state,
        "questions": {
            decision_kind: {
                "type": "choice",
                "instructions": QUESTION_INSTRUCTIONS.get(decision_kind, "Choose the best validated option."),
                "criteria": {option["option_id"]: option["summary"] for option in options},
                "option_ids": option_ids,
                "option_count": len(option_ids),
            }
        },
        "gold": {
            decision_kind: {
                "label": chosen,
                "probabilities": {option_id: 1.0 if option_id == chosen else 0.0 for option_id in option_ids},
                "label_source": "reviewer",
                "confidence": confidence,
            }
        },
        "weights": {"case_weight": confidence, "question_weights": {decision_kind: confidence}, "source_quality": confidence, "temporal_decay": 1.0},
        "created_at": str(record.get("captured_at") or _now()),
        "dataset_version": "1.0.0",
        "split": split,
        "label_provenance": {"method": "reviewer", "evidence_hash": evidence_hash, "reviewer_id": _id(record.get("reviewer_id"), "factorio-player")},
    }


def iter_coach_cases(path: str | Path, *, split: str = "train"):
    """Yield valid labelled cases and skip guidance-only records explicitly."""
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                yield coach_record_to_case(record, split=split)
            except (json.JSONDecodeError, LayaWorkflowError, TypeError, ValueError) as error:
                # Callers can count skipped records without the training loop
                # failing on one malformed human note.
                yield {"_skipped": True, "line": line_number, "reason": str(error)}


class LayaWorkflowDirector:
    """Run one typed decision boundary for the full director workflow.

    The director owns only model selection and evidence. Candidate generation,
    hard validation, execution, and measurement stay outside this class. A
    low-confidence or malformed model response raises explicitly; there is no
    deterministic decision fallback hidden in the workflow seam.
    """

    def __init__(
        self,
        predictor: Any,
        *,
        confidence_threshold: float = 0.70,
        max_options: int = 20,
        budget_profile: LayaBudgetProfileLike = DEFAULT_BUDGET_PROFILE,
        request_tokenizer: Any | None = None,
    ):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be in [0, 1]")
        if not 1 <= max_options <= 20:
            raise ValueError("max_options must be between 1 and 20")
        self.predictor = predictor
        self.confidence_threshold = confidence_threshold
        self.max_options = max_options
        self.budget_profile = budget_profile
        self.request_tokenizer = request_tokenizer

    def decide(
        self,
        decision_kind: str,
        state: Mapping[str, Any],
        options: Sequence[Mapping[str, Any] | str],
    ) -> dict[str, Any]:
        if decision_kind not in DECISION_KINDS:
            raise LayaWorkflowError(f"unsupported workflow decision kind: {decision_kind}")
        profile = get_budget_profile(self.budget_profile)
        if not options or len(options) > min(self.max_options, profile.max_options):
            raise LayaWorkflowError("workflow decision must have between 1 and 20 options")
        record = {"source": {"surface": state.get("run", {}).get("surface", "training/coach"), "force": state.get("run", {}).get("force", "training-coach")}, "options": options}
        normalized = _normalize_options(record)
        state_copy = copy.deepcopy(dict(state))
        state_copy["options"] = normalized
        option_ids = [str(option["option_id"]) for option in normalized]
        question = {
            decision_kind: {
                "type": "choice",
                "instructions": QUESTION_INSTRUCTIONS[decision_kind],
                "criteria": {option["option_id"]: option["summary"] for option in normalized},
                "option_ids": option_ids,
                "option_count": len(option_ids),
            }
        }
        try:
            validate_state(state_copy)
        except Exception as error:
            raise LayaWorkflowError(f"Laya workflow state is invalid: {error}") from error
        # The predictor builds and budgets the exact model sequence. A JSON
        # envelope check here would measure a representation the model never sees.
        try:
            response = self.predictor.predict(state_copy, question)
        except Exception as error:
            raise LayaWorkflowError(f"Laya workflow inference failed: {error}") from error
        answer = ((response.get("answers") or {}).get(decision_kind) or {})
        selected = str(answer.get("choice", answer.get("selected_label", "")))
        try:
            confidence = float(answer.get("confidence", 0.0))
        except (TypeError, ValueError) as error:
            raise LayaWorkflowError("Laya confidence must be a finite probability") from error
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise LayaWorkflowError("Laya confidence must be a finite probability")
        if selected not in option_ids:
            raise LayaWorkflowError("Laya selected an option outside the validated workflow set")
        if confidence < self.confidence_threshold:
            raise LayaWorkflowError(
                f"Laya confidence {confidence:.3f} is below {self.confidence_threshold:.3f}"
            )
        selected_option = next(option for option in normalized if option["option_id"] == selected)
        return {
            "decision_kind": decision_kind,
            "state": state_copy,
            "question": question[decision_kind],
            "response": response,
            "selected_option": selected_option,
            "confidence": confidence,
        }


__all__ = [
    "DECISION_KINDS",
    "LayaWorkflowError",
    "coach_record_to_case",
    "iter_coach_cases",
    "LayaWorkflowDirector",
    "validate_coach_record",
]
