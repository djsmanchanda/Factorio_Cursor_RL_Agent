# Path: tests/test_laya_contracts.py
# Purpose: Verify strict M0 Laya director schemas and fail-closed boundaries.

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft7Validator
from referencing import Registry, Resource


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
SCHEMA_NAMES = (
    "laya_state.schema.json",
    "laya_case.schema.json",
    "laya_decision.schema.json",
    "laya_tool_registry.schema.json",
    "laya_guideline_pack.schema.json",
)
HASH = "sha256:" + ("0" * 64)
SCHEMA_BASE_URI = "https://factorio-agent.local/schemas/"


def _schemas() -> dict[str, dict]:
    schemas = {
        name: json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))
        for name in SCHEMA_NAMES
    }
    for name, schema in schemas.items():
        schema["$id"] = SCHEMA_BASE_URI + name
    return schemas


def _validator(name: str) -> Draft7Validator:
    schemas = _schemas()
    registry = Registry().with_resources(
        (SCHEMA_BASE_URI + key, Resource.from_contents(value))
        for key, value in schemas.items()
    )
    return Draft7Validator(schemas[name], registry=registry)


def _versioned(identifier: str) -> dict:
    return {"id": identifier, "version": "1.0.0", "hash": HASH}


def _scope() -> dict:
    return {"surface": "training/1", "force": "training-1", "bounds": None}


def _option() -> dict:
    return {
        "option_id": "option.build",
        "operation_family": "assemble",
        "tool_id": "assemble.line",
        "signature": HASH,
        "plan_hash": HASH,
        "summary": "Build one validated line",
        "features": {"expected_rate": 1.0},
        "scope": _scope(),
        "risk_tier": "ghost_submit",
    }


def _state() -> dict:
    return {
        "version": "1.0.0",
        "run": {
            "run_id": "run-1",
            "episode_id": "episode-1",
            "tick": 100,
            "elapsed_ticks": 100,
            "bootstrap_profile": "training-v1",
            "surface": "training/1",
            "force": "training-1",
            "runtime": "disposable_training",
        },
        "objective": {
            "family": "mining",
            "description": "Deliver iron ore",
            "target": 1.0,
            "unit": "items_per_tick",
        },
        "goal_stack": [{
            "goal_id": "goal.iron",
            "kind": "production",
            "objective": "Deliver iron ore",
            "status": "active",
            "gap": 1.0,
            "age_ticks": 100,
            "retry_count": 0,
            "last_failure_kind": "none",
        }],
        "acceptance_gap": {
            "metric": "delivery_rate",
            "current": 0.0,
            "target": 1.0,
            "remaining": 1.0,
            "unit": "items_per_tick",
            "sustained_ticks": 0,
            "required_sustained_ticks": 600,
        },
        "current_transaction": None,
        "state_digest": {
            "power": {
                "generation_kw": 100.0,
                "firm_generation_kw": 100.0,
                "accumulator_mj": 0.0,
                "unpowered_consumers": 0,
                "poles": 1,
            },
            "logistics": {
                "robots": 0,
                "providers_available": 0,
                "pending_ghosts": 0,
                "ghost_blockages_top3": [],
            },
            "stock": {
                "total": 0.0,
                "accessible": 0.0,
                "allocated": 0.0,
                "top_shortages": [],
            },
            "production": {
                "rate_now": 0.0,
                "rate_target": 1.0,
                "sustained_ticks": 0,
                "bottleneck_hint": None,
            },
            "resource": {"remaining": 1000.0, "patch_bounds_hint": None},
            "space": {
                "free_area_tiles": 100,
                "reserved_area_tiles": 0,
                "occupied_footprint_tiles": 0,
                "candidate_site_count": 1,
            },
        },
        "recent_history": [],
        "active_failures": [],
        "guidelines": [{
            "rule_id": "rule.isolation",
            "kind": "hard",
            "statement": "Remain on the declared training surface and force.",
        }],
        "options": [_option()],
        "provenance": {
            "renderer_version": "1.0.0",
            "state_snapshot_hash": HASH,
            "guideline_pack": _versioned("pack.training"),
            "tool_registry": _versioned("registry.training"),
            "model": {"model_id": "laya.base", "model_hash": HASH},
        },
        "budget": {
            "materials_remaining": 100.0,
            "construction_budget_remaining": 10,
            "action_count_remaining": 10,
            "elapsed_ticks_remaining": 3600,
        },
    }


