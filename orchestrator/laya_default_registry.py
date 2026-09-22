# Path: orchestrator/laya_default_registry.py
# Purpose: Provide the first executable Laya registry entries for disposable training.

from __future__ import annotations

from orchestrator.laya_tool_registry import LayaToolRegistry, OperationDefinition


def _definition(tool_id: str, family: str, risk_tier: str, authorization: str) -> OperationDefinition:
    return OperationDefinition(
        tool_id=tool_id,
        operation_family=family,
        schemas=(f"{tool_id}.args",),
        dependencies=("laya.state", "training.snapshot"),
        eligibility_predicate_ids=(f"{tool_id}.eligible",),
        parameter_generator_id=f"{tool_id}.candidates",
        validators=("runtime.identity", "operation.scope", "plan.legality"),
        risk_tier=risk_tier,
        authorization_action=authorization,
        report_readers=("training.report", "training.measurement"),
        rollback_method=f"{tool_id}.rollback",
        executable=True,
    )


def training_registry() -> LayaToolRegistry:
    """Return the bounded initial registry; no deterministic ranking is embedded."""
    return LayaToolRegistry([
        _definition("training.mining_delivery", "mine", "ghost_submit", "training.execute"),
        _definition("training.furnace_refining", "smelt", "ghost_submit", "training.execute"),
        _definition("training.wait", "wait", "read_only", "training.observe"),
        _definition("training.escalate", "escalate", "read_only", "training.observe"),
    ])


__all__ = ["training_registry"]
