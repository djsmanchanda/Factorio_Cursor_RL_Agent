# Path: tests/test_promoted_line_sources.py
# Purpose: Prove a promoted belt-fed line resolves a real producing source per ingredient instead of accepting a stocked buffer a belt cannot route from.

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from orchestrator.autonomous_builder import may_consume_stocked_inputs  # noqa: E402


def test_a_promoted_line_will_not_settle_for_a_stocked_buffer() -> None:
    """The run-ender: gears promoted to a shared line, iron-plate was satisfied
    from 13 stocked plates, so no source position was recorded and
    build_conversion_stage had nothing to route a belt from."""
    assert not may_consume_stocked_inputs(
        upgrade_bootstrap=False, promote_to_line=True,
    )


def test_a_mall_cell_may_consume_stock() -> None:
    """A requester-fed cell really is supplied by whatever sits in a chest;
    demanding a live upstream line first deadlocks bootstrap items."""
    assert may_consume_stocked_inputs(
        upgrade_bootstrap=False, promote_to_line=False,
    )


def test_the_upgrade_bootstrap_path_still_resolves_real_stages() -> None:
    assert not may_consume_stocked_inputs(
        upgrade_bootstrap=True, promote_to_line=False,
    )


@pytest.mark.parametrize("promote", [True, False])
def test_upgrade_bootstrap_overrides_either_way(promote: bool) -> None:
    assert not may_consume_stocked_inputs(
        upgrade_bootstrap=True, promote_to_line=promote,
    )


def test_only_the_plain_mall_path_takes_the_shortcut() -> None:
    """Exactly one of the four combinations may skip source resolution."""
    allowed = [
        (bootstrap, promote)
        for bootstrap in (False, True)
        for promote in (False, True)
        if may_consume_stocked_inputs(
            upgrade_bootstrap=bootstrap, promote_to_line=promote,
        )
    ]

    assert allowed == [(False, False)]
