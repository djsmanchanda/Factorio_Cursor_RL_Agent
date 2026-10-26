# Path: tests/test_mining_geometry.py | Purpose: Preserve shared drill coordinates, ordering, legacy exports, and training import isolation.

import subprocess
import sys
from pathlib import Path

import pytest

from planners.mining_geometry import mining_drill_positions, paired_mining_drill_positions


@pytest.mark.parametrize(
    ("origin", "machine_count", "expected"),
    [
        ((0, 0), 3, [
            (1.5, -1.5), (4.5, -1.5), (7.5, -1.5),
            (1.5, 2.5), (4.5, 2.5), (7.5, 2.5),
        ]),
        ((10, -20), 1, [(11.5, -21.5), (11.5, -17.5)]),
        ((-8.5, 12.25), 2, [
            (-7.0, 10.75), (-4.0, 10.75), (-7.0, 14.75), (-4.0, 14.75),
        ]),
        ((12, 6), 0, []),
        ((12, 6), -2, []),
    ],
)
def test_drill_positions_preserve_coordinates_and_upper_then_lower_order(
    origin, machine_count, expected,
):
    assert paired_mining_drill_positions(origin, machine_count) == expected
    assert mining_drill_positions(origin, machine_count) == expected[:len(expected) // 2]


def test_extraction_keeps_the_legacy_geometry_exports():
    from orchestrator import stage_extraction

    assert stage_extraction.mining_drill_positions is mining_drill_positions
    assert stage_extraction.paired_mining_drill_positions is paired_mining_drill_positions


@pytest.mark.integration
def test_training_lines_import_without_the_extraction_runtime():
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
import planners.mining_geometry
assert not any(name == "orchestrator" or name.startswith("orchestrator.")
               for name in sys.modules)
import training.laya_lines
assert "orchestrator.stage_extraction" not in sys.modules
"""],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
