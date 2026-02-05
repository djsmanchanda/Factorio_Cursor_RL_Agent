# Path: core/upgrade_executor.py
# Purpose: Validate and prepare authorized upgrade actions deterministically.

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from jsonschema import Draft7Validator


@dataclass(frozen=True)
class UpgradeAction:
    action: str
    position: dict
    from_name: str | None
    to_name: str | None
    module_to: str | None
    module_count: int | None
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


def prepare_upgrade_actions(
    upgrade_plan: dict,
    upgrade_authorization: dict,
    plan_schema_path: Path | None = None,
    auth_schema_path: Path | None = None,
) -> List[UpgradeAction]:
    repo_root = Path(__file__).resolve().parents[1]
    if plan_schema_path is None:
        plan_schema_path = repo_root / "schemas" / "upgrade_plan.schema.json"
    if auth_schema_path is None:
        auth_schema_path = repo_root / "schemas" / "upgrade_authorization.schema.json"

    _validate_schema(upgrade_plan, plan_schema_path, "UpgradePlan")
    _validate_schema(upgrade_authorization, auth_schema_path, "UpgradeAuthorization")

    approved = set(upgrade_authorization.get("approved_actions", []))
    if "apply_upgrades" not in approved:
        raise ValueError("Upgrade authorization missing apply_upgrades")

    scope_limits = upgrade_authorization.get("scope_limits") or {}
    max_count = scope_limits.get("max_count")
    block_filter = set(scope_limits.get("block_filter", []))

    actions: List[UpgradeAction] = []
    for entry in upgrade_plan.get("actions", []):
        if block_filter and entry.get("block") not in block_filter:
            continue
+        actions.append(
+            UpgradeAction(
+                action=entry.get("action"),
+                position=entry.get("position"),
+                from_name=entry.get("from_name"),
+                to_name=entry.get("to_name"),
+                module_to=entry.get("module_to"),
+                module_count=entry.get("module_count"),
+                block=entry.get("block"),
+            )
+        )
+        if max_count is not None and len(actions) >= int(max_count):
+            break
-
-    for entry in upgrade_plan.get("actions", []):
-        actions.append(
-            UpgradeAction(
-                action=entry.get("action"),
-                position=entry.get("position"),
-                from_name=entry.get("from_name"),
-                to_name=entry.get("to_name"),
-                module_to=entry.get("module_to"),
-                module_count=entry.get("module_count"),
-                block=entry.get("block"),
-            )
-        )
-
     return actions