def _case() -> dict:
    return {
        "version": "1.0.0",
        "case_id": "case-1",
        "source": {"kind": "rl_transition", "run_id": "run-1", "provenance_hash": HASH},
        "state": _state(),
        "questions": {
            "next_operation": {
                "type": "choice",
                "instructions": "Choose the next operation.",
                "criteria": {"option.build": "Build one validated line"},
            }
        },
        "gold": {
            "next_operation": {
                "label": "option.build",
                "probabilities": {"option.build": 1.0},
                "label_source": "outcome",
                "confidence": 1.0,
            }
        },
        "weights": {
            "case_weight": 1.0,
            "question_weights": {"next_operation": 1.0},
            "source_quality": 1.0,
        },
        "created_at": "2026-09-19T00:00:00Z",
        "dataset_version": "1.0.0",
        "split": "train",
        "label_provenance": {"method": "direct_outcome", "evidence_hash": HASH},
    }


def _decision() -> dict:
    return {
        "version": "1.0.0",
        "decision_id": "decision-1",
        "run_id": "run-1",
        "episode_id": "episode-1",
        "tick": 100,
        "decision_kind": "operation_family",
        "state": _state(),
        "eligible_options": [{
            "option": _option(),
            "validator_ids": ["validator.scope"],
            "validator_passed": True,
        }],
        "pruned_options": [],
        "model_output": {
            "model_id": "laya.base",
            "model_hash": HASH,
            "answers": {
                "next_operation": {
                    "type": "choice",
                    "selected_label": "option.build",
                    "probabilities": {"option.build": 1.0},
                    "confidence": 1.0,
                }
            },
            "temperature": 1.0,
            "margin": 1.0,
            "entropy": 0.0,
            "latency_ms": 10.0,
        },
        "selected_action": {
            "kind": "operation",
            "option_id": "option.build",
            "tool_id": "assemble.line",
            "signature": HASH,
            "why_rule_ids": ["rule.isolation"],
        },
        "execution": {
            "status": "not_started",
            "transaction_id": None,
            "authorization": {"authorized": False, "source": None, "scope_hash": None},
            "validator_ids": ["validator.scope"],
            "reports": [],
        },
        "outcome": {
            "status": "pending",
            "failure_kind": "none",
            "tick_delta": 0,
            "summary": "Awaiting execution",
            "measurements": {},
        },
        "next_state": None,
        "reward": {"components": {"pending": 0.0}, "total": 0.0},
        "attribution": None,
        "provenance": {
            "state_hash": HASH,
            "guideline_pack": _versioned("pack.training"),
            "tool_registry": _versioned("registry.training"),
            "renderer_version": "1.0.0",
        },
    }


def _registry() -> dict:
    return {
        "version": "1.0.0",
        "registry_id": "registry.training",
        "registry_hash": HASH,
        "compiler_version": "1.0.0",
        "compiled_at": "2026-09-19T00:00:00Z",
        "entries": [{
            "tool_id": "assemble.line",
            "version": "1.0.0",
            "description": "Compile a validated assembly line.",
            "operation_family": "assemble",
            "executable": True,
            "availability": "executable",
            "operation_schema": {
                "schema_id": "assemble.line.args",
                "version": "1.0.0",
                "type": "object",
                "required": [],
                "properties": {},
                "additional_properties": False,
            },
            "read_dependencies": [],
            "eligibility_predicates": [],
            "parameter_generators": [],
            "expected_effects": [],
            "cost_estimate": {"material_items": {}, "ticks": 0, "space_tiles": 0},
            "idempotent": False,
            "risk_tier": "ghost_submit",
            "required_validators": ["validator.scope"],
            "execution_authorization": {
                "required": True,
                "authorization_kind": "policy",
                "required_actions": ["assemble.line"],
                "scope_limits": {"max_count": 1, "block_filter": []},
            },
            "report_readers": ["report.layout"],
            "rollback_method": {"supported": False, "description": "Safe stop before submission."},
        }],
    }


