# Path: tests/test_laya_default_registry.py
# Purpose: Verify the initial disposable registry contains capabilities, not policy scores.

from __future__ import annotations

from orchestrator.laya_default_registry import training_registry


def test_training_registry_has_only_bounded_operation_capabilities() -> None:
    registry = training_registry()
    assert [entry.tool_id for entry in registry.selection_options()] == [
        "training.escalate",
        "training.furnace_refining",
        "training.mining_delivery",
        "training.wait",
    ]
    assert all(not hasattr(entry, "score") for entry in registry)
