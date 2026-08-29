# Path: orchestrator/work_state.py
# Purpose: Define the one typed lifecycle signal shared by deferred and terminal deterministic work.

from __future__ import annotations

from collections.abc import Mapping


WORK_STATES = frozenset({
    "planned", "constructing", "coverage_wait", "power_wait",
    "supply_wait", "producing", "retiring", "retired", "failed",
})
WORK_CLASSIFICATIONS = frozenset({"bug", "intended_difficulty"})


class WorkStateSignal(RuntimeError):
    """Machine-readable lifecycle context for a wait or fail-closed stop."""

    def __init__(
        self, message: str, *, code: str, classification: str, state: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        if classification not in WORK_CLASSIFICATIONS:
            raise ValueError(f"Unknown work classification {classification!r}")
        if state not in WORK_STATES:
            raise ValueError(f"Unknown work state {state!r}")
        super().__init__(message)
        self.code = code
        self.classification = classification
        self.state = state
        self.details = dict(details or {})