def _guideline_pack() -> dict:
    document = {"path": "docs/system_invariants.md", "hash": HASH}
    return {
        "version": "1.0.0",
        "pack_id": "pack.training",
        "pack_version": "1.0.0",
        "task_scopes": ["assemble"],
        "goal": {"statement": "Build safely.", "acceptance_assertion": "Output is sustained."},
        "hard_rules": [{
            "rule_id": "rule.isolation",
            "scope": ["assemble"],
            "kind": "hard",
            "statement": "Remain on the declared surface and force.",
            "rationale": "Training and production are isolated.",
            "test_assertion": "Every action scope matches the episode identity.",
            "provenance": {
                "source_documents": [document],
                "compiled_by": "guideline-compiler",
                "compiled_at": "2026-09-19T00:00:00Z",
            },
            "promotion_evidence": {"status": "not_required", "evidence_refs": []},
        }],
        "soft_rules": [],
        "tool_inventory": [{
            "tool_id": "assemble.line",
            "registry_version": "1.0.0",
            "when_to_use": "When an assembly goal has a legal funded candidate.",
            "when_not_to_use": "When validators reject every candidate.",
            "failure_modes": ["budget", "collision"],
        }],
        "worked_examples": [],
        "provenance": {
            "source_documents": [document],
            "compiler_version": "1.0.0",
            "compiled_at": "2026-09-19T00:00:00Z",
            "pack_hash": HASH,
        },
        "promotion": {"status": "proposed", "evidence": None, "parent_pack": None},
    }


EXAMPLES = {
    "laya_state.schema.json": _state,
    "laya_case.schema.json": _case,
    "laya_decision.schema.json": _decision,
    "laya_tool_registry.schema.json": _registry,
    "laya_guideline_pack.schema.json": _guideline_pack,
}


def test_all_laya_schemas_are_valid_draft7() -> None:
    for schema in _schemas().values():
        Draft7Validator.check_schema(schema)


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_minimal_contract_examples_validate(schema_name: str) -> None:
    assert list(_validator(schema_name).iter_errors(EXAMPLES[schema_name]())) == []


@pytest.mark.parametrize("schema_name", SCHEMA_NAMES)
def test_contracts_reject_unknown_root_fields(schema_name: str) -> None:
    payload = EXAMPLES[schema_name]()
    payload["unknown"] = True
    assert list(_validator(schema_name).iter_errors(payload))


def test_case_requires_an_explicit_dataset_split() -> None:
    payload = _case()
    del payload["split"]
    assert list(_validator("laya_case.schema.json").iter_errors(payload))


def test_registry_never_confuses_required_authority_with_a_grant() -> None:
    payload = _registry()
    authorization = payload["entries"][0]["execution_authorization"]
    authorization["approved_actions"] = authorization.pop("required_actions")
    assert list(_validator("laya_tool_registry.schema.json").iter_errors(payload))


def test_submitted_decision_requires_positive_authorization() -> None:
    payload = _decision()
    payload["execution"]["status"] = "submitted"
    assert list(_validator("laya_decision.schema.json").iter_errors(payload))


def test_promoted_guideline_pack_requires_zero_safety_violations() -> None:
    payload = _guideline_pack()
    payload["promotion"] = {
        "status": "promoted",
        "parent_pack": None,
        "evidence": {
            "model_ref": _versioned("model-1"),
            "dataset_ref": _versioned("dataset-1"),
            "scenario_refs": [{"kind": "scenario", "id": "scenario-1", "hash": HASH}],
            "evaluation_ref": {"kind": "evaluation", "id": "eval-1", "hash": HASH},
            "safety_violations": 1,
        },
    }
    assert list(_validator("laya_guideline_pack.schema.json").iter_errors(payload))


def test_director_state_never_exceeds_twenty_options() -> None:
    payload = _state()
    payload["options"] = [copy.deepcopy(_option()) for _ in range(21)]
    assert list(_validator("laya_state.schema.json").iter_errors(payload))
