"""Path: tests/test_laya_tool_registry.py
Purpose: Verify the fail-closed Laya operation registry seam.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from orchestrator.laya_tool_registry import (
    LayaToolRegistry,
    LayaToolRegistryError,
    OperationDefinition,
)


def _operation(tool_id: str, *, executable: bool = True, authorization_action: str = "build") -> OperationDefinition:
    return OperationDefinition(
        tool_id=tool_id,
        operation_family="assemble",
        schemas=("laya.operation.v1",),
        dependencies=("world.snapshot",),
        eligibility_predicate_ids=("eligibility.production.v1",),
        parameter_generator_id="production.site_candidates.v1",
        validators=("validator.legality.v1", "validator.scope.v1"),
        risk_tier="ghost_submit",
        authorization_action=authorization_action,
        report_readers=("report.production.v1",),
        rollback_method="rollback.transaction.v1",
        executable=executable,
    )


def test_selection_options_are_executable_authorization_addressable_and_ordered() -> None:
    registry = LayaToolRegistry(
        [_operation("zeta.build"), _operation("observe.state", executable=False), _operation("alpha.repair")]
    )

    assert [entry.tool_id for entry in registry.definitions] == [
        "alpha.repair",
        "observe.state",
        "zeta.build",
    ]
    assert [entry.tool_id for entry in registry.selection_options()] == [
        "alpha.repair",
        "zeta.build",
    ]
    assert registry.options() == registry.selectable()


def test_registry_and_definitions_are_immutable() -> None:
    definition = _operation("alpha.build")
    registry = LayaToolRegistry([definition])

    with pytest.raises(FrozenInstanceError):
        definition.executable = False  # type: ignore[misc]
    with pytest.raises(TypeError):
        registry._by_tool_id["other"] = definition  # type: ignore[index]
    assert isinstance(registry.definitions, tuple)
    assert isinstance(definition.validators, tuple)


def test_duplicate_tool_ids_fail_closed() -> None:
    with pytest.raises(LayaToolRegistryError, match="duplicate tool_id"):
        LayaToolRegistry([_operation("same.tool"), _operation("same.tool")])


@pytest.mark.parametrize(
    "field, value",
    [
        ("schemas", ()),
        ("dependencies", ()),
        ("eligibility_predicate_ids", ()),
        ("parameter_generator_id", ""),
        ("validators", ()),
        ("risk_tier", ""),
        ("authorization_action", ""),
        ("report_readers", ()),
        ("rollback_method", ""),
    ],
)
def test_missing_contract_or_safety_metadata_fails(field: str, value: object) -> None:
    incomplete = replace(_operation("incomplete.tool"), **{field: value})
    with pytest.raises(LayaToolRegistryError, match="missing safety/contract metadata"):
        LayaToolRegistry([incomplete])


def test_executable_unauthorized_sentinel_fails_closed() -> None:
    with pytest.raises(LayaToolRegistryError, match="no explicit authorization"):
        LayaToolRegistry([_operation("blocked.tool", authorization_action="none")])


@pytest.mark.parametrize(
    "field, value, message",
    [
        ("operation_family", "production", "operation_family must be one of"),
        ("risk_tier", "low", "risk_tier must be one of"),
        ("authorization_action", "build action", "stable identifier"),
        ("rollback_method", "rollback action", "stable identifier"),
    ],
)
def test_closed_vocab_and_handler_identifiers_fail_closed(
    field: str, value: object, message: str
) -> None:
    with pytest.raises(LayaToolRegistryError, match=message):
        replace(_operation("invalid.tool"), **{field: value})


def test_observation_only_definition_is_retained_but_never_offered() -> None:
    observation = _operation("observe.diagnose", executable=False, authorization_action="observe")
    registry = LayaToolRegistry([observation])

    assert registry.get("observe.diagnose") is observation
    assert registry.selection_options() == ()


def test_validation_rejects_non_definitions() -> None:
    with pytest.raises(LayaToolRegistryError, match="OperationDefinition"):
        LayaToolRegistry([{"tool_id": "not-a-definition"}])  # type: ignore[list-item]
