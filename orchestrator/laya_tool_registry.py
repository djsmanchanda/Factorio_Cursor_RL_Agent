"""Path: orchestrator/laya_tool_registry.py
Purpose: Immutable, fail-closed operation definitions for the Laya director.

This module is deliberately only a contract seam.  It does not execute tools,
evaluate predicates, rank candidates, or choose a policy action.  A later
operation compiler may use the identifiers in a definition to do those jobs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
import re
from typing import Any


class LayaToolRegistryError(ValueError):
    """Raised when an operation registry would not be safe to expose."""


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")
_UNAUTHORIZED = frozenset({"none", "unauthorized", "forbidden", "deny"})
_OPERATION_FAMILIES = frozenset({
    "observe", "diagnose", "mine", "smelt", "assemble", "route", "power",
    "logistics", "research", "repair", "upgrade", "retire", "reserve_space",
    "wait", "escalate",
})
_RISK_TIERS = frozenset({
    "read_only", "ghost_submit", "reversible_mutation", "destructive_mutation",
    "human_authorized",
})


def _identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or not _IDENTIFIER.fullmatch(value):
        raise LayaToolRegistryError(
            f"{field} must be a non-empty stable identifier"
        )
    return value


def _ids(value: Sequence[str] | Iterable[str] | str | None, field: str) -> tuple[str, ...]:
    """Return an immutable identifier tuple, rejecting malformed metadata."""
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)
        except TypeError as error:
            raise LayaToolRegistryError(f"{field} must be a sequence of identifiers") from error
    for item in values:
        _identifier(item, field)
    return values


@dataclass(frozen=True, slots=True)
class OperationDefinition:
    """Declarative metadata for one operation family/tool.

    Every field is part of the versioned action-space contract.  Identifiers
    point to code-owned schemas, predicates, generators, validators, readers,
    and rollback handlers; this object never imports or invokes any of them.
    ``executable=False`` is the explicit catalog form for observation-only or
    not-yet-implemented capabilities.
    """

    tool_id: str
    operation_family: str
    schemas: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    eligibility_predicate_ids: tuple[str, ...] = ()
    parameter_generator_id: str = ""
    validators: tuple[str, ...] = ()
    risk_tier: str = ""
    authorization_action: str = ""
    report_readers: tuple[str, ...] = ()
    rollback_method: str = ""
    executable: bool = False

    def __post_init__(self) -> None:
        # Dataclass freezing prevents reassignment after construction.  These
        # conversions also prevent mutable lists supplied by callers from
        # becoming an escape hatch through a supposedly immutable definition.
        object.__setattr__(self, "tool_id", _identifier(self.tool_id, "tool_id"))
        object.__setattr__(
            self, "operation_family", _identifier(self.operation_family, "operation_family")
        )
        if self.operation_family not in _OPERATION_FAMILIES:
            raise LayaToolRegistryError(
                f"operation_family must be one of {sorted(_OPERATION_FAMILIES)}"
            )
        for field in (
            "schemas",
            "dependencies",
            "eligibility_predicate_ids",
            "validators",
            "report_readers",
        ):
            object.__setattr__(self, field, _ids(getattr(self, field), field))
        if self.parameter_generator_id:
            object.__setattr__(
                self,
                "parameter_generator_id",
                _identifier(self.parameter_generator_id, "parameter_generator_id"),
            )
        if self.risk_tier and self.risk_tier not in _RISK_TIERS:
            raise LayaToolRegistryError(f"risk_tier must be one of {sorted(_RISK_TIERS)}")
        if self.authorization_action:
            object.__setattr__(
                self,
                "authorization_action",
                _identifier(self.authorization_action, "authorization_action"),
            )
        if self.rollback_method:
            object.__setattr__(
                self,
                "rollback_method",
                _identifier(self.rollback_method, "rollback_method"),
            )
        if not isinstance(self.executable, bool):
            raise LayaToolRegistryError("executable must be a boolean")

    # Compatibility names mirror the vocabulary used by the design docs while
    # retaining one canonical representation in serialized definitions.
    @property
    def args_schema(self) -> tuple[str, ...]:
        return self.schemas

    @property
    def read_dependencies(self) -> tuple[str, ...]:
        return self.dependencies

    @property
    def preconditions(self) -> tuple[str, ...]:
        return self.eligibility_predicate_ids

    @property
    def required_validators(self) -> tuple[str, ...]:
        return self.validators

    def validate(self) -> None:
        """Reject an incomplete definition before it can enter a registry."""
        required_sequences = {
            "schemas": self.schemas,
            "dependencies": self.dependencies,
            "eligibility_predicate_ids": self.eligibility_predicate_ids,
            "validators": self.validators,
            "report_readers": self.report_readers,
        }
        missing = [name for name, value in required_sequences.items() if not value]
        if not self.parameter_generator_id:
            missing.append("parameter_generator_id")
        if not self.risk_tier:
            missing.append("risk_tier")
        if not self.authorization_action:
            missing.append("authorization_action")
        if not self.rollback_method:
            missing.append("rollback_method")
        if missing:
            raise LayaToolRegistryError(
                f"tool {self.tool_id!r} is missing safety/contract metadata: "
                + ", ".join(missing)
            )
        if self.authorization_action.casefold() in _UNAUTHORIZED:
            raise LayaToolRegistryError(
                f"tool {self.tool_id!r} has no explicit authorization action"
            )


@dataclass(frozen=True, slots=True, init=False)
class LayaToolRegistry:
    """An immutable, deterministically ordered collection of tool definitions.

    The registry retains observation-only definitions for introspection, but
    :meth:`selection_options` is the only selection-facing view and filters
    strictly to ``executable`` entries with explicit authorization metadata.
    Runtime authorization is still checked later against a concrete grant;
    appearing here never means an operation is already authorized.
    Ordering is lexical by stable ``tool_id`` and is not a policy ranking.
    """

    _definitions: tuple[OperationDefinition, ...]
    _by_tool_id: Mapping[str, OperationDefinition]

    def __init__(self, definitions: Iterable[OperationDefinition]) -> None:
        values = tuple(definitions)
        for definition in values:
            if not isinstance(definition, OperationDefinition):
                raise LayaToolRegistryError(
                    "registry definitions must be OperationDefinition instances"
                )
            definition.validate()
        ids = [definition.tool_id for definition in values]
        duplicates = sorted({tool_id for tool_id in ids if ids.count(tool_id) > 1})
        if duplicates:
            raise LayaToolRegistryError(
                "duplicate tool_id(s): " + ", ".join(duplicates)
            )
        ordered = tuple(sorted(values, key=lambda definition: definition.tool_id))
        object.__setattr__(self, "_definitions", ordered)
        object.__setattr__(
            self,
            "_by_tool_id",
            MappingProxyType({definition.tool_id: definition for definition in ordered}),
        )

    @property
    def definitions(self) -> tuple[OperationDefinition, ...]:
        """All validated definitions, including observation-only entries."""
        return self._definitions

    def __iter__(self):
        return iter(self._definitions)

    def __len__(self) -> int:
        return len(self._definitions)

    def get(self, tool_id: str) -> OperationDefinition | None:
        """Return a definition by stable ID without invoking any capability."""
        return self._by_tool_id.get(tool_id)

    def selection_options(self) -> tuple[OperationDefinition, ...]:
        """Return executable definitions addressable by authorization policy.

        This is a filter, not a score: lexical ordering is solely to make
        serialized questions and training records reproducible.
        """
        return tuple(
            definition
            for definition in self._definitions
            if definition.executable
            and definition.authorization_action
            and definition.authorization_action.casefold() not in _UNAUTHORIZED
        )

    # Short aliases make the selection boundary hard to accidentally bypass.
    def selectable(self) -> tuple[OperationDefinition, ...]:
        return self.selection_options()

    def options(self) -> tuple[OperationDefinition, ...]:
        return self.selection_options()

    def validate(self) -> None:
        """Revalidate the immutable contents (useful at integration seams)."""
        for definition in self._definitions:
            definition.validate()


ToolDefinition = OperationDefinition
ToolRegistry = LayaToolRegistry


def validate_registry(definitions: Iterable[OperationDefinition]) -> LayaToolRegistry:
    """Build and validate a registry in one explicit, side-effect-free call."""
    return LayaToolRegistry(definitions)


__all__ = [
    "LayaToolRegistry",
    "LayaToolRegistryError",
    "OperationDefinition",
    "ToolDefinition",
    "ToolRegistry",
    "validate_registry",
]
