# Path: orchestrator/controller_budget.py
# Purpose: Bound deterministic control passes and every submitted plan.

from __future__ import annotations

from dataclasses import dataclass


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class ControlBudget:
    max_passes: int
    max_plans: int
    max_remediations: int
    max_waits: int
    max_diagnoses: int
    passes: int = 0
    plans: int = 0
    remediations: int = 0
    waits: int = 0
    diagnoses: int = 0
    progress_credits: int = 0

    def begin_pass(self) -> None:
        if self.passes >= self.max_passes:
            raise BudgetExhausted(f"control pass budget exhausted at {self.passes} passes")
        self.passes += 1

    def credit_progress_pass(self) -> None:
        """Do not spend the control-loop limit on an observably productive pass."""
        if self.passes > 0:
            self.passes -= 1
            self.progress_credits += 1

    def consume_plan(self, name: str) -> None:
        if self.plans >= self.max_plans:
            raise BudgetExhausted(
                f"plan submission budget exhausted before {name}"
            )
        self.plans += 1

    def consume_remediation(self, name: str) -> None:
        if self.remediations >= self.max_remediations:
            raise BudgetExhausted(f"remediation budget exhausted before {name}")
        self.remediations += 1

    def consume_wait(self, name: str) -> None:
        if self.waits >= self.max_waits:
            raise BudgetExhausted(f"wait budget exhausted before {name}")
        self.waits += 1

    def consume_diagnosis(self, name: str) -> None:
        if self.diagnoses >= self.max_diagnoses:
            raise BudgetExhausted(f"diagnosis budget exhausted before {name}")
        self.diagnoses += 1


_ACTIVE: ControlBudget | None = None


def begin_run_budget(max_passes: int, *, plans_per_pass: int = 8) -> ControlBudget:
    global _ACTIVE
    _ACTIVE = ControlBudget(
        max_passes=max_passes,
        max_plans=max_passes * plans_per_pass,
        max_remediations=max_passes * 8,
        max_waits=max_passes * 4,
        max_diagnoses=max_passes * 8,
    )
    return _ACTIVE


def end_run_budget() -> None:
    global _ACTIVE
    _ACTIVE = None


def consume_plan_submission(name: str) -> None:
    if _ACTIVE is not None:
        _ACTIVE.consume_plan(name)


def consume_remediation(name: str) -> None:
    if _ACTIVE is not None:
        _ACTIVE.consume_remediation(name)


def consume_wait(name: str) -> None:
    if _ACTIVE is not None:
        _ACTIVE.consume_wait(name)


def consume_diagnosis(name: str) -> None:
    if _ACTIVE is not None:
        _ACTIVE.consume_diagnosis(name)
