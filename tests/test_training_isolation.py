# Path: tests/test_training_isolation.py
# Purpose: Verify training identities and imports cannot cross the real-runtime boundary.

from __future__ import annotations

from pathlib import Path

import pytest

from training.isolation import (
    assert_import_firewall,
    assert_runtime_import_allowed,
    assert_training_identity,
    find_import_violations,
)

_ROOT = Path(__file__).resolve().parents[1]


def test_current_repository_obeys_import_firewall() -> None:
    assert find_import_violations(_ROOT) == []
    assert_import_firewall(_ROOT)


@pytest.mark.parametrize(
    ("surface_name", "force_name"),
    [
        ("nauvis", "player"),
        ("planner-sandbox", "planner"),
        ("training/example", "player"),
        ("nauvis", "training-example"),
        ("training/example", "training-other"),
        ("training/Example", "training-Example"),
    ],
)
def test_training_identity_rejects_live_mismatched_and_noncanonical_names(
    surface_name: str,
    force_name: str,
) -> None:
    with pytest.raises(ValueError):
        assert_training_identity(surface_name, force_name)


def test_training_identity_accepts_exact_paired_names() -> None:
    assert_training_identity("training/mining-delivery-002a", "training-mining-delivery-002a")


@pytest.mark.parametrize(
    ("layer", "module_name"),
    [
        ("active", "training"),
        ("active", "training.contracts"),
        ("active", "experimental.legacy_autonomy"),
        ("training", "experimental.legacy_autonomy.rl_advisor"),
        ("training", "orchestrator.autonomous_builder"),
        ("training", "orchestrator.stage_transport"),
    ],
)
def test_runtime_import_firewall_rejects_forbidden_dependencies(
    layer: str,
    module_name: str,
) -> None:
    with pytest.raises(ImportError):
        assert_runtime_import_allowed(layer, module_name)


def test_training_import_firewall_allows_pure_current_dependencies() -> None:
    assert_runtime_import_allowed("training", "core.action_catalog")
    assert_runtime_import_allowed("training", "planners.resource_layouts")
    assert_runtime_import_allowed("training", "orchestrator.game_bridge")
