# Path: orchestrator/laya_guidelines.py
# Purpose: Compile versioned hard/soft guideline packs without silently promoting model proposals.

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from jsonschema import Draft7Validator

from orchestrator.laya_operations import sha256_hash


class LayaGuidelineError(ValueError):
    """Raised when a guideline pack cannot be compiled or validated."""


_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA = json.loads((_ROOT / "schemas" / "laya_guideline_pack.schema.json").read_text(encoding="utf-8"))
_VALIDATOR = Draft7Validator(_SCHEMA)
_HASH = "sha256:" + ("0" * 64)


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LayaGuidelineError(f"{field} must be a non-empty string")
    return value.strip()


def _rule(
    rule: Mapping[str, Any], *, kind: str, scopes: tuple[str, ...], source_documents: list[dict[str, str]], compiler_version: str,
) -> dict[str, Any]:
    if rule.get("kind", kind) != kind:
        raise LayaGuidelineError(f"guideline {rule.get('rule_id')} has the wrong kind")
    rule_id = _non_empty(rule.get("rule_id"), "rule_id")
    scope = tuple(str(value) for value in rule.get("scope", scopes))
    if not scope:
        raise LayaGuidelineError(f"guideline {rule_id} needs a task scope")
    payload: dict[str, Any] = {
        "rule_id": rule_id,
        "scope": list(scope),
        "kind": kind,
        "statement": _non_empty(rule.get("statement"), "statement"),
        "rationale": _non_empty(rule.get("rationale"), "rationale"),
        "test_assertion": _non_empty(rule.get("test_assertion"), "test_assertion"),
        "provenance": {
            "source_documents": source_documents,
            "compiled_by": "laya-guideline-compiler",
            "compiled_at": "1970-01-01T00:00:00+00:00",
        },
        "promotion_evidence": {"status": "not_required", "evidence_refs": []},
    }
    if kind == "soft":
        payload["reward_weight"] = float(rule.get("reward_weight", 1.0))
    return payload


def compile_guideline_pack(
    *,
    pack_id: str,
    pack_version: str,
    task_scopes: Iterable[str],
    goal: Mapping[str, str],
    hard_rules: Iterable[Mapping[str, Any]],
    soft_rules: Iterable[Mapping[str, Any]],
    tool_inventory: Iterable[Mapping[str, Any]],
    source_documents: Iterable[Mapping[str, str]],
    worked_examples: Iterable[Mapping[str, Any]] = (),
    compiler_version: str = "1.0.0",
) -> dict[str, Any]:
    scopes = tuple(str(scope) for scope in task_scopes)
    if not scopes:
        raise LayaGuidelineError("guideline packs need at least one task scope")
    documents = [{"path": _non_empty(doc.get("path"), "document path"), "hash": _non_empty(doc.get("hash"), "document hash")} for doc in source_documents]
    if not documents:
        raise LayaGuidelineError("guideline packs need source document provenance")
    pack: dict[str, Any] = {
        "version": "1.0.0",
        "pack_id": _non_empty(pack_id, "pack_id"),
        "pack_version": _non_empty(pack_version, "pack_version"),
        "task_scopes": list(scopes),
        "goal": {"statement": _non_empty(goal.get("statement"), "goal.statement"), "acceptance_assertion": _non_empty(goal.get("acceptance_assertion"), "goal.acceptance_assertion")},
        "hard_rules": [_rule(rule, kind="hard", scopes=scopes, source_documents=documents, compiler_version=compiler_version) for rule in hard_rules],
        "soft_rules": [_rule(rule, kind="soft", scopes=scopes, source_documents=documents, compiler_version=compiler_version) for rule in soft_rules],
        "tool_inventory": [dict(tool) for tool in tool_inventory],
        "worked_examples": [dict(example) for example in worked_examples],
        "provenance": {
            "source_documents": documents,
            "compiler_version": _non_empty(compiler_version, "compiler_version"),
            "compiled_at": "1970-01-01T00:00:00+00:00",
            "pack_hash": _HASH,
        },
        "promotion": {"status": "proposed", "evidence": None, "parent_pack": None},
    }
    pack["provenance"]["pack_hash"] = sha256_hash(pack)
    errors = sorted(_VALIDATOR.iter_errors(pack), key=lambda error: list(error.path))
    if errors:
        raise LayaGuidelineError("guideline pack validation failed: " + "; ".join(error.message for error in errors[:4]))
    return pack


__all__ = ["LayaGuidelineError", "compile_guideline_pack"]
