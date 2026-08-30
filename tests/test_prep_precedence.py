# Path: tests/test_prep_precedence.py
# Purpose: Prove production prep fills its mall cells to the baseline count instead of being overridden by promotion on the second pass.

from __future__ import annotations

import inspect
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator import autonomous_builder  # noqa: E402
from orchestrator.baseline_production import BASELINE_MACHINES  # noqa: E402
from orchestrator.intermediate_scaling import (  # noqa: E402
    PROMOTED_LINE_PHASES,
    promoted_line_machine_count,
)

_SOURCE = inspect.getsource(autonomous_builder)


def test_ensure_produced_can_be_told_not_to_promote() -> None:
    signature = inspect.signature(autonomous_builder.ensure_produced)

    assert signature.parameters["allow_promotion"].default is True
    assert signature.parameters["minimum_machines"].default == 1


def test_prep_asks_for_mall_cells_and_forbids_promotion() -> None:
    """The live fault: prep asked for 2 copper-cable machines, and promotion
    turned the second pass into a 6-machine dedicated line instead."""
    prep = inspect.getsource(autonomous_builder._prep_intermediate)
    call = prep[:prep.index(")", prep.index("ensure_produced("))]

    assert "minimum_machines=wanted" in call
    assert "allow_promotion=False" in call


def test_promotion_is_gated_on_the_flag() -> None:
    gate = re.search(r"promote_to_line = ([^\n]+)", _SOURCE).group(1)

    assert gate.startswith("allow_promotion and")


def test_a_prep_sized_cell_is_far_below_the_promotion_floor() -> None:
    """Prep counts and promotion phases are different scales on purpose -- had
    they overlapped, this collision would not have mattered."""
    assert max(BASELINE_MACHINES.values()) < PROMOTED_LINE_PHASES[0]


def test_promotion_still_fires_once_prep_is_not_driving() -> None:
    """Suppressing promotion during prep must not disable it afterwards."""
    assert promoted_line_machine_count(
        "copper-cable", 0.0, BASELINE_MACHINES["copper-cable"], saturated=True,
    ) == PROMOTED_LINE_PHASES[0]
