# Path: tests/test_executor_settings_coverage.py
# Purpose: Guard the executor's silent-skip failure mode -- a settable action field missing from SETTING_FIELDS is never applied to an entity that already exists, and nothing reports it.

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_EXECUTOR = (REPO_ROOT / "factorio_mod" / "layout_executor.lua").read_text(encoding="utf-8")
_SECTIONS = (REPO_ROOT / "factorio_mod" / "logistic_sections.lua").read_text(encoding="utf-8")
_SCHEMA = json.loads(
    (REPO_ROOT / "schemas" / "build_plan.schema.json").read_text(encoding="utf-8")
)

# Applied only at creation time, or a modifier of another field rather than a
# setting in its own right.
_NOT_STANDALONE_SETTINGS = {
    "recipe",          # ghosts carry it from creation; guarded separately
    "fill_percentage",  # qualifies infinity_filter
    "entity", "position", "direction", "action_type", "underground_type",
    "logistic_group",   # qualifies logistic_requests
}


def _setting_fields() -> set[str]:
    block = re.search(r"M\.SETTING_FIELDS = \{(.*?)\}", _SECTIONS, re.S)
    assert block, "SETTING_FIELDS table not found in logistic_sections.lua"
    return set(re.findall(r'"([^"]+)"', block.group(1)))


def _fields_configure_reads() -> set[str]:
    start = _EXECUTOR.index("local function configure_created_entity")
    end = _EXECUTOR.index("local function", start + 1)
    return set(re.findall(r"action\.(\w+)", _EXECUTOR[start:end]))


def _action_properties() -> set[str]:
    """Every field a build-plan action may carry, from the schema itself."""
    found: set[str] = set()

    def walk(node) -> None:
        if isinstance(node, dict):
            properties = node.get("properties", {})
            if "action_type" in properties:
                found.update(properties)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(_SCHEMA)
    return found


def test_every_field_configure_applies_is_gated_as_a_setting() -> None:
    """Anything configure_created_entity writes must be listed in
    SETTING_FIELDS, or it is silently skipped for an already-present entity."""
    applied = _fields_configure_reads() - _NOT_STANDALONE_SETTINGS
    missing = sorted(applied - _setting_fields())
    assert not missing, (
        f"configure_created_entity applies {missing} but SETTING_FIELDS omits them; "
        "an existing entity would be reported already_present with the setting never applied"
    )


def test_setting_fields_are_all_real_schema_fields() -> None:
    """The gate must not reference fields no plan can carry."""
    unknown = sorted(_setting_fields() - _action_properties())
    assert not unknown, f"SETTING_FIELDS names fields absent from build_plan.schema.json: {unknown}"


def test_logistic_sections_is_gated() -> None:
    """The regression that shipped: paired mall cells put a second machine on an
    EXISTING requester, so the second half only ever configures via this path."""
    assert "logistic_sections" in _setting_fields()


def test_existing_entity_paths_use_the_shared_gate() -> None:
    """Both place_ghost and place_entity must ask needs_reconfiguration rather
    than re-listing fields inline, which is how the omission happened."""
    assert _EXECUTOR.count("needs_reconfiguration(action, existing)") == 2
    assert "action.logistic_request or action.logistic_requests" not in _EXECUTOR


def test_the_schema_promises_no_action_field_the_executor_ignores() -> None:
    """`blueprint` sat in the schema for a capability the executor never had, so
    a plan could declare one and have it silently dropped -- the same silent
    failure SETTING_FIELDS exists to prevent, one level up. Any field added here
    must be either a setting, a creation-time field, or a qualifier of one."""
    declared = _action_properties()
    accounted = _setting_fields() | _NOT_STANDALONE_SETTINGS

    assert declared <= accounted, (
        f"schema declares {sorted(declared - accounted)}, which the executor "
        "neither applies at creation nor reapplies as a setting"
    )
