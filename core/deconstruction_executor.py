# Path: core/deconstruction_executor.py
# Purpose: Validate and prepare authorized deconstruction actions deterministically.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class DeconstructionAction:
    action: str
    position: dict
    name: str | None
    block: str | None


def _load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_schema(payload: dict, schema_path: Path, label: str) -> None:
    schema = _load_json(schema_path)
    validator = Draft7Validator(schema)
    errors = list(validator.iter_errors(payload))
    if errors:
        messages = []
        for error in errors:
            path = "/".join(str(part) for part in error.path) if error.path else "<root>"
            messages.append(f"- {path}: {error.message}")
        raise ValueError(f"{label} validation FAILED:\n" + "\n".join(messages))


def prepare_deconstruction_actions(
    deconstruction_plan: dict,
    deconstruction_authorization: dict,
    plan_schema_path: Path | None = None,
    auth_schema_path: Path | None = None,
) -> List[DeconstructionAction]:
    repo_root = Path(__file__).resolve().parents[1]
    if plan_schema_path is None:
        plan_schema_path = repo_root / "schemas" / "deconstruction_plan.schema.json"
    if auth_schema_path is None:
        auth_schema_path = repo_root / "schemas" / "deconstruction_authorization.schema.json"

    _validate_schema(deconstruction_plan, plan_schema_path, "DeconstructionPlan")
    _validate_schema(deconstruction_authorization, auth_schema_path, "DeconstructionAuthorization")

    approved = set(deconstruction_authorization.get("approved_actions", []))
    if "apply_deconstruction" not in approved:
        raise ValueError("Deconstruction authorization missing apply_deconstruction")

    scope_limits = deconstruction_authorization.get("scope_limits") or {}
    max_count = scope_limits.get("max_count")
    block_filter = set(scope_limits.get("block_filter", []))

    actions: List[DeconstructionAction] = []
    for entry in deconstruction_plan.get("actions", []):
        if block_filter and entry.get("block") not in block_filter:
            continue

        actions.append(
            DeconstructionAction(
                action=entry.get("action"),
                position=entry.get("position"),
                name=entry.get("name"),
                block=entry.get("block"),
            )
        )
        if max_count is not None and len(actions) >= int(max_count):
            break

    return actions
