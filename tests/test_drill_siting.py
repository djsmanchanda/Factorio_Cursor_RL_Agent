# Path: tests/test_drill_siting.py
# Purpose: Prove drill siting clears the MINING AREA of foreign ore, so a drill on a patch border cannot mine two items and jam its output.

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from orchestrator import live_base  # noqa: E402
from planners.recipe_data import DRILL_MINING_AREAS, drill_mining_reach  # noqa: E402

_AREAS = re.compile(r"area=\{\{([-\d.]+),([-\d.]+)\},\{([-\d.]+),([-\d.]+)\}\}")


class _FakeRcon:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.command_text = ""

    def command(self, text: str) -> str:
        self.command_text = text
        return self.reply


def test_a_drill_reaching_a_second_ore_is_rejected() -> None:
    """The hazard: a drill sited entirely on iron still reaches the copper
    beside it, mines both, and jams its output with an item nothing downstream
    accepts."""
    conflicts = live_base.drill_siting_conflicts(
        _FakeRcon("9,copper-ore"), "nauvis", "iron-ore", [(11.5, 18.5)],
    )

    assert len(conflicts) == 1
    centre, reason = conflicts[0]
    assert centre == (11.5, 18.5)
    assert "copper-ore" in reason and "jam" in reason


def test_clean_ore_under_and_around_the_drill_passes() -> None:
    assert live_base.drill_siting_conflicts(
        _FakeRcon("9,;9,"), "nauvis", "iron-ore", [(11.5, 18.5), (14.5, 18.5)],
    ) == []


def test_an_empty_footprint_is_still_rejected() -> None:
    """The original fault must survive the stronger check."""
    conflicts = live_base.drill_siting_conflicts(
        _FakeRcon("0,"), "nauvis", "iron-ore", [(11.5, 18.5)],
    )

    assert len(conflicts) == 1
    assert "no iron-ore" in conflicts[0][1]


def test_the_purity_scan_covers_the_mining_area_not_the_footprint() -> None:
    """5x5 mined vs 3x3 occupied -- scanning only the footprint is what let a
    border drill through."""
    client = _FakeRcon("9,")
    live_base.drill_siting_conflicts(client, "nauvis", "iron-ore", [(10.5, 10.5)])
    boxes = {tuple(float(v) for v in match) for match in _AREAS.findall(client.command_text)}

    assert (9.0, 9.0, 12.0, 12.0) in boxes, "3x3 footprint scanned for the target"
    assert (8.0, 8.0, 13.0, 13.0) in boxes, "5x5 mining area scanned for foreign ore"


def test_the_big_drill_scans_its_much_wider_reach() -> None:
    client = _FakeRcon("9,")
    live_base.drill_siting_conflicts(
        client, "nauvis", "iron-ore", [(10.5, 10.5)], drill="big-mining-drill",
    )
    boxes = {tuple(float(v) for v in match) for match in _AREAS.findall(client.command_text)}

    assert (4.0, 4.0, 17.0, 17.0) in boxes, "13x13 mining area"


def test_reach_is_half_the_mining_area() -> None:
    assert DRILL_MINING_AREAS["electric-mining-drill"] == 5
    assert DRILL_MINING_AREAS["big-mining-drill"] == 13
    assert drill_mining_reach("electric-mining-drill") == 2.5
    assert drill_mining_reach("big-mining-drill") == 6.5


def test_an_unmeasured_drill_fails_loudly_rather_than_guessing() -> None:
    with pytest.raises(ValueError, match="DRILL_MINING_AREAS"):
        drill_mining_reach("burner-mining-drill")


def test_the_boolean_gate_enforces_both_faults() -> None:
    """Every siting search calls this predicate, so the mixed-ore rule cannot
    be forgotten by a future caller."""
    assert live_base.drill_footprints_have_resource(
        _FakeRcon("9,"), "nauvis", "iron-ore", [(11.5, 18.5)],
    )
    assert not live_base.drill_footprints_have_resource(
        _FakeRcon("9,copper-ore"), "nauvis", "iron-ore", [(11.5, 18.5)],
    )
    assert not live_base.drill_footprints_have_resource(
        _FakeRcon("0,"), "nauvis", "iron-ore", [(11.5, 18.5)],
    )


def test_one_query_covers_every_centre() -> None:
    """Siting probes many candidate rows; one round-trip per row, not per drill."""
    client = _FakeRcon(";".join(["9,"] * 6))
    centres = [(10.5 + 3 * i, 10.5) for i in range(6)]

    assert live_base.drill_siting_conflicts(client, "nauvis", "iron-ore", centres) == []
    assert client.command_text.count("rcon.print") == 1


def test_no_centres_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="at least one drill centre"):
        live_base.drill_siting_conflicts(_FakeRcon(""), "nauvis", "iron-ore", [])
