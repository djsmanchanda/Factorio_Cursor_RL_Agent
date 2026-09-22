# Path: tests/test_laya_operations.py
# Purpose: Verify candidate compilation, hard pruning, signatures, and safe escalation.

from __future__ import annotations

import pytest

from orchestrator.laya_operations import LayaOperationError, OperationCompiler, escalation_option
from orchestrator.laya_tool_registry import OperationDefinition, LayaToolRegistry


def _definition(tool_id: str = "assemble.line", *, executable: bool = True) -> OperationDefinition:
    return OperationDefinition(
        tool_id=tool_id,
        operation_family="assemble" if executable else "observe",
        schemas=("assemble.args",),
        dependencies=("snapshot",),
        eligibility_predicate_ids=("eligible",),
        parameter_generator_id="site_candidates",
        validators=("scope", "legality"),
        risk_tier="ghost_submit" if executable else "read_only",
        authorization_action="assemble" if executable else "observe",
        report_readers=("layout_report",),
        rollback_method="rollback",
        executable=executable,
    )


def _raw(option_id: str = "option.1") -> dict:
    return {
        "option_id": option_id,
        "tool_id": "assemble.line",
        "summary": "Build one legal line",
        "features": {"rate": 1.0, "cost": None},
        "scope": {"surface": "training/1", "force": "training-1", "bounds": None},
        "parameters": {"site": "A"},
    }


def test_compiler_preserves_generator_order_without_ranking() -> None:
    compiler = OperationCompiler(LayaToolRegistry([_definition()]))
    options = compiler.compile([_raw("z.option"), _raw("a.option")])
    assert [option.option_id for option in options] == ["z.option", "a.option"]
    assert options[0].signature.startswith("sha256:")
    assert options[0].to_state_option()["features"]["rate"] == 1.0


def test_unknown_or_observation_only_tools_fail_closed() -> None:
    compiler = OperationCompiler(LayaToolRegistry([_definition(executable=False)]))
    with pytest.raises(LayaOperationError, match="observation-only"):
        compiler.compile([_raw()])
    compiler = OperationCompiler(LayaToolRegistry([_definition()]))
    unknown = _raw()
    unknown["tool_id"] = "unknown"
    with pytest.raises(LayaOperationError, match="unknown tool_id"):
        compiler.compile([unknown])


def test_compiler_rejects_duplicate_and_oversized_option_sets() -> None:
    compiler = OperationCompiler(LayaToolRegistry([_definition()]), max_options=2)
    with pytest.raises(LayaOperationError, match="unique"):
        compiler.compile([_raw(), _raw()])
    with pytest.raises(LayaOperationError, match="split"):
        compiler.compile([_raw("one"), _raw("two"), _raw("three")])


def test_pruning_reports_all_rejections_without_fallback() -> None:
    compiler = OperationCompiler(LayaToolRegistry([_definition()]))
    options = compiler.compile([_raw("one"), _raw("two")])

    def reject(option):
        return option.option_id == "one", "collision"

    reject.validator_id = "collision-validator"
    eligible, pruned = compiler.prune(options, [reject])
    assert [option.option_id for option in eligible] == ["one"]
    assert pruned[0]["option"]["option_id"] == "two"
    assert pruned[0]["prune_reason"] == "collision"


def test_escalation_option_is_explicit_and_read_only() -> None:
    option = escalation_option({"surface": "training/1", "force": "training-1", "bounds": None}, reason="No legal route")
    assert option.operation_family == "escalate"
    assert option.risk_tier == "read_only"
    assert option.to_state_option()["summary"] == "No legal route"
