# Path: tests/test_electronics_bundle_preflight.py
# Purpose: Regression cover for the two live-build defects M6 preflight found in
# the REAL composed electronics bundle: an unpowered far drill in the coal mining
# row, and route ghosts outside every roboport's construction radius.

from __future__ import annotations

from planners.plan_validation import actions
from planners.preflight import preflight
from planners.resource_layouts import generate_coal_mine


def test_real_electronics_bundle_passes_preflight(processing_bundle):
    """The whole point of M6: this bundle is what goes to a live game."""
    result = preflight(processing_bundle)
    assert result["ok"], "\n".join(
        f"[{f['check']}] {f['detail']}" for f in result["failures"]
    )
    assert result["skipped"] == []


def test_coal_row_pole_pitch_scales_with_row_width():
    """A wider row must emit more poles, not the same single anchor pole."""
    narrow = generate_coal_mine([(20.5, 0.5)], 2.5, 40.5)
    wide = generate_coal_mine(
        [(20.5, 0.5), (23.5, 0.5), (26.5, 0.5), (29.5, 0.5), (32.5, 0.5)], 2.5, 40.5,
    )
    narrow_count = sum(
        1 for action in actions(narrow) if action["entity"] == "medium-electric-pole"
    )
    wide_count = sum(
        1 for action in actions(wide) if action["entity"] == "medium-electric-pole"
    )
    assert narrow_count == 1
    assert wide_count > narrow_count
